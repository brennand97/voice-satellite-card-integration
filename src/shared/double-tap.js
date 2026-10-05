/**
 * DoubleTapHandler
 *
 * Detects double-taps on the document to cancel active interactions.
 * Also supports Escape key and includes touch/click deduplication.
 */

import { State, INTERACTING_STATES, BlurReason, Timing } from '../constants.js';
import { clearNotificationUI } from '../shared/satellite-notification.js';
import { sendAck } from '../shared/notification-comms.js';
import { getSwitchState } from '../shared/satellite-state.js';
import * as kiosk from '../kiosk/index.js';

/**
 * Attach a double-tap/double-click handler to an element.
 * Includes touch/click deduplication to avoid phantom triggers.
 */
export function attachDoubleTap(el, callback) {
  let lastTap = 0;
  let lastTouchTime = 0;
  const handler = (e) => {
    const now = Date.now();
    if (e.type === 'touchstart') lastTouchTime = now;
    if (e.type === 'click' && (now - lastTouchTime) < 400) return;
    if (now - lastTap < Timing.DOUBLE_TAP_THRESHOLD && now - lastTap > 0) {
      e.preventDefault();
      e.stopPropagation();
      callback();
    }
    lastTap = now;
  };
  el.addEventListener('touchstart', handler, { passive: false });
  el.addEventListener('click', handler);
}

export class DoubleTapHandler {
  constructor(card) {
    this._card = card;
    this._log = card.logger;

    this._lastTapTime = 0;
    this._lastTouchTime = 0;
    this._handler = null;
    this._keyHandler = null;
  }

  setup() {
    // Idempotency guard - prevent duplicate listeners on repeated calls
    if (this._handler) return;

    this._handler = (e) => {
      const state = this._getInteractionState();
      if (!state) return;

      const now = Date.now();

      // Touch/click deduplication - skip synthetic click that follows a recent touch
      if (e.type === 'touchstart') this._lastTouchTime = now;
      if (e.type === 'click' && (now - this._lastTouchTime) < 400) return;
      const timeSinceLastTap = now - this._lastTapTime;
      this._lastTapTime = now;

      if (timeSinceLastTap < Timing.DOUBLE_TAP_THRESHOLD && timeSinceLastTap > 0) {
        e.preventDefault();
        this._cancel(state.isTimerAlert, state.isNotification, state.isShow);
      }
    };

    document.addEventListener('touchstart', this._handler, { passive: false });
    document.addEventListener('click', this._handler);

    // Escape key cancels the same way as double-tap
    this._keyHandler = (e) => {
      if (e.key !== 'Escape') return;

      const state = this._getInteractionState();
      if (!state) return;

      e.preventDefault();
      this._cancel(state.isTimerAlert, state.isNotification);
    };
    document.addEventListener('keydown', this._keyHandler);
  }

  teardown() {
    if (this._handler) {
      document.removeEventListener('touchstart', this._handler);
      document.removeEventListener('click', this._handler);
      this._handler = null;
    }
    if (this._keyHandler) {
      document.removeEventListener('keydown', this._keyHandler);
      this._keyHandler = null;
    }
  }

  _getInteractionState() {
    const isActive = INTERACTING_STATES.includes(this._card.currentState) || this._card.tts.isPlaying;
    const isImageLinger = !!this._card._imageLingerTimeout || this._card.ui.isLightboxVisible() || this._card.ui.hasVisibleMedia();
    const isTimerAlert = this._card.timer.alertActive;
    const isShow = !!this._card.show?.active;
    const isNotification = this._card.announcement.playing
      || this._card.askQuestion.playing
      || this._card.startConversation.playing
      || this._card.announcement.clearTimeoutId
      || this._card.startConversation.clearTimeoutId;

    if (!isActive && !isImageLinger && !isTimerAlert && !isNotification && !isShow) return null;
    return { isTimerAlert, isNotification, isShow };
  }

