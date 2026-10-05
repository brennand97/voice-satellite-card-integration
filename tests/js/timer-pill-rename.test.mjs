import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Exercise the shipped sync method without loading its browser bundle graph.
const source=readFileSync(new URL('../../src/card/ui.js',import.meta.url),'utf8');
const begin=source.indexOf('  syncTimerPills(timers, onDoubleTap) {');
assert.ok(begin>=0);
const end=source.indexOf('\n  }\n',begin)+4;
const method=vm.runInNewContext(`({${source.slice(begin,end)}})`,{truncateTimerName:s=>s});
for (const name of ['Dinner','<img src=x onerror=alert(1)>']) test(`existing timer pill refreshes name as text: ${name}`,()=>{
  const label={textContent:'Pasta'};
  const pill={_nameShown:true,_vsNameEl:label};
  const timer={id:'same-id',name,el:pill};
  const ctx={...method,_card:{config:{}},_timerPills:new Map([[timer.id,pill]]),
    _timerContainer:{contains:el=>el===pill},ensureTimerContainer(){},
    createTimerPill(){assert.fail('A simple rename must not rebuild the pill');}};
  ctx.syncTimerPills([timer],()=>()=>{});
  assert.equal(label.textContent,name);
  assert.equal(timer.el,pill);
});
