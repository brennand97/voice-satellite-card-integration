/**
 * Provider-neutral frontend lifecycle for persistent External Transport runs.
 * It deliberately separates provider generation from local audio playback.
 */
export const ExternalState = Object.freeze({
  IDLE: 'idle',
  CAPTURING: 'capturing',
  PROCESSING: 'processing',
  PLAYING_AND_CAPTURING: 'playing_and_capturing',
  BARGE_IN: 'barge_in',
  FOLLOWUP_LISTENING: 'followup_listening',
  TERMINATING: 'terminating',
  TERMINATED: 'terminated',
  FAILED: 'failed',
});

export class ExternalSessionController {
  constructor({
    log, playResponseAudio, stopResponseAudio, restoreCaptureVisualization,
    setPresentationState, showInteractionUi, hideInteractionUi, stopPipeline,
    schedule = setTimeout, cancelScheduled = clearTimeout, followupTimeoutMs = 60000,
    terminalUiTimeoutMs = 3000,
  }) {
    Object.assign(this, {
      _log: log, _playResponseAudio: playResponseAudio, _stopResponseAudio: stopResponseAudio,
      _restoreCaptureVisualization: restoreCaptureVisualization,
      _setPresentationState: setPresentationState, _showInteractionUi: showInteractionUi,
      _hideInteractionUi: hideInteractionUi, _stopPipeline: stopPipeline,
      _schedule: schedule, _cancelScheduled: cancelScheduled, _followupTimeoutMs: followupTimeoutMs,
      _terminalUiTimeoutMs: terminalUiTimeoutMs,
    });
    this._state = ExternalState.IDLE;
    this._providerResponseId = null;
    this._providerTurnId = null;
    this._playbackResponseId = null;
    this._playbackTurnId = null;
    this._followupTimer = null;
    this._terminalUiTimer = null;
  }

  get state() { return this._state; }
  get providerResponseId() { return this._providerResponseId; }
  get playbackResponseId() { return this._playbackResponseId; }
  ownsPlayback() { return this._playbackResponseId !== null; }
  isActive() {
    return ![
      ExternalState.IDLE,
      ExternalState.TERMINATING,
      ExternalState.TERMINATED,
      ExternalState.FAILED,
    ].includes(this._state);
  }

  _transition(state) { this._state = state; }
  _clearFollowupTimer() {
    if (this._followupTimer !== null) this._cancelScheduled(this._followupTimer);
    this._followupTimer = null;
  }
  _clearTerminalUiTimer() {
    if (this._terminalUiTimer !== null) this._cancelScheduled(this._terminalUiTimer);
    this._terminalUiTimer = null;
  }
  _armFollowupTimer() {
    this._clearFollowupTimer();
    this._followupTimer = this._schedule(() => {
      this._followupTimer = null;
      this.onExplicitStop('followup_timeout');
    }, this._followupTimeoutMs);
  }
  _matchesPlayback(meta) { return meta?.response_id && meta.response_id === this._playbackResponseId; }
  _matchesProvider(meta) { return meta?.response_id && meta.response_id === this._providerResponseId; }
  _stopPlayback(reason) {
    if (!this._playbackResponseId) return false;
    const responseId = this._playbackResponseId;
    this._playbackResponseId = this._playbackTurnId = null;
    this._stopResponseAudio(responseId, reason);
    return true;
  }
  _restoreCapture() {
    this._restoreCaptureVisualization();
    this._setPresentationState('stt');
    this._showInteractionUi();
  }

  onRunStart() {
    this._clearFollowupTimer();
    this._clearTerminalUiTimer();
    // A replacement subscription can arrive before its displaced event. Do
    // not orphan an old native handle while resetting correlation ownership.
    this._stopPlayback('new_run');
    this._providerResponseId = this._providerTurnId = null;
    this._transition(ExternalState.CAPTURING);
  }

