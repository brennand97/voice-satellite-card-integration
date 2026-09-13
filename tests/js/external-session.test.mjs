import assert from 'node:assert/strict';
import test from 'node:test';
import { ExternalSessionController, ExternalState } from '../../src/pipeline/external-session.mjs';

function harness() {
  const calls = [];
  let nextTimer = 0;
  const timers = new Map();
  const controller = new ExternalSessionController({
    log: { log: (...args) => calls.push(['log', ...args]) },
    playResponseAudio: (id, url) => calls.push(['play', id, url]),
    stopResponseAudio: (id, reason) => calls.push(['stop', id, reason]),
    restoreCaptureVisualization: () => calls.push(['restore']),
    setPresentationState: (state) => calls.push(['state', state]),
    showInteractionUi: () => calls.push(['show']),
    hideInteractionUi: () => calls.push(['hide']),
    stopPipeline: (reason) => calls.push(['pipeline-stop', reason]),
    schedule: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    cancelScheduled: (id) => timers.delete(id),
    followupTimeoutMs: 1000,
  });
  return {
    controller, calls,
    timers,
    fireTimers: () => [...timers.values()].forEach(({ fn }) => fn()),
  };
}

function activePlayback(h) {
  h.controller.onRunStart();
  h.controller.onResponseStarted({ turn_id: 'turn-1', response_id: 'response-1' });
  h.controller.onResponseAudio({ turn_id: 'turn-1', response_id: 'response-1' }, 'https://audio/1');
}

test('response finish releases provider ownership but retains active playback', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onResponseFinished({ turn_id: 'turn-1', response_id: 'response-1' });

  assert.equal(h.controller.state, ExternalState.PLAYING_AND_CAPTURING);
  assert.equal(h.controller.providerResponseId, null);
  assert.equal(h.controller.playbackResponseId, 'response-1');
  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), []);
  assert.deepEqual(h.calls.filter(([name]) => name === 'pipeline-stop'), []);
});

test('server-confirmed speech cancels locally buffered playback and restores capture visuals', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onResponseFinished({ turn_id: 'turn-1', response_id: 'response-1' });
  h.controller.onSpeechStarted({ turn_id: 'turn-2' });

  assert.equal(h.controller.state, ExternalState.BARGE_IN);
  assert.equal(h.controller.playbackResponseId, null);
  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), [['stop', 'response-1', 'speech_started']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'restore'), [['restore']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'pipeline-stop'), []);
});

test('a new response replaces, rather than overlaps, old local playback', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onResponseStarted({ turn_id: 'turn-2', response_id: 'response-2' });
  h.controller.onResponseAudio({ turn_id: 'turn-2', response_id: 'response-2' }, 'https://audio/2');

  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), [['stop', 'response-1', 'replaced']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'play'), [
    ['play', 'response-1', 'https://audio/1'], ['play', 'response-2', 'https://audio/2'],
  ]);
  assert.equal(h.controller.playbackResponseId, 'response-2');
});

test('playback completion restores delegated capture and starts follow-up timeout', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onPlaybackComplete(false);

  assert.equal(h.controller.state, ExternalState.FOLLOWUP_LISTENING);
  assert.equal(h.controller.playbackResponseId, null);
  assert.deepEqual(h.calls.filter(([name]) => name === 'restore'), [['restore']]);
  h.fireTimers();
  assert.equal(h.controller.state, ExternalState.TERMINATED);
  assert.deepEqual(h.calls.filter(([name]) => name === 'pipeline-stop'), [['pipeline-stop', 'followup_timeout']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'hide'), [['hide']]);
});

test('replacement run cannot orphan prior playback', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onRunStart();

  assert.equal(h.controller.state, ExternalState.CAPTURING);
  assert.equal(h.controller.playbackResponseId, null);
  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), [['stop', 'response-1', 'new_run']]);
});

test('stale interruptions cannot stop newer playback', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onResponseStarted({ turn_id: 'turn-2', response_id: 'response-2' });
  h.controller.onResponseAudio({ turn_id: 'turn-2', response_id: 'response-2' }, 'https://audio/2');
  h.controller.onInterrupted({ turn_id: 'turn-1', response_id: 'response-1' });

  assert.equal(h.controller.playbackResponseId, 'response-2');
  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), [['stop', 'response-1', 'replaced']]);
});

test('active state is exposed only while an External run can be stopped', () => {
  const h = harness();
  assert.equal(h.controller.isActive(), false);
  h.controller.onRunStart();
  assert.equal(h.controller.isActive(), true);
  h.controller.onExplicitStop('stop_word');
  assert.equal(h.controller.isActive(), false);
});

test('server terminal event stops PCM but clears the final transcript after three seconds', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onTerminal('session_finished');

  assert.equal(h.controller.state, ExternalState.TERMINATED);
  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), [['stop', 'response-1', 'session_finished']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'pipeline-stop'), [['pipeline-stop', 'session_finished']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'hide'), []);
  assert.deepEqual([...h.timers.values()].map(({ delay }) => delay), [3000]);

  h.fireTimers();
  assert.deepEqual(h.calls.filter(([name]) => name === 'hide'), [['hide']]);
});

test('explicit stop is terminal and cancels playback exactly once', () => {
  const h = harness();
  activePlayback(h);
  h.controller.onExplicitStop('kiosk_stop');
  h.controller.onExplicitStop('kiosk_stop');

  assert.equal(h.controller.state, ExternalState.TERMINATED);
  assert.deepEqual(h.calls.filter(([name]) => name === 'stop'), [['stop', 'response-1', 'kiosk_stop']]);
  assert.deepEqual(h.calls.filter(([name]) => name === 'pipeline-stop'), [['pipeline-stop', 'kiosk_stop']]);
});
