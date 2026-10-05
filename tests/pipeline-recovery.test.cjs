// Run with: node --experimental-vm-modules --test tests/pipeline-recovery.test.cjs
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const noop = () => {};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

async function fixture() {
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    __VERSION__: 'test', console,
    setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  const modules = new Map();
  const stubs = {
    'src/wake-word/native-handoff.js': { resumeNativeWake: async () => {} },
    'src/pipeline/kiosk-transport.js': { nativePipelinePreferred: () => false, subscribeKioskPipelineRun: noop },
    'src/audio/chime.js': { CHIME_WAKE: {}, getChimeDuration: () => 0 },
    'src/session/events.js': { onTTSComplete: noop },
    'src/shared/tool-name.js': { humanizeToolName: x => x },
  };
  function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    const relative = path.relative(root, filename);
    const stub = stubs[relative];
    const module = stub
      ? new vm.SyntheticModule(Object.keys(stub), function () {
        for (const [key, value] of Object.entries(stub)) this.setExport(key, value);
      }, { context, identifier: filename })
      : new vm.SourceTextModule(readFileSync(filename, 'utf8'), { context, identifier: filename });
    modules.set(filename, module);
    return module;
  }
  const module = load(path.join(root, 'src/pipeline/index.js'));
  await module.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
  await module.evaluate();
  const toasts = [], calls = { starts: 0, sends: 0, wakes: 0, unsubs: 0 };
  const card = {
    config: { satellite_entity: 'assist_satellite.test' },
    hass: { states: { 'assist_satellite.test': { attributes: { muted: false } } } },
    logger: { log: noop, error: noop }, currentState: 'IDLE',
    toast: { show: t => toasts.push(t), dismiss: noop },
    chat: { clear: noop }, ui: { hideBar: noop, hideBlurOverlay: noop, showStartButton: noop },
    mediaPlayer: { resumeAfterInterrupt: noop },
    tts: { storeStreamingUrl: noop },
    setState(state) { this.currentState = state; },
    audio: {
      _mediaStream: null, stopSending: noop, stopBuffering: noop,
      startMicrophone: async () => { calls.starts++; },
      startSending: () => { calls.sends++; },
    },
    connection: {
      addEventListener: noop, removeEventListener: noop,
      async subscribeMessage(callback) {
        callback({ type: 'init', handler_id: 1 });
        callback({ type: 'run-start', data: {} });
        return async () => { calls.unsubs++; };
      },
    },
  };
  const pipeline = new module.namespace.PipelineManager(card);
  card.pipeline = pipeline;
  card.onPipelineMessage = m => {
    if (m.type === 'run-start') pipeline.handleRunStart(m.data);
    if (m.type === 'error') pipeline.handleError(m.data);
  };
  card.teardown = () => { pipeline.stop(); card._startAttempted = false; };
  const tick = async () => {
    await flush();
    const next = timers.entries().next().value;
    assert.ok(next, 'expected scheduled work');
    timers.delete(next[0]); next[1].fn(); await flush();
  };
  return { card, pipeline, timers, toasts, calls, tick };
}

for (const flag of ['_muted', '_intercomHold', '_userStopped']) {
  test(`automatic restart respects ${flag}, including local wake detection`, async () => {
    const f = await fixture();
    f.card[flag] = true;
    f.card.wakeWord = { isEnabled: () => true, restart: () => f.calls.wakes++ };
    f.pipeline.restart(0);
    f.pipeline.restartContinue('conversation');
    await flush();
    assert.equal(f.timers.size, 0);
    assert.equal(f.calls.wakes, 0);
    assert.equal(f.calls.starts, 0);
  });
}

test('restart reads the HA mute attribute before the session processes its transition', async () => {
  const f = await fixture();
  f.card.hass.states['assist_satellite.test'].attributes.muted = true;
  f.pipeline.restart(0); await flush();
  assert.equal(f.timers.size, 0);
});

test('muting after a restart was scheduled prevents microphone acquisition', async () => {
  const f = await fixture();
  f.pipeline.restart(0); await flush();
  f.card._muted = true;
  await f.tick();
  assert.equal(f.calls.starts, 0);
});