  _cancel(isTimerAlert, isNotification, isShow) {
    if (isTimerAlert) {
      this._log.log('ui', 'Cancel detected - dismissing timer alert');
      this._card.timer.dismissAlert();
      return;
    }

    if (isShow) {
      this._log.log('ui', 'Cancel detected - dismissing show');
      this._card.show.dismiss();
      return;
    }

    if (isNotification) {
      this._log.log('ui', 'Cancel detected - dismissing notification');
      for (const mgr of [this._card.announcement, this._card.askQuestion, this._card.startConversation]) {
        if (!mgr.playing && !mgr.clearTimeoutId) continue;
        if (mgr.currentAnnounceId) {
          sendAck(this._card, mgr.currentAnnounceId, 'double-tap');
        }
        if (mgr.currentAudio) {
          mgr.currentAudio.onended = null;
          mgr.currentAudio.onerror = null;
          mgr.currentAudio.pause();
          mgr.currentAudio.src = '';
          mgr.currentAudio = null;
        }
        mgr.playing = false;
        mgr.currentAnnounceId = null;
        mgr.queued = null;
        clearNotificationUI(mgr);
      }
      // Release server-side _question_event if ask_question was playing
      this._card.askQuestion.cancel();
      this._card.chat.clear();
      this._card.ui.clearNotificationStatusOverride();
      // Resume any media playback paused when the notification started.
      this._card.mediaPlayer.resumeAfterInterrupt();

      if (getSwitchState(this._card.hass, this._card.config.satellite_entity, 'wake_sound') !== false) {
        this._card.tts.playChime('done');
      }

      this._card.pipeline.restart(0);
      return;
    }

    // Cancel image linger timeout if active
    if (this._card._imageLingerTimeout) {
      clearTimeout(this._card._imageLingerTimeout);
      this._card._imageLingerTimeout = null;
    }
    // A media panel lingering after its response: the turn is already over
    // (state IDLE, TTS done) and its end-of-turn cleanup was parked for the
    // panel's lifetime. Run that cleanup, exactly as the stop word and the
    // linger timer do, so everything it owns is released: the stop word,
    // paused media, the external screensaver keepalive and the kiosk
    // companion's "interaction running" hold. Tearing the panel down by
    // hand here used to skip that release, and Kiosk Satellite then kept
    // refusing its screensaver and hiding its player until the page
    // reloaded.
    if (this._card._mediaLingerDismiss) {
      this._log.log('ui', 'Cancel detected - dismissing media panel');
      const dismiss = this._card._mediaLingerDismiss;
      this._card._mediaLingerDismiss = null;
      dismiss();
      if (getSwitchState(this._card.hass, this._card.config.satellite_entity, 'wake_sound') !== false) {
        this._card.tts.playChime('done');
      }
      return;
    }

    this._log.log('ui', 'Cancel detected - cancelling interaction');

    this._card.tts.stop();

    // Clean up ask_question STT mode (timers, server release, ANNOUNCEMENT blur)
    this._card.askQuestion.cancel();

    this._card.pipeline.clearContinueState();
    this._card.setState(State.IDLE);
    this._card.chat.clear();
    this._card.ui.hideBlurOverlay(BlurReason.PIPELINE);
    this._card.ui.updateForState(State.IDLE, this._card.pipeline.serviceUnavailable, false);
    // setState only releases the screensaver holds when it leaves an
    // interacting state. A cancel that lands after the state already went
    // idle (TTS still speaking, a lingering response) would keep them, so
    // release explicitly; both calls are no-ops when nothing is held.
    this._card.screensaver.stopExternalKeepalive();
    kiosk.releaseScreensaver('voice');

    if (getSwitchState(this._card.hass, this._card.config.satellite_entity, 'wake_sound') !== false) {
      this._card.tts.playChime('done');
    }

    this._card.pipeline.restart(0);
  }
}
