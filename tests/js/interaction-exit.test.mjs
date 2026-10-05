import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';

// Exercise the shipped common exit method without constructing DOM listeners.
const source = fs.readFileSync(new URL('../../src/shared/double-tap.js', import.meta.url), 'utf8');
const method = source.slice(source.indexOf('  cancelInteraction('), source.lastIndexOf('\n}'));
for (const playDoneChime of [true, false]) {
  test(`shared interaction exit clears presentation but preserves timers (chime=${playDoneChime})`, () => {
    const calls = [];
    const transcript = ['user request', 'assistant reply'];
    const card = {
      show: { dismiss: () => calls.push('show-dismiss') },
      audio: { stopSending: () => calls.push('stop-pcm') },
      tts: { stop: () => calls.push('stop-audio'), playChime: () => calls.push('chime') },
      askQuestion: { cancel: () => calls.push('cancel-question') },
      pipeline: { serviceUnavailable: false, clearContinueState: () => calls.push('clear-continue'), restart: () => calls.push('restart') },
      chat: { clear: () => transcript.splice(0) },
      ui: { hideBlurOverlay: () => calls.push('unblur'), updateForState: () => calls.push('idle-ui') },
      screensaver: { stopExternalKeepalive: () => calls.push('release-keepalive') },
      setState: () => calls.push('idle-state'),
      timer: { dismissAlert: () => assert.fail('Conversation exit must not dismiss timer alerts') },
      hass: {}, config: { satellite_entity: 'assist_satellite.fixture' },
    };
    const Exit = vm.runInNewContext(`(class Exit { ${method} })`, {
      State: { IDLE: 'idle' }, BlurReason: { PIPELINE: 'pipeline' },
      kiosk: { releaseScreensaver: () => calls.push('release-kiosk') },
      getSwitchState: () => true, clearTimeout,
    });
    const exit = new Exit(); exit._card = card; exit._log = { log() {} };
    exit.cancelInteraction({ playDoneChime });
    assert.deepEqual(transcript, []);
    for (const name of ['stop-pcm', 'stop-audio', 'clear-continue', 'unblur', 'idle-ui', 'release-keepalive', 'release-kiosk', 'restart']) {
      assert.equal(calls.filter(x => x === name).length, 1, name);
    }
    assert.equal(calls.includes('chime'), playDoneChime);
  });
}
