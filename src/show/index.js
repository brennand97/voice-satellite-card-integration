/**
 * ShowManager
 *
 * Handles the `voice_satellite.show` service: drives the satellite's
 * Assist pipeline with a text prompt as if the user had spoken it, then
 * keeps the response on screen until dismissed (stop word, double tap,
 * or the configured duration timer).
 *
 * Event flow:
 *   1. Backend pushes `show-trigger` with `{prompt, silent, duration, pipeline_id}`.
 *   2. ShowManager plays the wake chime, marks `active=true`, and triggers
 *      `pipeline.start({ start_stage: 'intent', intent_input: prompt, ... })`.
 *   3. Pipeline emits the normal events (intent-start, intent-progress with
 *      tool calls, intent-end, tts-start, tts-end, run-end). The card renders
 *      assistant bubbles and rich media via the existing handlers — no new
 *      rendering code in this manager.
 *   4. At run-end (silent) or after TTS playback (non-silent), the cleanup
 *      paths see `session.show.active === true` and call `enterSticky()`
 *      instead of clearing chat / restarting the pipeline.
 *   5. On dismissal, ShowManager runs the deferred cleanup and restarts
 *      the pipeline so wake-word listening resumes.
 */

import { BlurReason, INTERACTING_STATES } from '../constants.js';
import { getSwitchState } from '../shared/satellite-state.js';

const LOG = 'show';

/**
 * Brief delay between starting the wake chime and submitting the prompt
 * so the chime is audible before the LLM response begins.
 */
const CHIME_LEAD_MS = 250;

export class ShowManager {
  constructor(session) {
    this._session = session;
    this._log = session.logger;

    this._active = false;
    this._currentShow = null;
    this._pendingShow = null;
    this._stickyTimer = null;
    this._lastTriggerId = 0;
  }

  get session() { return this._session; }
  get card() { return this._session; }
  get log() { return this._log; }
  get active() { return this._active; }
  get currentAnnounceId() { return this._currentShow?.id ?? null; }
  // Compat shims with the other notification managers so cancel/stop-word
  // dispatchers can iterate uniformly. ShowManager doesn't use the shared
  // playNotification flow so most of these stay false / no-op.
  get playing() { return this._active; }
  set playing(_v) { /* state owned by _active; setter is a no-op */ }
  get clearTimeoutId() { return this._stickyTimer; }
  set clearTimeoutId(_v) { /* state owned by _stickyTimer; setter is a no-op */ }
  get queued() { return this._pendingShow; }
  set queued(v) { this._pendingShow = v; }
  get currentAudio() { return null; }
  set currentAudio(_v) { /* show audio is owned by tts manager via pipeline */ }

  /**
   * Handle a `show-trigger` satellite event.
   * @param {{id:number, prompt:string, silent:boolean, duration:number,
   *          pipeline_id:string, pipeline_name?:string}} data
   */
  trigger(data) {
    if (!data || !data.id) return;
    if (data.id <= this._lastTriggerId) {
      this._log.log(LOG, `Ignoring duplicate trigger #${data.id}`);
      return;
    }
    this._lastTriggerId = data.id;

    const replacingShow = this._active;
    if (replacingShow) {
      this._log.log(LOG, `Replacing active show #${this._currentShow?.id} with #${data.id}`);
      this._dismissNow({ skipPipelineRestart: true, skipDoneChime: true });
    }

    const cardState = this._session.currentState;
    const pipelineBusy = INTERACTING_STATES.includes(cardState);
    // Other notification managers play their own audio outside the TTS
    // manager, so `tts.isPlaying` doesn't see them. Check explicitly.
    const notifPlaying = this._session.announcement.playing
      || this._session.askQuestion.playing
      || this._session.startConversation.playing;
    // Dismissing the old show can leave its persistent external pipeline in
    // follow-up STT. That stale presentation state must not queue its explicit
    // replacement forever; pipeline.start will revoke the old subscription.
    if ((!replacingShow && (pipelineBusy || this._session.tts.isPlaying)) || notifPlaying) {
      this._log.log(
        LOG,
        `Show #${data.id} queued — busy (state=${cardState}, tts=${this._session.tts.isPlaying}, notif=${notifPlaying})`,
      );
      this._pendingShow = data;
      return;
    }

    this._startShow(data);
  }

