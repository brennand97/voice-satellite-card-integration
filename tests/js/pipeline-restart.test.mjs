import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the actual methods without importing the browser-only bundle graph.
const source=readFileSync(new URL('../../src/pipeline/index.js',import.meta.url),'utf8');
function fixture(signature) {
  const begin=source.indexOf(`  ${signature} {`);
  assert.ok(begin>=0);
  const end=source.indexOf('\n  }\n',begin)+4;
  const timers=[]; const stopped=[]; const actions=[];
  const method=vm.runInNewContext(`({${source.slice(begin,end)}})`,{
    getWakeWordMode:()=> 'disabled',WAKE_MODE_DISABLED:'disabled',
    State:{IDLE:'idle'},setTimeout:fn=>{timers.push(fn);return timers.length;},
    resumeNativeWake:()=>Promise.resolve(),
  });
  const ctx={...method,_pipelineGen:0,_isRestarting:false,_isDetectionDisabled:()=>true,_log:{log(){}},
    _card:{setState:state=>actions.push(state),audio:{stopMicrophone:()=>actions.push('mic stopped')}},
    stop(){this._pipelineGen++;this._isRestarting=false;return new Promise(resolve=>stopped.push(resolve));},
    start:()=>actions.push('pipeline started'),
  };
  return {ctx,timers,stopped,actions};
}

test('late idle restart cannot stop a newer manual-wake microphone',async()=>{
  const {ctx,timers,stopped,actions}=fixture('restart(delay)');
  ctx.restart(0); ctx.stop(); stopped[0](); await Promise.resolve();
  assert.equal(timers.length,0); assert.deepEqual(actions,[]);
});
test('already-queued restart callback is fenced by the latest generation',async()=>{
  const {ctx,timers,stopped,actions}=fixture('restart(delay)');
  ctx.restart(0); stopped[0](); await Promise.resolve(); ctx.stop(); timers[0]();
  assert.deepEqual(actions,[]);
});
test('current idle restart still releases the microphone',async()=>{
  const {ctx,timers,stopped,actions}=fixture('restart(delay)');
  ctx.restart(0); stopped[0](); await Promise.resolve(); timers[0]();
  assert.deepEqual(actions,['mic stopped','idle']);
});
test('late continue restart cannot replace a newer user turn',async()=>{
  const {ctx,stopped,actions}=fixture('restartContinue(conversationId, opts = {})');
  ctx.restartContinue(); ctx.stop(); stopped[0](); await Promise.resolve();
  assert.deepEqual(actions,[]);
});