  onResponseStarted(meta) {
    if (!meta?.response_id) return;
    this._clearFollowupTimer();
    this._providerResponseId = meta.response_id;
    this._providerTurnId = meta.turn_id || null;
    this._transition(ExternalState.PROCESSING);
    this._setPresentationState('intent');
    this._showInteractionUi();
  }

  onResponseAudio(meta, url) {
    if (!this._matchesProvider(meta) || !url) return;
    this._clearFollowupTimer();
    if (this._playbackResponseId && this._playbackResponseId !== meta.response_id) this._stopPlayback('replaced');
    this._playbackResponseId = meta.response_id;
    this._playbackTurnId = meta.turn_id || null;
    this._transition(ExternalState.PLAYING_AND_CAPTURING);
    this._setPresentationState('tts');
    this._playResponseAudio(meta.response_id, url);
  }

  onResponseFinished(meta) {
    if (!this._matchesProvider(meta)) return;
    this._providerResponseId = this._providerTurnId = null;
    if (this._playbackResponseId) return;
    this._restoreCapture();
    this._transition(ExternalState.FOLLOWUP_LISTENING);
    this._armFollowupTimer();
  }

  onSpeechStarted(_meta) {
    this._clearFollowupTimer();
    this._stopPlayback('speech_started');
    this._restoreCapture();
    this._transition(ExternalState.BARGE_IN);
  }

  onInterrupted(meta) {
    if (!this._matchesProvider(meta) && !this._matchesPlayback(meta)) {
      this._log?.log?.('external', `Ignoring stale interruption for ${meta?.response_id || 'unknown response'}`);
      return;
    }
    if (this._matchesProvider(meta)) this._providerResponseId = this._providerTurnId = null;
    if (this._matchesPlayback(meta)) this._stopPlayback('interrupted');
    this._clearFollowupTimer();
    this._restoreCapture();
    this._transition(ExternalState.BARGE_IN);
  }

  onPlaybackComplete(playbackFailed = false) {
    if (!this._playbackResponseId) return false;
    this._playbackResponseId = this._playbackTurnId = null;
    this._restoreCapture();
    this._transition(ExternalState.FOLLOWUP_LISTENING);
    this._armFollowupTimer();
    if (playbackFailed) this._log?.log?.('external', 'External playback failed; retaining follow-up capture');
    return true;
  }

  onExplicitStop(reason = 'client_stopped') {
    if ([ExternalState.TERMINATING, ExternalState.TERMINATED].includes(this._state)) return;
    this._transition(ExternalState.TERMINATING);
    this._clearFollowupTimer();
    this._stopPlayback(reason);
    this._providerResponseId = this._providerTurnId = null;
    this._stopPipeline(reason);
    this._hideInteractionUi();
    this._transition(ExternalState.TERMINATED);
  }

  onTerminal(reason = 'session_finished') {
    if ([ExternalState.IDLE, ExternalState.TERMINATING, ExternalState.TERMINATED].includes(this._state)) return;
    this._transition(ExternalState.TERMINATING);
    this._clearFollowupTimer();
    this._clearTerminalUiTimer();
    this._stopPlayback(reason);
    this._providerResponseId = this._providerTurnId = null;
    // The server has already closed its consumer. Tear down the delegated HA
    // run now so native PCM cannot continue filling its bounded audio queue.
    // Keep the final transcript visible briefly, but force cleanup afterward
    // even when normal run-end cleanup would defer for media linger.
    this._stopPipeline(reason);
    this._terminalUiTimer = this._schedule(() => {
      this._terminalUiTimer = null;
      this._hideInteractionUi();
    }, this._terminalUiTimeoutMs);
    this._transition(ExternalState.TERMINATED);
  }

  onFailure(error) {
    this._log?.error?.('external', `External session failed: ${error?.message || error}`);
    this._transition(ExternalState.FAILED);
    this.onExplicitStop('external_failure');
  }

  destroy() {
    this._clearTerminalUiTimer();
    this.onExplicitStop('session_destroyed');
  }
}
