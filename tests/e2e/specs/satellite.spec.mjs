import fs from 'node:fs';
import { test, expect, hass, screenshot, ws } from '@homelab/ha-testbed';

async function openKiosk(page, ha) {
  expect(ha.run.fixture, 'Satellite entry must exist; see adapter setup error').toBeTruthy();
  await page.addInitScript(fixture => {
    localStorage.setItem('vs-satellite-entity', fixture.entity);
    if (!localStorage.getItem('vs-panel-config')) localStorage.setItem('vs-panel-config', JSON.stringify(fixture.panel));
    window.__e2ePlayback = [];
    window.__e2eMedia = [];
    window.addEventListener('unhandledrejection', e => console.warn('E2E unhandled rejection', JSON.stringify({code:e.reason?.code, message:e.reason?.message})));
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function(data) {
      if (!this.__e2eTypes) {
        this.__e2eTypes = new Map();
        this.addEventListener('message', event => {
          if (typeof event.data !== 'string') return;
          try { const m = JSON.parse(event.data); if (m.success === false) console.warn('E2E failed WS', JSON.stringify({type:this.__e2eTypes.get(m.id),code:m.error?.code})); } catch (_) {}
        });
      }
      if (typeof data === 'string') { try {const m=JSON.parse(data); if (m.id) this.__e2eTypes.set(m.id,m.type);} catch (_) {} }
      return send.call(this,data);
    };
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function(...args) {
      if (!window.__e2eMedia.includes(this)) window.__e2eMedia.push(this);
      this.addEventListener('timeupdate', () => {
        // Observe actual decode/playback progress, not just a successful HTTP fetch.
        if (this.currentTime > 0 && /\/audio\/v1\/|\/voice_satellite\//.test(this.src)) {
          window.__e2ePlayback.push({ path: new URL(this.src).pathname, time: this.currentTime });
        }
      });
      return play.apply(this, args);
    };
  }, ha.run.fixture);
  await page.goto('/lovelace/testbed');
  await hass(page);
  await page.waitForFunction(() => document.querySelector('voice-satellite-card')?._session?.isStarted && !document.querySelector('voice-satellite-card')._session._starting, {}, { timeout: 15_000 });
}