  /**
   * Called by other notification flows after their TTS completes so a queued
   * show drains. Mirrors the playQueued shape on the other managers.
   */
  playQueued() {
    if (!this._pendingShow || this._active) return;
    const data = this._pendingShow;
    this._pendingShow = null;
    this._startShow(data);
  }

  _startShow(data) {
    this._log.log(
      LOG,
      `Starting show #${data.id} (silent=${data.silent}, duration=${data.duration}s, pipeline=${data.pipeline_name || data.pipeline_id})`,
    );
    this._active = true;
    this._currentShow = data;

    this._session.ui.showBlurOverlay(BlurReason.PIPELINE);
    this._session.mediaPlayer.interrupt();

    if (getSwitchState(this._session.hass, this._session.config.satellite_entity, 'wake_sound') !== false) {
      this._session.tts.playChime('wake');
    }

    setTimeout(() => {
      // Bail if the show was replaced or dismissed during the chime lead.
      if (!this._active || this._currentShow?.id !== data.id) return;
      this._session.pipeline.start({
        start_stage: 'intent',
        end_stage: data.silent ? 'intent' : 'tts',
        intent_input: data.prompt,
        pipeline_id: data.pipeline_id,
      }).catch((e) => {
        this._log.error(LOG, `Pipeline start failed: ${e?.message || e}`);
        this._dismissNow();
      });
    }, CHIME_LEAD_MS);
  }

  /**
   * Called from onTTSComplete (non-silent shows) or finishRunEnd (silent
   * shows) when this manager is active. Suppresses the normal cleanup
   * (chat clear, blur hide, pipeline restart) and arms dismissal.
   */
  enterSticky() {
    if (!this._active) return;
    this._log.log(LOG, `Show #${this._currentShow.id} entering sticky mode`);

    // Pipeline run is done — pin the bar to the calm "listening" gradient
    // so it stays visible without the misleading processing/speaking
    // animations or mic-driven reactivity.
    this._session.ui.setBarSticky();

    const wakeWord = this._session.wakeWord;
    if (wakeWord && !wakeWord.isStopOnlyMode?.()) {
      wakeWord.enableStopModel(true);
    }

    const seconds = Number(this._currentShow.duration) || 0;
    if (seconds > 0) {
      this._log.log(LOG, `Auto-dismiss in ${seconds}s`);
      if (this._stickyTimer) clearTimeout(this._stickyTimer);
      this._stickyTimer = setTimeout(() => this.dismiss(), seconds * 1000);
    } else {
      this._log.log(LOG, 'Sticky — waiting for stop word or double tap');
    }
  }

  /**
   * Public dismiss entry — used by stop-word handler, double-tap handler,
   * and the duration timer. Pipeline-error path passes skipDoneChime so
   * the error's own UX (toast / error chime) isn't preceded by a "done".
   * @param {{skipDoneChime?: boolean, skipPipelineRestart?: boolean}} [opts]
   */
  dismiss(opts = {}) {
    if (!this._active) return;
    this._dismissNow(opts);
  }

  _dismissNow({ skipDoneChime = false, skipPipelineRestart = false } = {}) {
    const showId = this._currentShow?.id;
    this._log.log(LOG, `Dismissing show #${showId}`);

    this._active = false;
    this._currentShow = null;
    if (this._stickyTimer) {
      clearTimeout(this._stickyTimer);
      this._stickyTimer = null;
    }

    const session = this._session;

    session.wakeWord?.disableStopModel();

    if (session.tts.isPlaying) {
      session.tts.stop();
    }

    session.chat.clear();
    session.ui.hideBlurOverlay(BlurReason.PIPELINE);
    session.ui.updateForState(
      session.currentState,
      session.pipeline.serviceUnavailable,
      false,
    );

    session.mediaPlayer.resumeAfterInterrupt();

    if (
      !skipDoneChime
      && getSwitchState(session.hass, session.config.satellite_entity, 'wake_sound') !== false
    ) {
      session.tts.playChime('done');
    }

    if (!skipPipelineRestart) {
      session.pipeline.restart(0);
    }

    if (this._pendingShow) {
      setTimeout(() => this.playQueued(), 100);
    }
  }
}
