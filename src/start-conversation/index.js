/**
 * StartConversationManager
 *
 * Handles start_conversation announcements: plays the prompt,
 * then clears the UI and enters full STT listening mode
 * so the user can begin a voice interaction.
 */

import {
  initNotificationState,
  dequeueNotification,
  playNotification,
  releaseNotificationInteraction,
} from '../shared/satellite-notification.js';
import { sendAck } from '../shared/notification-comms.js';
import { BlurReason } from '../constants.js';
import { performFollowupHandoff } from '../session/events.js';

const LOG = 'start-conversation';

export class StartConversationManager {
  constructor(card) {
    this._card = card;
    this._log = card.logger;
    initNotificationState(this);
  }

  get card() { return this._card; }
  get log() { return this._log; }

  playQueued() {
    const ann = dequeueNotification(this);
    if (!ann) return;
    this._log.log(LOG, `Playing queued start_conversation #${ann.id}`);
    this._play(ann);
  }
  _play(ann) {
    playNotification(this, ann, (a) => this._onComplete(a), LOG);
  }

  _onComplete(ann) {
    this.currentAudio = null;
    this._log.log(LOG, `Prompt #${ann.id} playback complete`);

    // The prompt itself can enable stop-only mode so the user can interrupt
    // the spoken announcement. Once the prompt finishes and we hand off into
    // an interactive pipeline session, normal wake keywords must be restored.
    // Otherwise the session enters STT/TTS with the wake-word engine still in
    // stop-only mode, and the final TTS cleanup has no wake keywords left to
    // restore.
    this._card.wakeWord?.disableStopModel();

    sendAck(this._card, ann.id, LOG);

    // Partial notification cleanup: clear only announcement-specific state and
    // blur. Do not restore/hide the speaking bar here; the pipeline restart
    // immediately transitions into STT/listening and a bar hide/show cycle can
    // cause a visible chat bubble reflow flicker in start_conversation.
    if (this.clearTimeoutId) {
      clearTimeout(this.clearTimeoutId);
      this.clearTimeoutId = null;
    }
    this._card.ui.setAnnouncementMode(false);
    this._card.ui.clearAnnouncementBubbles();
    this._card.ui.hideBlurOverlay(BlurReason.ANNOUNCEMENT);
    // onNotificationDismiss() resets bar state on the full card, causing
    // flicker since start_conversation transitions into a conversation.
    // Clear the mini card's notification status override explicitly instead.
    this._card.ui.clearNotificationStatusOverride?.();

    this._card.ui.showBlurOverlay(BlurReason.PIPELINE);

    const { pipeline } = this._card;
    if (!pipeline) {
      this.playing = false;
      releaseNotificationInteraction(this);
      return;
    }

    // Apply the same delay/chime handoff as the continue-conversation
    // branch in onTTSComplete — the prompt TTS just played and the next
    // step is STT capture, so the speaker-drain / AEC-failure window
    // exists here too.  Without this, the user's first response after a
    // start_conversation prompt skipped the protective pause that every
    // subsequent turn used.
    performFollowupHandoff(this._card, () => {
      this.playing = false;
      // A running conversation releases this hold with the voice hold at
      // its end. Keep it across init, which can arrive before run-start.
      // Muted or failed starts have no run to own the release.
      Promise.resolve(pipeline.restartContinue(null, {
        extra_system_prompt: ann.extra_system_prompt || null,
      })).finally(() => {
        if (this.currentAnnounceId === ann.id && !pipeline.binaryHandlerId) {
          releaseNotificationInteraction(this);
        }
      });
    }, { logTag: 'start_conversation' });
  }
}
