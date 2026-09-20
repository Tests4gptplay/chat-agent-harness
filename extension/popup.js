'use strict';
const ext = globalThis.browser || globalThis.chrome;
const lanesApi = globalThis.CAHLanes;
const taskCellApi = globalThis.CAHTaskCell;

async function getStorage(keys) { return ext.storage.local.get(keys); }
async function setStorage(value) { return ext.storage.local.set(value); }
async function send(message) { return ext.runtime.sendMessage(message); }

function status(text) {
  document.getElementById('status').textContent = typeof text === 'string' ? text : JSON.stringify(text, null, 2);
}

function topologyText(value) {
  if (!value) return 'Canonical topology: unavailable';
  const desired = Number(value.desired_count ?? 0);
  const canonical = Number(value.canonical_count ?? 0);
  const enabled = Number(value.canonical_enabled_count ?? 0);
  const phase = value.synced ? 'Synced' : (value.control_request ? 'Applying…' : 'Pending');
  return `Desired: ${desired} | Canonical: ${canonical} (${enabled} enabled) | ${phase}`;
}

function laneCard(lane) {
  const card = document.createElement('section');
  card.className = 'lane';
  card.dataset.laneId = lane.lane_id;

  const top = document.createElement('div');
  top.className = 'lane-top';

  const enabled = document.createElement('input');
  enabled.type = 'checkbox';
  enabled.className = 'lane-enabled';
  enabled.checked = lane.enabled !== false;

  const name = document.createElement('input');
  name.type = 'text';
  name.className = 'lane-name';
  name.value = lane.display_name || lane.lane_id;
  name.placeholder = 'CAH Sandbox0';

  top.append(enabled, name);

  const root = document.createElement('input');
  root.type = 'url';
  root.className = 'lane-root';
  root.value = lane.project_root_url || '';
  root.placeholder = 'https://chatgpt.com/g/g-p-.../project';

  const meta = document.createElement('div');
  meta.className = 'lane-meta';
  const pool = lane.pool_state || {};
  const current = pool.current || null;
  meta.textContent = `${lane.lane_id} | ${lane.project_key || 'unparsed'} | current: ${current ? current.conversation_id : 'none'} | managed: ${Array.isArray(pool.managed) ? pool.managed.length : 0}`;

  const buttons = document.createElement('div');
  buttons.className = 'buttons';

  const laneStatus = document.createElement('button');
  laneStatus.textContent = 'Status';
  laneStatus.addEventListener('click', async () => {
    try {
      const result = await send({ type: 'gah-lane-status', lane_id: lane.lane_id });
      status(result);
    } catch (e) { status(String(e)); }
  });

  const remove = document.createElement('button');
  remove.textContent = 'Remove';
  remove.addEventListener('click', () => {
    card.remove();
    status(`${lane.lane_id} removed from desired topology. Click Save topology to apply.`);
  });

  buttons.append(laneStatus, remove);
  card.append(top, root, meta, buttons);
  return card;
}

async function renderTaskCell() {
  if (!taskCellApi) throw new Error('Task Cell registry unavailable');
  const binding = await taskCellApi.getBinding();
  document.getElementById('taskCellEnabled').checked = binding.enabled !== false;
  document.getElementById('taskCellName').value = binding.display_name || 'CAH Task Cell';
  document.getElementById('taskCellRoot').value = binding.project_root_url || '';
  document.getElementById('taskCellMeta').textContent =
    `${binding.project_key} | roles: ${(binding.roles || []).join(', ')} | 5+1: off | release: ${binding.release_gate}`;
  return binding;
}

async function saveTaskCell() {
  if (!taskCellApi) throw new Error('Task Cell registry unavailable');
  const binding = await taskCellApi.saveBinding({
    display_name: document.getElementById('taskCellName').value.trim(),
    project_root_url: document.getElementById('taskCellRoot').value.trim(),
    enabled: document.getElementById('taskCellEnabled').checked,
  });
  await renderTaskCell();
  status({
    ok: true,
    task_cell_saved: true,
    project_key: binding.project_key,
    project_root_url: binding.project_root_url,
    worker_pool_managed: false,
    retirement_managed: false,
  });
}

async function refreshTaskCellStatus() {
  const result = await send({ type: 'gah-task-cell-status' });
  if (!result || !result.ok) throw new Error((result && result.error) || 'Task Cell status failed');
  const binding = result.binding || {};
  document.getElementById('taskCellMeta').textContent =
    `${binding.project_key || 'unbound'} | open tabs: ${Number(result.open_tab_count || 0)} | 5+1: off | release: ${binding.release_gate || 'unknown'}`;
  return result;
}

