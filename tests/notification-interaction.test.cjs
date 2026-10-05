const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const noop = () => {};
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

async function fixture({ realPipeline = false } = {}) {
  const holds = new Set(), calls = [], timers = new Map();
  const audioCalls = { starts: 0, sends: 0 };
  let timerId = 0;
  const context = vm.createContext({
    __VERSION__: 'test', console,
    window: { kioskSatellite: {
      platform: 'kiosksatellite',
      bringToFront: noop,
      setInteractionActive(active, reason) {
        calls.push([active, reason]);
        if (active) holds.add(reason);
        else holds.delete(reason);
      },
    } },
    setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  const stubs = {
    'src/audio/chime.js': {
      CHIME_ANNOUNCE_URL: 'chime.mp3', CHIME_WAKE: {},
      getChimeDuration: () => 0, setChimeDurationOverrides: noop, refreshNativeChimeDurations: async () => {},
    },
    'src/audio/media-playback.js': {
      buildMediaUrl: x => x, buildRemoteMediaUrl: x => x, playMediaUrl: noop,
    },
    'src/tts/comms.js': { playRemote: noop },
    'src/shared/satellite-subscription.js': {
      subscribeSatelliteEvents: noop, teardownSatelliteSubscription: noop,
    },
    'src/wake-word/native-handoff.js': {
      setupNativeWakeHandoff: noop, teardownNativeWakeHandoff: noop,
      nativeEngineFor: noop, resumeNativeWake: async () => {},
    },
    'src/pipeline/kiosk-transport.js': {
      nativePipelinePreferred: () => false, subscribeKioskPipelineRun: noop,
    },
  };
  const modules = new Map();
  function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    const stub = stubs[path.relative(root, filename)];
    const module = stub
      ? new vm.SyntheticModule(Object.keys(stub), function () {
        for (const [key, value] of Object.entries(stub)) this.setExport(key, value);
      }, { context, identifier: filename })
      : new vm.SourceTextModule(readFileSync(filename, 'utf8'), { context, identifier: filename });
    modules.set(filename, module);
    return module;
  }
  const module = new vm.SourceTextModule(`
    export { AskQuestionManager } from './src/ask-question/index.js';
    export { StartConversationManager } from './src/start-conversation/index.js';
    export { AnnouncementManager } from './src/announcement/index.js';
    export { PipelineManager } from './src/pipeline/index.js';
    export { DoubleTapHandler } from './src/shared/double-tap.js';
    export { setState, onTTSComplete } from './src/session/events.js';
  `, { context, identifier: path.join(root, 'test-entry.js') });
  await module.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
  await module.evaluate();
  const kiosk = modules.get(path.join(root, 'src/kiosk/index.js')).namespace;
  const notifications = modules.get(path.join(root, 'src/shared/satellite-notification.js')).namespace;
  let onSttEnd;
  const card = {
    config: { satellite_entity: 'assist_satellite.test' },
    hass: { states: { 'assist_satellite.test': { attributes: { muted: false } } } },
    currentState: 'IDLE',
    logger: { log: noop, error: noop },
    connection: {
      sendMessagePromise: async () => ({ matched: true }),
      addEventListener: noop, removeEventListener: noop,
      async subscribeMessage(callback) {
        callback({ type: 'init', handler_id: 1 });
        callback({ type: 'run-start', data: {} });
        return async () => {};
      },
    },
    chat: { clear: noop },
    ui: {
      showBlurOverlay: noop, hideBlurOverlay: noop, onNotificationStart: noop,
      onNotificationDismiss: noop, setAnnouncementMode: noop,
      clearAnnouncementBubbles: noop, startReactive: noop, clearServiceError: noop,
      updateForState: noop, hideBar: noop, clearNotificationStatusOverride: noop,
      hasVisibleMedia: () => false, isLightboxVisible: () => false,
    },
    screensaver: {
      dismiss: noop, startExternalKeepalive: noop, stopExternalKeepalive: noop,
      notifyActivity: noop,
    },
    timer: { alertActive: false },
    audio: {
      setMicTracksMuted: noop, stopSending: noop, stopBuffering: noop,
      startMicrophone: async () => { audioCalls.starts++; },
      startSending: () => { audioCalls.sends++; },
    },
    mediaPlayer: { interrupt: noop, resumeAfterInterrupt: noop, refreshStopWord: noop },
    tts: { playChime: noop, storeStreamingUrl: noop, stop: noop, isPlaying: false },
    setState(state) { module.namespace.setState(this, state); },
    pipeline: {
      restartContinue(_, options) {
        kiosk.stopScreensaver('voice');
        onSttEnd = options.onSttEnd;
      },
      restart() { kiosk.releaseScreensaver('voice'); },
    },
  };
  if (realPipeline) {
    card.pipeline = new module.namespace.PipelineManager(card);
    card.onPipelineMessage = message => {
      if (message.type === 'run-start') card.pipeline.handleRunStart(message.data);
    };
  }
  const manager = new module.namespace.AskQuestionManager(card);
  card.askQuestion = manager;
  card.startConversation = new module.namespace.StartConversationManager(card);
  card.announcement = new module.namespace.AnnouncementManager(card);
  const tick = async delay => {
    const next = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(next, `expected timer for ${delay}ms`);
    timers.delete(next[0]);
    next[1].fn();
    await flush();
  };
  return {
    manager, card, holds, calls, kiosk, notifications, tick, timers, audioCalls,
    completeTts: () => module.namespace.onTTSComplete(card, false),
    cancel() {
      const handler = new module.namespace.DoubleTapHandler(card);
      const state = handler._getInteractionState();
      assert.ok(state, 'interaction must remain cancellable');
      handler._cancel(state.isTimerAlert, state.isNotification, state.isShow);
    },
    start() { manager._play({ id: 1, ask_question: true, preannounce: false }); },
    answer(text) { onSttEnd(text); },
  };
}

for (const outcome of ['answer', 'no speech', 'answer request pending']) {
  test(`question releases its kiosk hold after ${outcome}`, async () => {
    const f = await fixture();
    if (outcome === 'answer request pending') {
      f.card.connection.sendMessagePromise = () => new Promise(() => {});
    }
    f.start();
    await f.tick(3000);
    await f.tick(250);
    assert.deepEqual([...f.holds].sort(), ['ask_question', 'voice']);
    if (outcome === 'no speech') await f.tick(30000);
    else f.answer('yes');
    await f.tick(2000);
    assert.deepEqual([...f.holds], []);
    assert.ok(f.calls.some(([active, reason]) => !active && reason === 'ask_question'));
  });
}

test('cancelling a question preserves unrelated interactions and releases only once', async () => {
  const f = await fixture();
  f.kiosk.stopScreensaver('announcement');
  f.kiosk.stopScreensaver('timer');
  f.start();
  await f.tick(3000);
  await f.tick(250);
  f.manager.cancel();
  f.notifications.clearNotificationUI(f.manager);
  assert.deepEqual([...f.holds].sort(), ['announcement', 'timer', 'voice']);
  assert.equal(f.calls.filter(([active, reason]) => !active && reason === 'ask_question').length, 1);
});

test('ordinary notification cleanup keeps another question active', async () => {
  const f = await fixture();
  f.start();
  const announcement = { card: f.card, log: f.card.logger };
  f.notifications.initNotificationState(announcement);
  f.notifications.playNotification(announcement, { id: 2, preannounce: false }, noop, 'test');
  f.notifications.clearNotificationUI(announcement);
  assert.deepEqual([...f.holds], ['ask_question']);
});

for (const blockedBy of ['_muted', 'HA mute', '_intercomHold', '_userStopped']) {
  for (const kind of ['announcement', 'startConversation']) {
    test(`${kind} releases its hold without reopening the microphone under ${blockedBy}`, async () => {
      const f = await fixture({ realPipeline: true });
      if (blockedBy === 'HA mute') {
        f.card.hass.states['assist_satellite.test'].attributes.muted = true;
      } else {
        f.card[blockedBy] = true;
      }
      f.card[kind]._play({ id: 1, start_conversation: kind === 'startConversation', preannounce: false });
      assert.equal(f.holds.size, 1);
      await f.tick(3000);
      if (kind === 'announcement') await f.tick(5000);
      assert.deepEqual([...f.holds], []);
      assert.deepEqual(f.audioCalls, { starts: 0, sends: 0 });
      assert.equal(f.timers.size, 0);
    });
  }
}

test('start_conversation stays held through the handoff and TTS then releases', async () => {
  const f = await fixture({ realPipeline: true });
  f.card.config.stt_followup_delay_ms = 1000;
  f.card.config.stt_followup_chime = true;
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  assert.deepEqual([...f.holds], ['start_conversation']);
  await f.tick(1000);
  assert.deepEqual([...f.holds], ['start_conversation']);
  await f.tick(250);
  assert.deepEqual([...f.holds], ['start_conversation', 'voice']);
  assert.equal(f.card.currentState, 'STT');
  assert.deepEqual(f.audioCalls, { starts: 1, sends: 1 });
  f.card.tts.isPlaying = true;
  f.card.setState('IDLE');
  assert.deepEqual([...f.holds], ['start_conversation', 'voice']);
  f.card.tts.isPlaying = false;
  f.completeTts();
  assert.deepEqual([...f.holds], []);
});

test('start_conversation stays held when run-start arrives after pipeline initialization', async () => {
  const f = await fixture({ realPipeline: true });
  let send;
  f.card.connection.subscribeMessage = async callback => {
    send = callback;
    callback({ type: 'init', handler_id: 1 });
    return async () => {};
  };
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  assert.deepEqual([...f.holds], ['start_conversation']);
  send({ type: 'run-start', data: {} });
  assert.deepEqual([...f.holds], ['start_conversation', 'voice']);
  f.card.setState('IDLE');
  assert.deepEqual([...f.holds], []);
});

test('cancelling start_conversation during the handoff releases its hold', async () => {
  const f = await fixture({ realPipeline: true });
  f.card.config.stt_followup_delay_ms = 1000;
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  f.card._muted = true;
  f.cancel();
  await flush();
  assert.deepEqual([...f.holds], []);
  assert.equal(f.timers.size, 0);
  assert.deepEqual(f.audioCalls, { starts: 0, sends: 0 });
});

test('cancelling start_conversation after STT begins releases the voice hold', async () => {
  const f = await fixture({ realPipeline: true });
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  f.card._muted = true;
  f.cancel();
  await flush();
  assert.deepEqual([...f.holds], []);
});

test('failed start_conversation startup releases the notification hold', async () => {
  const f = await fixture({ realPipeline: true });
  f.card.connection.subscribeMessage = async () => { throw new Error('connection closed'); };
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  assert.deepEqual([...f.holds], []);
});

test('muting during the handoff releases start_conversation without starting STT', async () => {
  const f = await fixture({ realPipeline: true });
  f.card.config.stt_followup_delay_ms = 1000;
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  f.card._muted = true;
  await f.tick(1000);
  assert.deepEqual([...f.holds], []);
  assert.deepEqual(f.audioCalls, { starts: 0, sends: 0 });
});

test('start_conversation releases its hold if no pipeline is available', async () => {
  const f = await fixture();
  f.card.pipeline = null;
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  assert.deepEqual([...f.holds], []);
});

test('late startup completion cannot release a newer start_conversation prompt', async () => {
  const f = await fixture({ realPipeline: true });
  let finishStart;
  f.card.pipeline.start = () => new Promise(resolve => { finishStart = resolve; });
  f.card.startConversation._play({ id: 1, start_conversation: true, preannounce: false });
  await f.tick(3000);
  await f.card.pipeline.stop();
  f.notifications.clearNotificationUI(f.card.startConversation);
  f.card.startConversation._play({ id: 2, start_conversation: true, preannounce: false });
  finishStart('aborted');
  await flush();
  assert.deepEqual([...f.holds], ['start_conversation']);
});
