const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };

async function fixture({ earlyEnd = false, pending = false, browser = false } = {}) {
  let now = 0, next = 0, nextSound = 0, release;
  const timers = new Map(), listeners = new Map(), plays = [], stops = [];
  const emit = (type, detail) => {
    for (const fn of [...(listeners.get(type) || [])]) fn({type, detail});
  };
  const window = {
    location: {origin: 'https://ha.test'},
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    kioskSatellite: {
      platform: 'kiosksatellite', bringToFront() {}, stopScreensaver() {},
      getVoiceChimeDurations: async () => ({'wake.mp3': 4.5, 'alert.mp3': 7.824}),
      async playSound(url) {
        const id = `sound${++nextSound}`;
        plays.push({id, url, at: now});
        if (earlyEnd) emit('kiosksatellite:sound-ended', {id});
        if (pending) await new Promise(r => { release = r; });
        return {id};
      },
      stopSound(id) { stops.push(id); emit('kiosksatellite:sound-ended', {id}); },
    },
  };
  const browserAudio = [];
  if (browser) delete window.kioskSatellite.playSound;
  class Audio {
    constructor() { this.handlers = new Map(); browserAudio.push(this); }
    addEventListener(name, fn) { this.handlers.set(name, fn); }
    removeEventListener(name) { this.handlers.delete(name); }
    play() { this.id = `browser${++nextSound}`; plays.push({id: this.id, at: now}); return Promise.resolve(); }
    pause() { stops.push(this.id); }
    end() { this.handlers.get('ended')?.(); }
  }
  const schedule = (fn, delay, repeat = false) => {
    const id = ++next; timers.set(id, {fn, at: now + delay, delay, repeat}); return id;
  };
  const context = vm.createContext({window, Audio, __VERSION__: 'test', console, URL,
    Date: {now: () => now},
    setTimeout: (fn, ms) => schedule(fn, ms), clearTimeout: id => timers.delete(id),
    setInterval: (fn, ms) => schedule(fn, ms, true), clearInterval: id => timers.delete(id),
  });
  const root = path.join(__dirname, '..');
  const modules = new Map();
  const stub = (values, identifier) => new vm.SyntheticModule(Object.keys(values), function () {
    for (const [key, value] of Object.entries(values)) this.setExport(key, value);
  }, {context, identifier});
  function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    const name = path.relative(root, filename);
    const mod = name === 'src/shared/satellite-state.js'
      ? stub({getSwitchState: () => false, getSelectState: () => 'announcement'}, filename)
      : name === 'src/tts/comms.js' ? stub({playRemote() {}, stopRemote() {}}, filename)
      : new vm.SourceTextModule(readFileSync(filename, 'utf8'), {context, identifier: filename});
    modules.set(filename, mod); return mod;
  }
  const entry = new vm.SourceTextModule(`
    export * from './src/timer/ui.js';
    export * from './src/audio/chime.js';
    export * from './src/kiosk/index.js';
  `, {context, identifier: path.join(root, 'test-entry.js')});
  await entry.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier)));
  await entry.evaluate();
  const noop = () => {};
  const manager = {
    timers: [], alertActive: false, log: {log: noop, error: noop}, stopTick: noop,
    card: {config: {}, mediaPlayer: {volume: 1, refreshStopWord: noop},
      ui: {showBlurOverlay: noop, hideBlurOverlay: noop, showTimerAlert: noop, clearTimerAlert: noop, removeTimerContainer: noop}},
  };
  const advance = async (ms) => {
    const target = now + ms;
    while (true) {
      const item = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a,b) => a[1].at-b[1].at)[0];
      if (!item) break;
      const [id, timer] = item;
      now = timer.at;
      if (timer.repeat) timer.at += timer.delay; else timers.delete(id);
      timer.fn(); await flush();
    }
    now = target; await flush();
  };
  return {api: entry.namespace, manager, plays, stops, emit, advance, release: () => release(), window, browserAudio};
}

test('a long native alert completes before the next repeat and dismissal stops it', async () => {
  const f = await fixture();
  await f.api.showAlert(f.manager, ['Test']); await flush();
  await f.advance(7824);
  assert.equal(f.plays.length, 1);
  f.emit('kiosksatellite:sound-ended', {id: 'sound1'}); await flush();
  await f.advance(250);
  assert.equal(f.plays.length, 2);
  f.api.clearAlert(f.manager); await flush();
  assert.deepEqual(f.stops, ['sound2']);
  await f.advance(10000);
  assert.equal(f.plays.length, 2);
});

test('dismissal during native startup cancels the late alert', async () => {
  const f = await fixture({pending: true});
  await f.api.showAlert(f.manager, ['Test']); await flush();
  f.api.clearAlert(f.manager);
  f.release(); await flush();
  assert.deepEqual(f.stops, ['sound1']);
});

test('a sound ending before the bridge response still resolves completion', async () => {
  const f = await fixture({earlyEnd: true});
  const sound = await f.api.playNativeSoundTracked('https://ha.test/chime.mp3', 1);
  let ended = false;
  sound.done.then(() => { ended = true; }); await flush();
  assert.equal(ended, true);
});

test('local durations update live while remote speakers retain server durations', async () => {
  const f = await fixture();
  f.api.setChimeDurationOverrides({'wake.mp3': 0.5});
  await f.api.refreshNativeChimeDurations();
  assert.equal(f.api.getChimeDuration(f.api.CHIME_WAKE, f.manager.card), 4.5);
  assert.equal(f.api.getChimeDuration(f.api.CHIME_WAKE, {ttsTarget: 'media_player.remote'}), 0.5);
  f.emit('kiosksatellite:voice-chimes-changed', {'wake.mp3': 2});
  assert.equal(f.api.getChimeDuration(f.api.CHIME_WAKE, f.manager.card), 2);
  delete f.window.kioskSatellite;
  assert.equal(f.api.getChimeDuration(f.api.CHIME_WAKE, f.manager.card), 0.5);
});

test('muting a long alert stops the currently playing sound', async () => {
  const f = await fixture();
  await f.api.showAlert(f.manager, ['Test']); await flush();
  f.manager._attrs = {mute_timers: true};
  await f.advance(100);
  assert.deepEqual(f.stops, ['sound1']);
  await f.advance(6000);
  assert.equal(f.plays.length, 1);
  f.api.clearAlert(f.manager);
});


test('browser playback also waits for completion and stops on dismissal', async () => {
  const f = await fixture({browser: true});
  await f.api.showAlert(f.manager, ['Browser']); await flush();
  await f.advance(7824);
  assert.equal(f.plays.length, 1);
  f.browserAudio[0].end(); await flush();
  await f.advance(250);
  assert.equal(f.plays.length, 2);
  f.api.clearAlert(f.manager); await flush();
  assert.deepEqual(f.stops, ['browser2']);
});

test('an older metadata request cannot overwrite a live settings update', async () => {
  const f = await fixture();
  let resolve;
  f.window.kioskSatellite.getVoiceChimeDurations = () => new Promise(r => {resolve = r;});
  const refresh = f.api.refreshNativeChimeDurations();
  f.emit('kiosksatellite:voice-chimes-changed', {'wake.mp3': 6});
  resolve({'wake.mp3': 1});
  await refresh;
  assert.equal(f.api.getChimeDuration(f.api.CHIME_WAKE, f.manager.card), 6);
});
