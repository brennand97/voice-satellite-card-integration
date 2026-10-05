import fs from 'node:fs';
import path from 'node:path';
import { test, expect, hass, screenshot, chromium, ws } from '@homelab/ha-testbed';

const directory = process.env.HA_TESTBED_ACOUSTICS_DIR;
const live = process.env.HA_TESTBED_LIVE_OPENAI === '1';
if (!directory) {
  test.skip('acoustic matrix: prepare synthetic corpus and set HA_TESTBED_ACOUSTICS_DIR', () => {});
} else {
  const cases = JSON.parse(fs.readFileSync(path.join(directory,'manifest.json')));
  for (const sample of cases) test(`acoustic: real microWakeWord + injected PCM ${sample.name}`, async ({ha,request},info) => {
    test.setTimeout(60_000);
    // launchOptions is worker-scoped: explicitly own a real browser per clip.
    const browser=await chromium.launch({args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required',`--use-file-for-fake-audio-capture=${sample.path}%noloop`]});
    const context=await browser.newContext({baseURL:ha.run.url,serviceWorkers:'block',permissions:['microphone'],viewport:{width:1280,height:800}});
    const page=await context.newPage();
    const stats=async () => {
      const response=await request.get(ha.run.transport_url+'/e2e/stats',{headers:{Authorization:`Bearer ${ha.run.credentials.transport_token}`}});
      expect(response.ok()).toBe(true); return response.json();
    };
    let detection=[];
    try {
      await ha.service('select','select_option',{entity_id:'select.e2e_browser_wake_word_detection',option:'On Device (microWakeWord)'});
      await ha.service('select','select_option',{entity_id:'select.e2e_browser_wake_word_1',option:'hey_jarvis'});
      await ha.service('select','select_option',{entity_id:'select.e2e_browser_wake_word_sensitivity',option:'Moderately sensitive'});
      await page.addInitScript(({fixture,tokens}) => {
        localStorage.setItem('hassTokens',JSON.stringify(tokens));
        localStorage.setItem('selectedLanguage',JSON.stringify('en'));
        localStorage.setItem('vs-satellite-entity',fixture.entity);
        localStorage.setItem('vs-panel-config',JSON.stringify(fixture.panel));
        const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia=async (...args) => {
          const stream=await capture(...args);
          window.__e2eMicStartedAt ??= Date.now();
          return stream;
        };
      },{fixture:live ? {...ha.run.fixture,panel:{...ha.run.fixture.panel,seamless_wake_command:true}} : ha.run.fixture,tokens:ha.run.credentials.tokens});
      if (!live) {
        const scripted=await request.post(ha.run.transport_url+'/e2e/next-audio',{headers:{Authorization:`Bearer ${ha.run.credentials.transport_token}`},data:{text:'test second utterance'}});
        expect(scripted.ok()).toBe(true);
      }
      const before=await stats();
      await page.goto('/lovelace/testbed'); await hass(page);
      await page.waitForFunction(() => {
        const session=document.querySelector('voice-satellite-card')?._session;
        return session?.isStarted && !session._starting && !!session.wakeWord?._inference;
      },{}, {timeout:25_000});
      // No fabricated wake event or mocked capture: shipped TFLite runs in
      // Chromium against the waveform. The fixture reply does NOT prove ASR.
      if (sample.wake_expected) {
        // Previous browser teardown can still drain transport PCM. Wait for
        // this browser's own detector, never treat global bytes as its wake.
        await expect.poll(async () => {
          detection=await page.evaluate(() => (window.__vsLogBuffer ?? []).filter(e=>e.category==='wake-word' && e.msg.startsWith('Detected:')).map(e=>({message:e.msg,seconds:(e.ts-window.__e2eMicStartedAt)/1000})));
          return detection.length;
        },{timeout:22_000}).toBeGreaterThan(0);
        await expect.poll(async () => (await stats()).input_bytes,{timeout:10_000}).toBeGreaterThan(before.input_bytes);
        expect(detection[0].seconds,'A noise-prefix false wake must not count as a correct wake').toBeGreaterThanOrEqual(sample.wake_start_seconds-0.15);
        if (live) {
          await expect.poll(async()=> (await stats()).timer_starts.length,{timeout:30_000}).toBeGreaterThan(before.timer_starts.length);
          const start=(await stats()).timer_starts.at(-1);
          expect(start.success).toBe(true);
          expect(start.duration).toEqual({hours:0,minutes:0,seconds:20});
          const response=await request.get(ha.run.transport_url+'/e2e/timers',{headers:{Authorization:`Bearer ${ha.run.credentials.transport_token}`}});
          expect(response.ok()).toBe(true);
          const truth=await response.json();
          expect(truth.timers.some(t=>/pasta/i.test(t.name) && t.seconds_remaining>0 && t.seconds_remaining<=20)).toBe(true);
        } else {
          await expect(page.locator('.vs-chat-msg').filter({hasText:'Test response: test second utterance'})).toBeVisible({timeout:10_000});
        }
      } else {
        const deadline=Date.now()+(sample.duration_seconds+3)*1000;
        await page.waitForFunction(deadline=>Date.now()>deadline,deadline,{timeout:30_000,polling:100});
        expect((await stats()).created).toBe(before.created);
        expect((await stats()).input_bytes).toBe(before.input_bytes);
      }
    } finally {
      await screenshot(page,info,'acoustic-'+sample.name);
      let logs=JSON.stringify(await page.evaluate(() => ({global:window.__vsLogBuffer,session:document.querySelector('voice-satellite-card')?._session?.pipeline?._log?.getEntries?.()})),null,2);
      const c=ha.run.credentials;
      for (const key of [c.password,c.transport_token,c.signing_key,c.tokens.access_token,c.tokens.refresh_token]) if(key) logs=logs.replaceAll(key,'[REDACTED]');
      logs=logs.replace(/([?&](?:token|authSig|signature)=)[^&\s"']+/gi,'$1[REDACTED]');
      fs.writeFileSync(info.outputPath('session-diagnostic.json'),logs);
      const result=JSON.stringify({...sample,detection,after:await stats()},null,2);
      fs.writeFileSync(info.outputPath('acoustic-result.json'),result);
      await info.attach('acoustic-result',{path:info.outputPath('acoustic-result.json'),contentType:'application/json'});
      if (live && !page.isClosed()) {
        const response=await request.get(ha.run.transport_url+'/e2e/timers',{headers:{Authorization:`Bearer ${ha.run.credentials.transport_token}`}});
        if (response.ok()) for (const timer of (await response.json()).timers) {
          if (/pasta/i.test(timer.name)) await ws(page,{type:'voice_satellite/cancel_timer',entity_id:ha.run.fixture.entity,timer_id:timer.id});
        }
      }
      await browser.close();
    }
  });
}