test('late unsubscribe completion cannot revive a cancelled restart', async () => {
  const f = await fixture(), unsubscribe = deferred();
  f.pipeline._unsubscribe = () => unsubscribe.promise;
  f.pipeline.restart(0);
  await f.pipeline.stop();
  unsubscribe.resolve(); await flush();
  assert.equal(f.timers.size, 0);
  assert.equal(f.calls.starts, 0);
});

test('late unsubscribe completion leaves a newer restart intact', async () => {
  const f = await fixture(), unsubscribe = deferred();
  f.pipeline._unsubscribe = () => unsubscribe.promise;
  f.pipeline.restart(0);
  await f.pipeline.stop();
  f.pipeline.restart(50); await flush();
  unsubscribe.resolve(); await flush();
  assert.equal(f.timers.size, 1);
  await f.tick();
  assert.equal(f.calls.starts, 1);
  assert.equal(f.calls.sends, 1);
});

for (const code of ['wake-engine-missing', 'wake-provider-missing']) {
  test(`${code} during microphone acquisition stays stopped without a connection toast`, async () => {
    const f = await fixture(), microphone = deferred();
    f.card.audio.startMicrophone = () => microphone.promise;
    f.pipeline.restart(0); await f.tick();
    f.pipeline.handleError({ code, message: 'No wake word engine' });
    microphone.reject(Object.assign(new Error('Microphone start aborted'), { name: 'MicStartAborted' }));
    await flush();
    assert.deepEqual(f.toasts.map(t => t.id), ['pipeline.no-wake-word-engine']);
    assert.equal(f.card._userStopped, true);
    assert.equal(f.card._startAttempted, true);
    assert.equal(f.pipeline.serviceUnavailable, false);
    assert.equal(f.timers.size, 0);
    assert.equal(f.calls.sends, 0);
    f.pipeline.restart(0); await flush();
    assert.equal(f.timers.size, 0);
  });
}

test('a stopped startup that resolves later cannot start streaming', async () => {
  const f = await fixture(), microphone = deferred();
  f.card.audio.startMicrophone = () => microphone.promise;
  const starting = f.pipeline.start(); await flush();
  await f.pipeline.stop(); microphone.resolve();
  assert.equal(await starting, 'aborted');
  assert.equal(f.calls.sends, 0);
});

test('a cancelled init wait leaves the newer subscription and cancellation callback intact', async () => {
  const f = await fixture();
  f.card.connection.subscribeMessage = async () => async () => { f.calls.unsubs++; };
  const oldStart = f.pipeline.start(); await flush();
  const stopping = f.pipeline.stop();
  const newStart = f.pipeline.start(); await flush();
  await stopping;
  assert.equal(await oldStart, 'aborted');
  assert.equal(typeof f.pipeline._cancelInit, 'function');
  assert.equal(typeof f.pipeline._unsubscribe, 'function');
  assert.equal(f.calls.unsubs, 1);
  await f.pipeline.stop();
  assert.equal(await newStart, 'aborted');
  assert.equal(f.calls.unsubs, 2);
});

test('cancelled follow-up startup does not produce a warning or restart', async () => {
  const f = await fixture(), microphone = deferred();
  f.card.audio.startMicrophone = () => microphone.promise;
  f.pipeline.restartContinue('conversation'); await flush();
  await f.pipeline.stop(); microphone.reject(new Error('startup cancelled'));
  await flush();
  assert.equal(f.toasts.length, 0);
  assert.equal(f.timers.size, 0);
});

test('a genuine connection failure still reports and retries', async () => {
  const f = await fixture();
  f.card.connection.subscribeMessage = async () => { throw new Error('connection closed'); };
  f.pipeline.restart(0); await f.tick();
  assert.deepEqual(f.toasts.map(t => t.id), ['pipeline.connection-lost']);
  assert.equal(f.pipeline.serviceUnavailable, true);
  assert.equal([...f.timers.values()][0].delay, 5000);
});

test('normal restart resumes server audio', async () => {
  const f = await fixture();
  f.pipeline.restart(0); await f.tick();
  assert.equal(f.calls.starts, 1);
  assert.equal(f.calls.sends, 1);
  assert.equal(f.toasts.length, 0);
});