async function stats(ha, request) {
  const response = await request.get(ha.run.transport_url + '/e2e/stats', {
    headers: { Authorization: `Bearer ${ha.run.credentials.transport_token}` },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test('satellite: options flow accepts the fixture', async ({ ha }) => {
  expect(ha.run.adapter_status, ha.run.adapter_error).toBe('ready');
});

test('satellite: configured panel, assets and persistence', async ({ page, ha }, info) => {
  await openKiosk(page, ha);
  await page.goto('/voice-satellite');
  await expect(page.getByText('Voice Satellite', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Engine running', { exact: true })).toBeVisible();
  expect((await ha.get('/api/states/' + ha.run.fixture.entity)).state).not.toBe('unavailable');
  await screenshot(page, info, 'satellite-panel');
  await page.reload();
  await expect(page.getByText('Engine running', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('vs-satellite-entity'))).toBe(ha.run.fixture.entity);
});

test('satellite: browser action -> external text -> streamed audio -> rendered reply', async ({ page, ha, request }, info) => {
  await openKiosk(page, ha);
  const before = await stats(ha, request);
  await page.getByText('Test text turn', { exact: true }).click();
  await expect(page.locator('.vs-chat-msg').filter({ hasText: 'Test response: hello from browser' })).toBeVisible();
  await expect.poll(async () => (await stats(ha, request)).turns).toBeGreaterThan(before.turns);
  await expect.poll(() => page.evaluate(() => window.__e2ePlayback.some(p => /\/audio\/v1\/|media_proxy/.test(p.path) && p.time > 0))).toBe(true);
  await screenshot(page, info, 'text-and-audio-response');
});

test('satellite: triggered voice captures real browser PCM and responds', async ({ page, ha, request }, info) => {
  await openKiosk(page, ha);
  const before = await stats(ha, request);
  await page.getByText('Wake test microphone', { exact: true }).click();
  await expect.poll(async () => (await stats(ha, request)).input_bytes, { timeout: 20_000 }).toBeGreaterThan(before.input_bytes);
  await expect(page.locator('.vs-chat-msg').filter({ hasText: 'Test response: received microphone audio' })).toBeVisible({ timeout: 30_000 });
  await screenshot(page, info, 'microphone-response');
});

test('satellite: timer renders, expires, dismisses', async ({ page, ha }, info) => {
  await openKiosk(page, ha);
  await page.getByText('Test timer', { exact: true }).click();
  await expect(page.locator('.vs-timer-pill')).toBeVisible();
  await screenshot(page, info, 'timer-active');
  await expect(page.locator('.vs-timer-alert')).toBeVisible({ timeout: 15_000 });
  await screenshot(page, info, 'timer-expired');
  await page.keyboard.press('Escape');
  await expect(page.locator('.vs-timer-alert')).not.toBeVisible();
});

test('satellite: diagnostics exposed over actual HA websocket', async ({ page, ha }, info) => {
  await openKiosk(page, ha);
  await page.goto('/voice-satellite');
  await page.getByRole('button', { name: 'Run diagnostics', exact: true }).click();
  await expect(page.getByText('Diagnostics have not been run yet.')).not.toBeVisible();
  await screenshot(page, info, 'satellite-diagnostics');
  const result = await ws(page, { type: 'voice_satellite/get_panel_settings', entity_id: ha.run.fixture.entity });
  expect(result).toBeTruthy();
});

const speaker = 'media_player.testbed_speaker';
const player = ha => ha.get('/api/states/' + speaker);
const volume = async ha => (await player(ha)).attributes.volume_level;
async function text(ha, prompt) {
  await ha.service('voice_satellite', 'show', { entity_id: ha.run.fixture.entity, prompt, silent: false, duration: 0 });
}
async function music(ha, level = 0.7) {
  await ha.service('media_player', 'play_media', {entity_id: speaker, media_content_type: 'music', media_content_id: 'fixture-track-a'});
  await ha.service('media_player', 'volume_set', {entity_id: speaker, volume_level: level});
  await expect.poll(() => volume(ha)).toBe(level);
}
async function policy(ha, action = 'duck', entities = [speaker]) {
  const f = ha.run.fixture;
  let flow = await ha.post('/api/config/config_entries/options/flow', {handler: f.satellite_entry});
  for (const payload of [
    {conversation_service_entry_id: f.service_entry},
    {conversation_profile_id: f.profile_id},
    {media_guard_entities: entities, media_guard_action: action, media_guard_volume: 10, media_guard_restore_delay_ms: 350},
  ]) flow = await ha.post('/api/config/config_entries/options/flow/' + flow.flow_id, payload);
  expect(flow.type, JSON.stringify(flow.errors)).toBe('create_entry');
}
async function heard(page, reply) {
  await expect(page.locator('.vs-chat-msg').filter({hasText: reply})).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__e2ePlayback.some(p => p.path.includes('/audio/v1/') && p.time > 0))).toBe(true);
}
async function dismiss(page) {
  await page.keyboard.press('Escape');
}

test.describe('live media and interruption', () => {
  test.afterEach(async ({page,ha}, info) => {
    if (page.isClosed()) return;
    const entries=await page.evaluate(() => document.querySelector('voice-satellite-card')?._session?.logger.getEntries().map(({ts,category,msg}) => ({ts,category,msg})) ?? []);
    let body=JSON.stringify(entries,null,2);
    const c=ha.run.credentials;
    for (const secret of [c.password,c.transport_token,c.signing_key,c.tokens.access_token,c.tokens.refresh_token]) if (secret) body=body.replaceAll(secret,'[REDACTED]');
    body=body.replace(/([?&](?:token|authSig|signature)=)[^&\s"']+/gi,'$1[REDACTED]');
    fs.writeFileSync(info.outputPath('session-log.json'),body);
    await info.attach('sanitized-session-log',{path:info.outputPath('session-log.json'),contentType:'application/json'});
    await page.close();
  });

  test('duck during real streamed playback; Escape stops audio and restores original volume', async ({page,ha}, info) => {
    await policy(ha); await music(ha); await openKiosk(page,ha);
    await text(ha,'long duck'); await heard(page,'Test response: long duck');
    await expect.poll(() => volume(ha)).toBe(0.1);
    await screenshot(page,info,'live-ducked-playback');
    await dismiss(page);
    await expect.poll(() => page.evaluate(() => window.__e2eMedia.filter(m => m.src.includes('/audio/v1/')).every(m => m.paused))).toBe(true);
    await expect.poll(() => volume(ha)).toBe(0.7);
    await screenshot(page,info,'live-restored-after-stop');
  });

  test('duck never raises an already quiet player', async ({page,ha}) => {
    await policy(ha); await music(ha,0.04); await openKiosk(page,ha);
    await text(ha,'long quiet'); await heard(page,'Test response: long quiet');
    expect(await volume(ha)).toBe(0.04);
    await dismiss(page); await expect.poll(() => volume(ha)).toBe(0.04);
  });

  test('user changes intended volume while ducked; restore latest intent, not stale original', async ({page,ha}) => {
    await policy(ha); await music(ha); await openKiosk(page,ha);
    await text(ha,'long intent'); await heard(page,'Test response: long intent');
    await expect.poll(() => volume(ha)).toBe(0.1);
    await ha.service('media_player','volume_set',{entity_id:speaker,volume_level:0.42});
    await expect.poll(() => volume(ha)).toBe(0.1);
    await dismiss(page); await expect.poll(() => volume(ha)).toBe(0.42);
  });

  test('user stops music while ducked; guard must not resume it', async ({page,ha}) => {
    await policy(ha); await music(ha); await openKiosk(page,ha);
    await text(ha,'long stopped music'); await heard(page,'Test response: long stopped music');
    await expect.poll(() => volume(ha)).toBe(0.1);
    await ha.service('media_player','media_stop',{entity_id:speaker});
    await dismiss(page);
    await expect.poll(async () => (await player(ha)).state).toBe('idle');
    // Closing the consumer releases the lease; inspect service history afterward.
    await page.close();
    expect((await player(ha)).attributes.service_history.at(-1).action).toBe('media_stop');
  });

  test('pause policy pauses and resumes playing music', async ({page,ha}) => {
    await policy(ha,'pause'); await music(ha); await openKiosk(page,ha);
    await text(ha,'long paused'); await heard(page,'Test response: long paused');
    await expect.poll(async () => (await player(ha)).state).toBe('paused');
    expect(await volume(ha)).toBe(0.7);
    await dismiss(page); await expect.poll(async () => (await player(ha)).state).toBe('playing');
  });

  test('off policy does not change music volume or playback', async ({page,ha}) => {
    await policy(ha,'off'); await music(ha); await openKiosk(page,ha);
    await text(ha,'long off'); await heard(page,'Test response: long off');
    expect(await volume(ha)).toBe(0.7); expect((await player(ha)).state).toBe('playing');
    await dismiss(page);
  });

  test('live voice barges into streaming response; old audio stops and new reply arrives', async ({page,ha,request},info) => {
    await policy(ha); await music(ha); await openKiosk(page,ha);
    const before = await stats(ha,request);
    await text(ha,'long interrupted'); await heard(page,'Test response: long interrupted');
    await expect.poll(() => volume(ha)).toBe(0.1);
    await page.waitForFunction(() => { const playing=window.__e2eMedia.find(m=>m.src.includes('/audio/v1/') && !m.paused); if (!playing) return false; window.__e2eOld=playing; return true; });
    await ha.service('voice_satellite','wake',{entity_id:ha.run.fixture.entity});
    await expect.poll(async () => (await stats(ha,request)).input_bytes).toBeGreaterThan(before.input_bytes);
    // The provider may have finished generation before browser playback.
    // This case asserts local playback barge-in; generation cancel is separate.
    await expect.poll(() => page.evaluate(() => window.__e2eOld?.paused)).toBe(true);
    await expect(page.locator('.vs-chat-msg').filter({hasText:'Test response: received microphone audio'})).toBeVisible();
    expect(await volume(ha)).toBe(0.1);
    await screenshot(page,info,'live-voice-barge-in');
    await dismiss(page); await expect.poll(() => volume(ha)).toBe(0.7);
  });

  test('actual browser turn executes real MCP tool against synthetic light', async ({page,ha,request},info) => {
    await policy(ha); await openKiosk(page,ha);
    await ha.service('input_boolean','turn_off',{entity_id:'input_boolean.testbed_light'});
    const before = await stats(ha,request);
    await text(ha,'test tool'); await heard(page,'Test light turned on');
    await expect.poll(async () => (await ha.get('/api/states/light.testbed_light')).state).toBe('on');
    await expect.poll(async () => (await stats(ha,request)).tool_calls).toBeGreaterThan(before.tool_calls);
    await screenshot(page,info,'live-browser-mcp-tool'); await dismiss(page);
  });

  test('interrupt a provider still generating; cancellation reaches the actor', async ({page,ha,request}) => {
    await policy(ha); await openKiosk(page,ha);
    const before = await stats(ha,request);
    await text(ha,'slow generation');
    await page.waitForFunction(() => document.querySelector('voice-satellite-card')._session._externalSession.state === 'processing');
    console.log('Pre-barge state', await page.evaluate(() => { const s=document.querySelector('voice-satellite-card')._session; return {state:s.currentState, external:s._externalSession.state, handler:s.pipeline.binaryHandlerId,started:s.isStarted, mic:!!s.audio._mediaStream}; }));
    await ha.service('voice_satellite','wake',{entity_id:ha.run.fixture.entity});
    console.log('Post-wake state', await page.evaluate(() => { const s=document.querySelector('voice-satellite-card')._session; return {state:s.currentState, external:s._externalSession.state, handler:s.pipeline.binaryHandlerId,started:s.isStarted, mic:!!s.audio._mediaStream}; }));
    await expect.poll(async () => (await stats(ha,request)).interrupts).toBeGreaterThan(before.interrupts);
    await expect(page.locator('.vs-chat-msg').filter({hasText:'Test response: received microphone audio'})).toBeVisible();
    await expect(page.locator('.vs-chat-msg').filter({hasText:'Test response: slow generation'})).not.toBeVisible();
    await dismiss(page);
  });

  test('all timer tools, relative mutations, ambiguous cancel safety and unrelated timer preservation', async ({page,ha,request},info) => {
    await policy(ha); await openKiosk(page,ha);
    async function call(operation, arguments_ = {}) {
      const before = await stats(ha,request);
      await text(ha,'timer:' + JSON.stringify({operation, arguments:arguments_}));
      await expect.poll(async () => (await stats(ha,request)).tool_calls).toBeGreaterThan(before.tool_calls);
      const result = (await stats(ha,request)).last_timer;
      return {error: result.is_error, data: result.is_error ? {} : JSON.parse(result.content.find(c => c.type === 'text').text)};
    }
    expect((await call('StartTimer',{name:'Pasta',seconds:45})).error).toBe(false);
    expect((await call('StartTimer',{name:'Tea',seconds:50})).error).toBe(false);
    let r = await call('GetTimerStatus'); expect(r.data.timers.map(t=>t.name).sort()).toEqual(['Pasta','Tea']);
    r = await call('ExtendTimer',{name:'Pasta',seconds:10}); expect(r.data.relative_seconds).toBe(10);
    r = await call('ShortenTimer',{name:'Pasta',seconds:5}); expect(r.data.relative_seconds).toBe(-5);
    expect((await call('ShortenTimer',{name:'Pasta',seconds:59})).error).toBe(true);
    expect((await call('RenameTimer',{name:'Pasta',new_name:'Dinner'})).error).toBe(false);
    await expect(page.locator('.vs-timer-pill').filter({hasText:'Dinner'})).toBeVisible();
    expect((await call('StopTimer')).error).toBe(true);
    r = await call('GetTimerStatus'); expect(r.data.timers.map(t=>t.name).sort()).toEqual(['Dinner','Tea']);
    expect((await call('StopTimer',{name:'Dinner'})).error).toBe(false);
    r = await call('GetTimerStatus'); expect(r.data.timers.map(t=>t.name)).toEqual(['Tea']);
    await screenshot(page,info,'live-timer-safety');
    expect((await call('StopTimer',{name:'Tea'})).error).toBe(false);
    await dismiss(page);
  });

  test('voice-requested agent end stops PCM/audio, restores ducking, preserves timer expiry, and permits a fresh wake', async ({page,ha,request},info) => {
    await policy(ha); await music(ha); await openKiosk(page,ha);
    await ha.service('voice_satellite','start_timer',{entity_id:ha.run.fixture.entity,name:'Survives conversation',seconds:10});
    const before = await stats(ha,request);
    const response = await request.post(ha.run.transport_url + '/e2e/next-audio',{headers:{Authorization:`Bearer ${ha.run.credentials.transport_token}`},data:{text:'please end session'}});
    expect(response.ok()).toBe(true);
    await ha.service('voice_satellite','wake',{entity_id:ha.run.fixture.entity});
    await expect.poll(async () => (await stats(ha,request)).end_requests).toBeGreaterThan(before.end_requests);
    await expect.poll(() => volume(ha)).toBe(0.7);
    await page.waitForFunction(() => {
      const session = document.querySelector('voice-satellite-card')._session;
      return session.isStarted && !session.audio._mediaStream && session.pipeline.binaryHandlerId === null;
    });
    const after = await stats(ha,request);
    // EndSession must use the same full dismissal as Escape, not merely unblur.
    await expect(page.locator('.vs-chat-msg')).toHaveCount(0);
    await expect(page.locator('.vs-timer-pill').filter({hasText:'Survives conversation'})).toBeVisible();
    await expect(page.locator('.vs-timer-alert')).toBeVisible({timeout:15_000});
    expect((await stats(ha,request)).input_bytes).toBe(after.input_bytes);
    await screenshot(page,info,'live-agent-ended-timer-still-alerts');
    await page.keyboard.press('Escape');
    await ha.service('voice_satellite','wake',{entity_id:ha.run.fixture.entity});
    await expect.poll(async () => (await stats(ha,request)).created).toBeGreaterThan(after.created);
    await expect(page.locator('.vs-chat-msg').filter({hasText:'Test response: received microphone audio'})).toBeVisible();
    await dismiss(page);
  });
});
