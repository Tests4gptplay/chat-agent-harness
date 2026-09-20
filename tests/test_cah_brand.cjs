'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const original = {
  v: 1, runtime_epoch: 3, desired_version: 7,
  lanes: [{lane_id: 'lane-00', display_name: 'GAH Sandbox0',
    project_key: 'g-p-examplelane00',
    project_root_url: 'https://chatgpt.com/g/g-p-examplelane00-gah-sandbox0/project',
    enabled: true, pending_tab_id: 81,
    pool_state: {project_key: 'g-p-examplelane00',
      current: {handoff_id: 'pool-keep-owner', generation: 9, handoff_verified: true},
      managed: [{handoff_id: 'pool-keep-owner'}], handoff: {status: 'verified'}, soft_limit: 5}}]
};
const store = {laneRegistry: structuredClone(original), taskCellBinding: {
  display_name: 'GAH Task Cell',
  project_root_url: 'https://chatgpt.com/g/g-p-exampletaskcell-task-cell/project',
  enabled: true
}};
const ctx = vm.createContext({URL, Date, console, chrome: {storage: {local: {
  get: async () => structuredClone(store), set: async x => Object.assign(store, structuredClone(x))
}}}});
vm.runInContext(read('extension/lane_registry.js'), ctx);
vm.runInContext(read('extension/task_cell_registry.js'), ctx);
(async () => {
  const lanes = await ctx.CAHLanes.getRegistry();
  const lane = lanes.lanes[0];
  assert.equal(lane.display_name, 'CAH Sandbox0');
  assert.equal(lane.project_root_url, 'https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project');
  assert.equal(lanes.desired_version, 7);
  assert.deepEqual(JSON.parse(JSON.stringify(lane.pool_state.current)), original.lanes[0].pool_state.current);
  assert.equal(lane.pending_tab_id, 81);
  assert.equal(lane.pool_state.managed.length, 1);
  assert.equal(store.laneRegistry.lanes[0].display_name, 'CAH Sandbox0');
  const custom = ctx.CAHLanes.normalizeLane({...original.lanes[0], display_name: 'My worker'}, 'lane-00');
  assert.equal(custom.display_name, 'My worker');
  const cell = await ctx.CAHTaskCell.getBinding();
  assert.equal(cell.display_name, 'CAH Task Cell');
  assert.equal(cell.project_root_url, 'https://chatgpt.com/g/g-p-exampletaskcell-cah-task-cell/project');
  assert.equal(cell.worker_pool, false);
  assert.equal(ctx.GAHLanes, undefined);
  assert.equal(ctx.GAHTaskCell, undefined);
  for (const target of ['chromium', 'firefox']) {
    const m = JSON.parse(read(`extension/manifest.${target}.json`));
    assert.equal(m.name, 'CAH Wake Bridge');
    assert.equal(m.version, '1.0.4');
    for (const icon of Object.values(m.icons)) assert(icon.includes('cah-icon-'));
  }
  assert(read('README.md').includes('# Chat Agent Harness (CAH)'));
  assert(read('Start_CAH.bat').includes('title CAH One-Click Start'));
  assert(read('host/create_start_shortcut.ps1').includes('Start CAH.lnk'));
  assert(read('extension/content.js').includes('GAH_WAKE'));
  console.log('PASS: CAH labels, old-root normalization, unchanged ownership/pool, custom labels, packages and launch surfaces');
})().catch(err => {console.error(err); process.exitCode = 1;});
