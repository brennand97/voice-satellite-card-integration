const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

async function fixture({ accepted = true, bridge = true } = {}) {
  const calls = [], requests = [], handlers = new Map();
  const api = bridge ? {
    platform: 'kiosksatellite',
    async setVoiceTimers(s) { calls.push(['timers', s]); return accepted; },
    async setVoiceTimerAlert(s) { calls.push(['alert', s]); return accepted; },
    voiceTimerActionFailed(e) { calls.push(['failed', e]); },
  } : null;
  const context = vm.createContext({ window: {
    kioskSatellite: api,
    addEventListener: (name, fn) => handlers.set(name, fn),
    removeEventListener: (name) => handlers.delete(name),
  } });
  const module = new vm.SourceTextModule(readFileSync(path.join(__dirname, '../src/timer/native-pills.js'), 'utf8'), { context });
  await module.link(() => {});
  await module.evaluate();
  const manager = {
    timers: [{ id: 'pasta', name: 'Pasta', totalSeconds: 60, startedAt: 1234, isActive: false }],
    log: { error: () => {} },
    card: {
      config: { satellite_entity: 'assist_satellite.kitchen' },
      ui: {
        removeTimerContainer: () => calls.push(['remove']),
        syncTimerPills: () => calls.push(['fallback']),
      },
      connection: { sendMessagePromise: async (p) => requests.push(p) },
    },
    clearAlert: () => calls.push(['dismiss']),
  };
  const native = new module.namespace.NativeTimerPills(manager);
  return { native, manager, calls, requests, handlers, api };
}

test('handoff hides browser pills only after acknowledgement and preserves names and pause state', async () => {
  const f = await fixture();
  assert.equal(f.native.sync(f.manager.timers), false);
  assert.equal(f.calls.length, 0);
  await flush();
  assert.equal(f.native.active, true);
  assert.equal(f.calls[0][1].timers[0].name, 'Pasta');
  assert.equal(f.calls[0][1].timers[0].isActive, false);
  assert.equal(f.calls[1][0], 'remove');
  f.native.sync(f.manager.timers);
  await flush();
  assert.equal(f.calls.length, 2);
});

test('unsupported or rejected handoff keeps browser rendering', async () => {
  const old = await fixture({ bridge: false });
  assert.equal(old.native.sync(old.manager.timers), false);
  const rejected = await fixture({ accepted: false });
  rejected.native.sync(rejected.manager.timers);
  await flush();
  assert.equal(rejected.native.active, false);
  assert.equal(rejected.calls.at(-1)[0], 'fallback');
});

test('gestures address the right timer and reject events for another satellite', async () => {
  const f = await fixture();
  await f.native.control({ entityId: 'assist_satellite.other', id: 'pasta', action: 'cancel' });
  await f.native.control({ entityId: 'assist_satellite.kitchen', id: 'unknown', action: 'cancel' });
  assert.equal(f.requests.length, 0);
  for (const action of ['pause', 'resume', 'cancel']) {
    await f.native.control({ entityId: 'assist_satellite.kitchen', id: 'pasta', action });
  }
  assert.equal(f.requests[0].paused, true);
  assert.equal(f.requests[1].paused, false);
  assert.equal(f.requests[2].type, 'voice_satellite/cancel_timer');
});

test('server errors leave state intact and report to the native interface', async () => {
  const f = await fixture();
  f.manager.card.connection.sendMessagePromise = async () => { throw new Error('Disconnected'); };
  await f.native.control({ entityId: 'assist_satellite.kitchen', id: 'pasta', action: 'pause' });
  assert.equal(f.manager.timers[0].isActive, false);
  assert.equal(f.calls.at(-1)[0], 'failed');
});

test('alerts are handed over with mute state and cleared on teardown', async () => {
  const f = await fixture();
  assert.equal(await f.native.showAlert(f.manager.timers, true), true);
  assert.equal(f.calls[0][1].muted, true);
  await f.native.control({ entityId: 'assist_satellite.kitchen', id: 'pasta', action: 'dismiss' });
  assert.equal(f.calls.at(-1)[0], 'dismiss');
  f.native.destroy();
  await flush();
  assert.equal(f.handlers.size, 0);
  assert.equal(f.calls.at(-1)[0], 'alert');
  assert.equal(f.calls.at(-1)[1].timers.length, 0);
});
