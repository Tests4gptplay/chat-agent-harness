'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const transport = require('../extension/control_transport.js');
const source = fs.readFileSync(require.resolve('../extension/background.js'), 'utf8')
  .replace(/reschedule\(\);\s*runMaintenanceTick\(\);\s*$/, '');
function context() {
  const events = {}, stored = {};
  const ext = {
    runtime: {onInstalled:{addListener:f=>events.installed=f}, onStartup:{addListener:f=>events.startup=f}, onMessage:{addListener:()=>{}}},
    alarms: {get:async name=>({periodInMinutes:name==='gah-wake-poll'?1:.5}), create:async()=>{}, clear:async()=>{}, onAlarm:{addListener:f=>events.alarm=f}},
    storage: {local:{get:async()=>stored, set:async v=>Object.assign(stored,v), remove:async k=>delete stored[k]}}
  };
  const c = vm.createContext({chrome:ext, CAHControlTransport:transport, CAHLanes:{getRegistry:async()=>({lanes:[]})}, URL, console, setTimeout, clearTimeout});
  vm.runInContext(source,c);
  c.config=async()=>({enabled:true,pollMinutes:.5,clientId:'client-test'});
  return {c,ext,events,stored};
}
const settle=()=>new Promise(r=>setImmediate(r));
test('service-worker wake retains an existing alarm instead of postponing delivery',async()=>{
  const {c,ext}=context();let creates=0,clears=0;
  ext.alarms.create=async()=>creates++;ext.alarms.clear=async()=>clears++;
  await c.reschedule();await c.reschedule();
  assert.equal(creates,0);assert.equal(clears,0);
  ext.alarms.get=async()=>null;await c.reschedule();assert.equal(creates,2);
  c.config=async()=>({enabled:false});await c.reschedule();assert.equal(clears,2);
});
test('new polling starts while admission repair hangs; duplicate repair ticks coalesce',async()=>{
  const {c}=context();let polls=0,repairs=0,liveness=0;
  c.pollEnabledLanes=async()=>polls++;
  c.extensionRuntimeMaintenance=async()=>{};c.maintainParallelBootstrap=async()=>{};
  c.reconcilePendingAdmissions=()=>{repairs++;return new Promise(()=>{});};
  c.semanticLivenessSweep=()=>{liveness++;return new Promise(()=>{});};
  c.runMaintenanceTick();await settle();c.runMaintenanceTick();await settle();
  assert.equal(polls,2);assert.equal(repairs,1);assert.equal(liveness,1);
});
test('installed update halves former default interval without changing deliberate custom intervals',async()=>{
  const {c,events,stored}=context();c.runMaintenanceTick=()=>{};
  stored.pollMinutes=1;await events.installed();assert.equal(stored.pollMinutes,.5);
  stored.pollMinutes=3;await events.installed();assert.equal(stored.pollMinutes,3);
});
const ctx={task_id:'task-one',backend_cl:'cl/task-one.json',dispatch_id:'dispatch-one',dispatch_generation:1,fence_token:'fence-one'};
const worker={status:'current',handoff_verified:true,handoff_id:'pool-current'};
const lane={lane_id:'lane-00',project_key:'g-p-test',enabled:true,pool_state:{current:worker}};
test('known admitted work skips redundant dispatch-status preflight',async()=>{
  const {c,stored}=context();
  stored[transport.key(ctx)]={context:ctx,phase:'ADMITTED',lane_id:lane.lane_id,worker_project_key:lane.project_key,worker_ref:worker.handoff_id};
  c.localApi=async()=>{throw new Error('redundant RPC');};
  const result=await c.ensureAdmissionForContext({},ctx,lane,worker,42);
  assert.equal(result.admission_cached,true);
});
test('admission cache does not cover a different Worker owner',async()=>{
  const {c,stored}=context();let reads=0;
  stored[transport.key(ctx)]={context:ctx,phase:'ADMITTED',lane_id:lane.lane_id,worker_project_key:lane.project_key,worker_ref:'pool-old'};
  c.localApi=async()=>{reads++;return {matched:false};};
  await assert.rejects(c.ensureAdmissionForContext({},ctx,lane,worker,42),/DISPATCH_STALE/);
  assert.equal(reads,1);
});
test('terminal dispatch stops idle Git probes; a new generation is still checked',async()=>{
  const {c}=context();let calls=0;let current={...ctx};
  c.CAHLanes.getRegistry=async()=>({lanes:[lane]});
  c.findCurrentWorkerTab=async()=>({id:42});
  c.tabsSendMessage=async()=>({dispatch_context:current,response_running:false});
  c.ensureAdmissionForContext=async()=>({ok:true});
  c.localApi=async()=>{calls++;return {ok:true,matched:true,state:'DONE'};};
  await c.semanticLivenessSweep();await c.semanticLivenessSweep();assert.equal(calls,1);
  current={...ctx,dispatch_generation:2,dispatch_id:'dispatch-two',fence_token:'fence-two'};
  await c.semanticLivenessSweep();assert.equal(calls,2);
});
test('fast mailbox alarm does not increase lifecycle maintenance rate',async()=>{
  const {c,events}=context();let polls=0,maintenance=0;
  c.pollEnabledLanes=async()=>polls++;c.runMaintenanceTick=()=>maintenance++;
  events.alarm({name:'cah-fast-wake-poll'});await settle();
  assert.equal(polls,1);assert.equal(maintenance,0);
});

test('unconfigured or disabled extension does not start maintenance', async()=>{
  const {c}=context();let calls=0;c.config=async()=>({enabled:false});
  for (const name of ['pollEnabledLanes','extensionRuntimeMaintenance','maintainParallelBootstrap','reconcilePendingAdmissions','semanticLivenessSweep']) c[name]=async()=>calls++;
  await c.runMaintenanceTick();assert.equal(calls,0);
});