async function renderRegistry() {
  const registry = await lanesApi.getRegistry();
  const host = document.getElementById('lanes');
  host.textContent = '';
  for (const lane of registry.lanes.filter(x => !x.pending_remove)) host.appendChild(laneCard(lane));
  return registry;
}

function collectDesired() {
  const cards = [...document.querySelectorAll('.lane')];
  return cards.map(card => ({
    lane_id: card.dataset.laneId || '',
    display_name: card.querySelector('.lane-name').value.trim(),
    project_root_url: card.querySelector('.lane-root').value.trim(),
    enabled: card.querySelector('.lane-enabled').checked,
  }));
}

async function refreshTopology() {
  const result = await send({ type: 'gah-topology-status' });
  document.getElementById('topology').textContent = topologyText(result);
  return result;
}

async function load() {
  const data = await getStorage(['enabled', 'localEndpoint', 'projectId', 'clientId', 'lastError', 'lastPollAt', 'lastTransport']);
  document.getElementById('enabled').checked = data.enabled !== false;
  document.getElementById('localEndpoint').value = data.localEndpoint || lanesApi.DEFAULT_ENDPOINT;
  document.getElementById('projectId').value = data.projectId || lanesApi.DEFAULT_PROJECT_ID;
  await renderTaskCell();
  await renderRegistry();
  let taskCell = null;
  let topo = null;
  try { taskCell = await refreshTaskCellStatus(); } catch (_) {}
  try { topo = await refreshTopology(); } catch (_) {}
  status({
    clientId: data.clientId || null,
    lastPollAt: data.lastPollAt || null,
    lastTransport: data.lastTransport || null,
    lastError: data.lastError || null,
    taskCell,
    topology: topo,
  });
}

async function addLane() {
  const registry = await lanesApi.getRegistry();
  const currentCards = collectDesired();
  const laneId = lanesApi.nextLaneId([...registry.lanes, ...currentCards]);
  document.getElementById('lanes').appendChild(laneCard({
    lane_id: laneId,
    display_name: `CAH Sandbox${Number(laneId.split('-')[1]) || 0}`,
    project_key: '',
    project_root_url: '',
    enabled: true,
    pool_state: { managed: [], current: null },
  }));
}

async function saveTopology() {
  const enabled = document.getElementById('enabled').checked;
  const localEndpoint = document.getElementById('localEndpoint').value.trim() || lanesApi.DEFAULT_ENDPOINT;
  const projectId = document.getElementById('projectId').value.trim() || lanesApi.DEFAULT_PROJECT_ID;
  await setStorage({ enabled, localEndpoint, projectId });

  const desiredInput = collectDesired();
  // Validate roots and persist desired local registry while preserving runtime pool state.
  const registry = await lanesApi.applyDesired(desiredInput);
  const desired = lanesApi.publicDesired(registry);
  if (!desired.length) {
    throw new Error('At least one lane entry is required for this migration build; disable/remove the final lane only after the control-worker shutdown path is live-proven.');
  }

  const result = await send({
    type: 'gah-topology-save',
    desired_lanes: desired,
    desired_version: registry.desired_version,
  });
  if (!result || !result.ok) throw new Error((result && result.error) || 'Topology save failed');
  await send({ type: 'gah-reschedule' });
  await renderRegistry();
  await refreshTopology();
  status(result);
}

document.getElementById('saveTaskCell').addEventListener('click', () => saveTaskCell().catch(e => status(String(e))));
document.getElementById('taskCellStatus').addEventListener('click', () => refreshTaskCellStatus().then(status).catch(e => status(String(e))));
document.getElementById('openTaskCell').addEventListener('click', () => send({ type: 'gah-task-cell-open' }).then(status).catch(e => status(String(e))));
document.getElementById('addLane').addEventListener('click', () => addLane().catch(e => status(String(e))));
document.getElementById('save').addEventListener('click', () => saveTopology().catch(e => status(String(e))));
document.getElementById('refresh').addEventListener('click', () => Promise.all([renderTaskCell(), refreshTaskCellStatus(), renderRegistry(), refreshTopology()]).catch(e => status(String(e))));
document.getElementById('poll').addEventListener('click', () => send({ type: 'gah-poll-now' }).then(status).catch(e => status(String(e))));
load().catch(e => status(String(e)));
