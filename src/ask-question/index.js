/**
 * AskQuestionManager
 *
 * Handles ask_question announcements: plays the question prompt,
 * enters STT-only mode to capture the user's spoken answer,
 * sends it to the integration, and provides audio/visual match feedback.
 */

import {
  initNotificationState,
  dequeueNotification,
  playNotification,
  clearNotificationUI,
  releaseNotificationInteraction,
  hasQueuedNotification,
  playQueuedNotifications,
} from '../shared/satellite-notification.js';
import { sendAck } from '../shared/notification-comms.js';
import { BlurReason, Timing } from '../constants.js';
import { performFollowupHandoff } from '../session/events.js';
import { sendAnswer } from './comms.js';

const LOG = 'ask-question';

export class AskQuestionManager {
  constructor(card) {
    this._card = card;
    this._log = card.logger;
    this._sttSafetyTimeout = null;
    this._cleanupTimeout = null;
    this._answerSent = false;
    initNotificationState(this);
  }

  get card() { return this._card; }
  get log() { return this._log; }

  /**
   * Cancel an in-progress ask_question flow (playback or STT).
   * Clears timers, sends empty answer to release the server,
   * and hides the ANNOUNCEMENT blur that playNotification added.
   */
  cancel() {
    // Cancel an in-flight follow-up handoff (chime + delay window) if
    // we're mid-handoff when the user double-taps to cancel.  The mute
    // the helper applied needs to come back off so the next interaction
    // isn't silently muted.
    if (this._card._followupDelayTimer) {
      clearTimeout(this._card._followupDelayTimer);
      this._card._followupDelayTimer = null;
      try { this._card.audio.setMicTracksMuted(false); } catch (_) { /* ignore */ }
    }
    if (this._sttSafetyTimeout) {
      clearTimeout(this._sttSafetyTimeout);
      this._sttSafetyTimeout = null;
    }
    if (this._cleanupTimeout) {
      clearTimeout(this._cleanupTimeout);
      this._cleanupTimeout = null;
    }

    // Release server's _question_event if we haven't sent an answer yet
    if (this.currentAnnounceId && !this._answerSent) {
      this._answerSent = true;
      sendAnswer(this._card, this.currentAnnounceId, '', 'double-tap');
    }

    this._card.ui.hideBlurOverlay(BlurReason.ANNOUNCEMENT);
    releaseNotificationInteraction(this);
    this.playing = false;
  }

  playQueued() {
    const ann = dequeueNotification(this);
    if (!ann) return;
    this._log.log(LOG, `Playing queued ask_question #${ann.id}`);
    this._play(ann);
  }
  _play(ann) {
    playNotification(this, ann, (a) => this._onComplete(a), LOG);
  }

  _onComplete(ann) {
    this.currentAudio = null;
    this._log.log(LOG, `Question #${ann.id} playback complete`);

    // ACK immediately on playback complete - signals the integration that
    // the prompt was played. The integration then waits for question_answered.
    sendAck(this._card, ann.id, LOG);
    this._enterSttMode(ann);
  }

  /**
   * Enter STT-only mode, capture answer, submit to integration.
   */
  _enterSttMode(ann) {
    this._log.log(LOG, 'Entering STT-only mode');

    // Switch from passive announcement centering to interactive mode
    this._card.ui.setAnnouncementMode(false);
    this._card.ui.showBlurOverlay(BlurReason.PIPELINE);

    const { pipeline } = this._card;
    if (!pipeline) return;

    const announceId = ann.id;

    // Start reactive bar on mic for STT phase. updateForState won't
    // run while playing is true, so we bypass it via startReactive().
    this._card.ui.startReactive();

    // Track whether an answer was submitted so the cleanup timeout
    // can release the server if STT never produced a result.
    this._answerSent = false;

    // Hand off to the unified follow-up window: optional configured
    // delay (lets the prompt's TTS speaker tail drain before we start
    // STT) followed by the wake chime as the "speak now" cue.  forceChime
    // is true here because the chime is functional for ask_question, not
    // optional like it is for continue-conversation turns.  Without the
    // delay around the chime, the mic captures it as speech and STT
    // returns stt-no-text-recognized.
    performFollowupHandoff(this._card, () => {
      pipeline.restartContinue(null, {
        end_stage: 'stt',
        onSttEnd: (text) => {
          this._log.log(LOG, `STT result: "${text}"`);
          this._answerSent = true;
          this._processAnswer(announceId, text);
        },
      });

      // Safety: if STT never produces a result (pipeline timeout, run-end
      // without error, etc.), send an empty answer to release the server
      // and clean up after a generous window.
      this._sttSafetyTimeout = setTimeout(() => {
        if (!this._answerSent) {
          this._log.log(LOG, `No STT result for #${announceId} - sending empty answer to release server`);
          this._answerSent = true;
          this._processAnswer(announceId, '');
        }
      }, Timing.ASK_QUESTION_STT_SAFETY);
    }, { forceChime: true, logTag: 'ask_question' });
  }

  /**
   * Send answer to integration and show match feedback.
   * Mirrors the original monolithic implementation: a safety cleanup timeout
   * runs in parallel with the sendAnswer promise. Whichever completes first
   * triggers cleanup; the other is a no-op via the `cleaned` guard.
   */
  _processAnswer(announceId, text) {
    // Clear pending timers - we have a result (or explicit empty)
    if (this._sttSafetyTimeout) {
      clearTimeout(this._sttSafetyTimeout);
      this._sttSafetyTimeout = null;
    }

    const { pipeline } = this._card;
    let cleaned = false;
    let matchedResult = null;

    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      this._cleanupTimeout = null;
      if (matchedResult !== null && !matchedResult) {
        this._card.ui.clearServiceError();
      }
      clearNotificationUI(this);
      this._card.chat.clear();
      this._card.ui.hideBlurOverlay(BlurReason.PIPELINE);
      this.playing = false;
      if (!hasQueuedNotification(this._card)) {
        // Resume any media playback paused at the start of the question.
        // Skipped when another notification is queued - its own interrupt()
        // will keep playback paused.
        this._card.mediaPlayer.resumeAfterInterrupt();
        pipeline.restart(0);
      } else {
        // Drain every manager, not just this one. An announce fired right
        // after the answer (the common "ask, then confirm" script shape)
        // arrives while the state is still STT and is queued on the
        // announcement manager; the normal run-end drain is skipped for
        // ask_question (this cleanup owns it), so it must happen here or
        // the announcement waits for an unrelated future turn (#170).
        playQueuedNotifications(this._card);
      }
    };

    // Safety timeout - if sendAnswer takes too long, clean up anyway
    this._cleanupTimeout = setTimeout(cleanup, Timing.ASK_QUESTION_CLEANUP);

    sendAnswer(this._card, announceId, text, LOG).then((result) => {
      const matched = result?.matched;
      matchedResult = matched;

      this._card.tts.playChime(matched ? 'done' : 'error');

      if (!matched) {
        this._card.toast?.show({
          id: 'ask-question.unmatched',
          severity: 'warn',
          category: 'Question',
          description: 'Your answer did not match any of the accepted options.',
        });
      }
    }).catch((e) => {
      this._card.logger.error('ask-question', `sendAnswer failed: ${e?.message || e}`);
      this._card.tts.playChime('error');
      this._card.toast?.show({
        id: 'ask-question.failed',
        severity: 'warn',
        category: 'Question',
        description: `Could not deliver your answer to Home Assistant. ${e?.message || ''}`.trim(),
      });
    });
  }

}
