'use strict';

const ext = globalThis.browser || globalThis.chrome;
const lanesApi = globalThis.CAHLanes;
const taskCellApi = globalThis.CAHTaskCell;
const transportApi = globalThis.CAHControlTransport;
const uiRecoveryApi = globalThis.CAHUiRecovery;
const ALARM = 'gah-wake-poll';
const POLL_ALARM = 'cah-fast-wake-poll';
const DEFAULTS = Object.freeze({
  enabled: '__CAH_CONFIGURED__' === 'yes',
  localEndpoint: lanesApi ? lanesApi.DEFAULT_ENDPOINT : 'http://127.0.0.1:8765/api',
  projectId: lanesApi ? lanesApi.DEFAULT_PROJECT_ID : 'git-agent-harness',
  pollMinutes: 0.5,
  leaseSeconds: 120,
  clientId: '',
  lastError: '',
  lastPollAt: '',
  lastTransport: '',
});

function maybePromise(value, fallback) {
  if (value && typeof value.then === 'function') return value;
  return fallback();
}
function storageGet(keys) {
  try { return maybePromise(ext.storage.local.get(keys), () => new Promise(r => ext.storage.local.get(keys, r))); }
  catch (_) { return new Promise(r => ext.storage.local.get(keys, r)); }
}
function storageSet(value) {
  try { return maybePromise(ext.storage.local.set(value), () => new Promise(r => ext.storage.local.set(value, r))); }
  catch (_) { return new Promise(r => ext.storage.local.set(value, r)); }
}
function storageRemove(keys) {
  try { return maybePromise(ext.storage.local.remove(keys), () => new Promise(r => ext.storage.local.remove(keys, r))); }
  catch (_) { return new Promise(r => ext.storage.local.remove(keys, r)); }
}
function tabsQuery(query) {
  try { return maybePromise(ext.tabs.query(query), () => new Promise(r => ext.tabs.query(query, r))); }
  catch (_) { return new Promise(r => ext.tabs.query(query, r)); }
}
function tabsGet(id) {
  try {
    return maybePromise(ext.tabs.get(id), () => new Promise((resolve, reject) => {
      ext.tabs.get(id, tab => {
        const err = globalThis.chrome && chrome.runtime && chrome.runtime.lastError;
        if (err) reject(new Error(err.message)); else resolve(tab);
      });
    }));
  } catch (_) {
    return new Promise((resolve, reject) => ext.tabs.get(id, tab => {
      const err = globalThis.chrome && chrome.runtime && chrome.runtime.lastError;
      if (err) reject(new Error(err.message)); else resolve(tab);
    }));
  }
}
function tabsCreate(create) {
  try { return maybePromise(ext.tabs.create(create), () => new Promise(r => ext.tabs.create(create, r))); }
  catch (_) { return new Promise(r => ext.tabs.create(create, r)); }
}
function tabsRemove(id) {
  try { return maybePromise(ext.tabs.remove(id), () => new Promise(r => ext.tabs.remove(id, r))); }
  catch (_) { return new Promise(r => ext.tabs.remove(id, r)); }
}
function tabsReload(id) {
  try { return maybePromise(ext.tabs.reload(id), () => new Promise(r => ext.tabs.reload(id, {}, r))); }
  catch (_) { return new Promise(r => ext.tabs.reload(id, {}, r)); }
}
function tabsUpdate(id, update) {
  try { return maybePromise(ext.tabs.update(id, update), () => new Promise(r => ext.tabs.update(id, update, r))); }
  catch (_) { return new Promise(r => ext.tabs.update(id, update, r)); }
}
function tabsSendMessage(id, message) {
  try {
    return maybePromise(ext.tabs.sendMessage(id, message), () => new Promise((resolve, reject) => {
      ext.tabs.sendMessage(id, message, response => {
        const err = globalThis.chrome && chrome.runtime && chrome.runtime.lastError;
        if (err) reject(new Error(err.message)); else resolve(response);
      });
    }));
  } catch (_) {
    return new Promise((resolve, reject) => ext.tabs.sendMessage(id, message, response => {
      const err = globalThis.chrome && chrome.runtime && chrome.runtime.lastError;
      if (err) reject(new Error(err.message)); else resolve(response);
    }));
  }
}

async function config() {
  const data = await storageGet(Object.keys(DEFAULTS));
  const cfg = { ...DEFAULTS, ...data };
  if (!cfg.clientId) {
    cfg.clientId = (globalThis.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    await storageSet({ clientId: cfg.clientId });
  }
  return cfg;
}

const admissionJournal = transportApi
  ? transportApi.journal({ get: storageGet, set: storageSet, remove: storageRemove })
  : null;
const pollGate = transportApi ? transportApi.laneGate() : null;

async function lastSubmittedWake(laneId) {
  if (!transportApi) return null;
  const key = transportApi.laneStorageKey(laneId);
  const value = await storageGet(key);
  return value && value[key] ? String(value[key]) : null;
}

async function setLastSubmittedWake(laneId, wakeId) {
  if (!transportApi) return;
  const key = transportApi.laneStorageKey(laneId);
  await storageSet({ [key]: String(wakeId || '') });
}

async function localApi(cfg, payload, options = {}) {
  if (!transportApi) throw new Error('CAH control transport is unavailable');
  return transportApi.call(cfg.localEndpoint || DEFAULTS.localEndpoint, payload, options);
}

async function report(cfg, event, level = 'info', data = {}) {
  if (!cfg.clientId) return;
  try {
    await localApi(cfg, {
      op: 'event',
      client_id: cfg.clientId,
      project_id: cfg.projectId,
      event, level, data,
    });
  } catch (_) {}
}

function marker(wake) {
  const project = String(wake.project_id || DEFAULTS.projectId).replace(/\s+/g, '_');
  return `GAH_WAKE v=1 id=${wake.wake_id} project=${project}`;
}

function wakeText(wake) {
  const lines = [marker(wake)];
  const dispatchFields = [
    wake.backend_cl, wake.dispatch_id, wake.dispatch_generation, wake.fence_token,
  ];
  if (dispatchFields.every(value => value !== undefined && value !== null && String(value) !== '')) {
    lines.push(
      `GAH_DISPATCH task_id=${String(wake.task_id || '')} backend_cl=${String(wake.backend_cl)} ` +
      `dispatch_id=${String(wake.dispatch_id)} generation=${Number(wake.dispatch_generation)} ` +
      `fence_token=${String(wake.fence_token)}`,
      'GAH_RUNTIME_ACK mode=response-start do_not_write_standalone_ack=true; the runtime records RUNNING after this response begins. Do the semantic work in this same response and write only the task-declared semantic output artifact.'
    );
  }
  if (wake.kind) lines.push(`GAH_EVENT kind=${String(wake.kind)}`);
  if (wake.result_ref) lines.push(`GAH_RESULT ref=${String(wake.result_ref)}`);
  if (wake.attachment_ref) lines.push(`GAH_ATTACHMENT ref=${String(wake.attachment_ref)}`);
  return lines.join('\n');
}

async function dispatchSuppression(cfg, wake) {
  const fields = [wake.backend_cl, wake.dispatch_id, wake.dispatch_generation, wake.fence_token];
  const present = fields.map(value => value !== undefined && value !== null && String(value) !== '');
  if (!present.some(Boolean)) return { applicable: false, suppress: false };
  if (!present.every(Boolean)) throw new Error('Dispatch-aware wake is missing scheduler identity fields');
  const status = await localApi(cfg, {
    op: 'dispatch_status',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
    task_id: wake.task_id || '',
    backend_cl: wake.backend_cl,
    dispatch_id: wake.dispatch_id,
    dispatch_generation: Number(wake.dispatch_generation),
    fence_token: wake.fence_token,
  });
  return { applicable: true, ...status };
}

function conversationLocation(lane, url) {
  try {
    const u = new URL(url || '');
    if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return null;
    const prefix = `/g/${lane.project_key}`;
    if (!u.pathname.startsWith(prefix)) return null;
    const rest = u.pathname.slice(prefix.length);
    const m = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
    return m ? { conversation_id: m[1], url: u.toString() } : null;
  } catch (_) { return null; }
}

function managedLaneLocation(lane, url) {
  try {
    const u = new URL(url || '');
    if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return false;
    const prefix = `/g/${lane.project_key}`;
    if (!u.pathname.startsWith(prefix)) return false;
    const next = u.pathname.slice(prefix.length, prefix.length + 1);
    return next === '/' || next === '-';
  } catch (_) { return false; }
}

async function waitForContentPing(tabId, timeoutMs = 15000) {
  const started = Date.now();
  let last = '';
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await tabsSendMessage(tabId, { type: 'gah-content-ping' });
      if (response && response.ok) return response;
    } catch (err) {
      last = String(err && err.message || err);
    }
    await new Promise(r => setTimeout(r, 300));
  }
  throw new Error(`managed tab content script did not become ready${last ? ': ' + last : ''}`);
}

async function rehydrateManagedTabs() {
  const registry = await lanesApi.getRegistry();
  const lanes = (registry.lanes || []).filter(lane => lane && lane.project_key);
  const tabs = await tabsQuery({ url: ['https://chatgpt.com/*'] });
  const targets = tabs.filter(tab =>
    tab && tab.id && lanes.some(lane => managedLaneLocation(lane, tab.url))
  );

  let ready = 0;
  let failed = 0;
  const failures = [];
  for (const tab of targets) {
    try {
      await tabsReload(tab.id);
      await waitForContentPing(tab.id, 20000);
      ready += 1;
    } catch (err) {
      failed += 1;
      failures.push({
        tab_id: tab.id,
        message: String(err && err.message || err).slice(0, 500),
      });
    }
  }
  return { total: targets.length, ready, failed, failures };
}

async function markCurrentWorkerStale(lane, expectedId, reason) {
  const registry = await lanesApi.getRegistry();
  const fresh = (registry.lanes || []).find(x => x.lane_id === lane.lane_id && x.project_key === lane.project_key);
  if (!fresh) return;
  const pool = fresh.pool_state || lanesApi.emptyPool(fresh);
  const current = pool.current;
  if (!current || String(current.conversation_id || '') !== expectedId) return;

  const managed = (Array.isArray(pool.managed) ? pool.managed : []).filter(item =>
    !(item && String(item.conversation_id || '') === expectedId)
  );
  fresh.pool_state = {
    ...pool,
    current: null,
    managed,
    handoff: null,
    updated_at: new Date().toISOString(),
    stale_current: {
      conversation_id: expectedId,
      detected_at: new Date().toISOString(),
      reason,
    },
  };
  fresh.pending_tab_id = null;
  registry.lanes = registry.lanes.map(x => x.lane_id === fresh.lane_id ? fresh : x);
  await lanesApi.saveRegistry(registry);
}

async function waitForExactConversationRoute(tabId, lane, expectedId, timeoutMs = 10000) {
  const started = Date.now();
  let lastUrl = '';
  let sawProjectRoot = false;
  while (Date.now() - started < timeoutMs) {
    let tab;
    try { tab = await tabsGet(tabId); } catch (_) { return { ok: false, reason: 'tab_disappeared', last_url: lastUrl }; }
    if (!tab || !tab.id) return { ok: false, reason: 'tab_disappeared', last_url: lastUrl };
    lastUrl = String(tab.url || '');
    const loc = conversationLocation(lane, lastUrl);
    if (loc && loc.conversation_id === expectedId) {
      // Require the exact route to persist briefly so an intermediate navigation
      // does not count as a live Worker.
      await new Promise(r => setTimeout(r, 600));
      const again = await tabsGet(tabId).catch(() => null);
      const againLoc = again && conversationLocation(lane, again.url);
      if (again && againLoc && againLoc.conversation_id === expectedId) {
        return { ok: true, tab: again };
      }
      continue;
    }
    try {
      const u = new URL(lastUrl);
      if (u.hostname === 'chatgpt.com'
          && u.pathname.startsWith(`/g/${lane.project_key}`)
          && /\/project\/?$/.test(u.pathname)) {
        sawProjectRoot = true;
        // ChatGPT's deleted-conversation fallback settles on the Project root.
        // Give it a short grace period in case this is only an intermediate route.
        await new Promise(r => setTimeout(r, 900));
        const again = await tabsGet(tabId).catch(() => null);
        if (again) {
          const exactAgain = conversationLocation(lane, again.url);
          if (exactAgain && exactAgain.conversation_id === expectedId) continue;
          const u2 = new URL(again.url || '');
          if (u2.pathname.startsWith(`/g/${lane.project_key}`) && /\/project\/?$/.test(u2.pathname)) {
            return { ok: false, reason: 'deleted_or_root_fallback', last_url: again.url || lastUrl };
          }
        }
      }
    } catch (_) {}
    await new Promise(r => setTimeout(r, 250));
  }
  return { ok: false, reason: sawProjectRoot ? 'deleted_or_root_fallback' : 'exact_route_timeout', last_url: lastUrl };
}

async function findCurrentWorkerTab(lane) {
  const pool = lane.pool_state || lanesApi.emptyPool(lane);
  const current = pool.current;
  if (!current || current.status !== 'current' || current.handoff_verified !== true) return null;
  const expectedId = String(current.conversation_id || '');
  const loc = conversationLocation(lane, current.url);
  if (!loc || loc.conversation_id !== expectedId) {
    await markCurrentWorkerStale(lane, expectedId, 'stored_current_url_invalid');
    return null;
  }

  const tabs = await tabsQuery({ url: ['https://chatgpt.com/*'] });
  const found = tabs.find(tab => {
    const x = tab && conversationLocation(lane, tab.url);
    return x && x.conversation_id === expectedId;
  });
  if (found && found.id) {
    const verified = await waitForExactConversationRoute(found.id, lane, expectedId, 2500);
    if (verified.ok) return verified.tab;
  }

  const created = await tabsCreate({ url: current.url, active: false });
  if (!created || !created.id) {
    await markCurrentWorkerStale(lane, expectedId, 'current_worker_open_failed');
    return null;
  }
  const verified = await waitForExactConversationRoute(created.id, lane, expectedId, 10000);
  if (verified.ok) return verified.tab;

  try { await tabsRemove(created.id); } catch (_) {}
  await markCurrentWorkerStale(lane, expectedId, verified.reason || 'current_worker_route_invalid');
  const cfg = await config().catch(() => null);
  if (cfg) {
    await report(cfg, 'worker.current_stale', 'warn', {
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
      conversation_id: expectedId,
      reason: verified.reason || 'unknown',
      last_url: String(verified.last_url || '').slice(0, 500),
    });
  }
  return null;
}

function admissionContextFromWake(wake) {
  const fields = [wake && wake.backend_cl, wake && wake.dispatch_id, wake && wake.dispatch_generation, wake && wake.fence_token];
  if (!fields.every(value => value !== undefined && value !== null && String(value) !== '')) return null;
  const ctx = {
    task_id: String(wake.task_id || ''),
    backend_cl: String(wake.backend_cl),
    dispatch_id: String(wake.dispatch_id),
    dispatch_generation: Number(wake.dispatch_generation),
    fence_token: String(wake.fence_token),
  };
  try { transportApi.identity(ctx); return ctx; } catch (_) { return null; }
}

function sameAdmissionContext(left, right) {
  try { return transportApi.identity(left) === transportApi.identity(right); }
  catch (_) { return false; }
}

function admissionLane(registry, record) {
  return (registry.lanes || []).find(lane =>
    lane
    && String(lane.lane_id || '') === String(record.lane_id || '')
    && String(lane.project_key || '') === String(record.worker_project_key || '')
  ) || null;
}

function verifiedAdmissionWorker(lane, record) {
  const pool = lane && (lane.pool_state || lanesApi.emptyPool(lane));
  const current = pool && pool.current;
  if (!current || current.status !== 'current' || current.handoff_verified !== true) return null;
  if (String(current.handoff_id || '') !== String(record.worker_ref || '')) return null;
  return current;
}

async function reconcileAdmissionRecord(cfg, record) {
  if (!admissionJournal || !record || !record.context || record.phase !== 'STARTED') return record;
  const updated = await admissionJournal.reconcile(record.context, async currentRecord => localApi(cfg, {
    op: 'dispatch_accept',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
    task_id: currentRecord.context.task_id,
    backend_cl: currentRecord.context.backend_cl,
    dispatch_id: currentRecord.context.dispatch_id,
    dispatch_generation: Number(currentRecord.context.dispatch_generation),
    fence_token: currentRecord.context.fence_token,
    worker_ref: String(currentRecord.worker_ref || ''),
    lane_id: String(currentRecord.lane_id || ''),
    worker_project_key: String(currentRecord.worker_project_key || ''),
  }));
  if (!updated) return updated;
  const event = updated.phase === 'ADMITTED'
    ? 'dispatch.admission_complete'
    : updated.phase === 'REJECTED'
      ? 'dispatch.admission_rejected'
      : 'dispatch.admission_pending';
  await report(cfg, event, updated.phase === 'REJECTED' ? 'error' : updated.phase === 'ADMITTED' ? 'info' : 'warn', {
    task_id: updated.context && updated.context.task_id || null,
    dispatch_id: updated.context && updated.context.dispatch_id || null,
    generation: updated.context && updated.context.dispatch_generation || null,
    lane_id: updated.lane_id || null,
    worker_ref: updated.worker_ref || null,
    attempts: Number(updated.attempts || 0),
    phase: updated.phase || null,
    last_error: updated.last_error || null,
    next_retry_at: updated.next_retry_at || null,
  });
  return updated;
}

async function observePreparedAdmission(cfg, record, registry) {
  if (!record || record.phase !== 'PREPARED' || !record.context) return record;
  const lane = admissionLane(registry, record);
  const current = lane && verifiedAdmissionWorker(lane, record);
  if (!lane || !current) {
    return admissionJournal.reject(record.context, 'DISPATCH_WORKER_MISMATCH');
  }
  const tab = await findCurrentWorkerTab(lane);
  if (!tab || !tab.id || !record.wake_marker) return record;
  let probe;
  try {
    probe = await tabsSendMessage(tab.id, { type: 'gah-probe-wake-marker', marker: String(record.wake_marker) });
  } catch (_) {
    return record;
  }
  if (!probe || probe.ok !== true || !probe.marker_visible || !probe.response_started) return record;
  if (probe.dispatch_context && !sameAdmissionContext(probe.dispatch_context, record.context)) {
    return admissionJournal.reject(record.context, 'DISPATCH_STALE_OBSERVED_MARKER_CONTEXT');
  }
  const started = await admissionJournal.started(record.context, {
    observed_at: new Date().toISOString(),
    tab_id: tab.id,
    observation: 'exact_marker_and_assistant',
  });
  await report(cfg, 'dispatch.admission_start_recovered', 'warn', {
    task_id: record.context.task_id,
    dispatch_id: record.context.dispatch_id,
    lane_id: lane.lane_id,
    worker_ref: current.handoff_id || null,
    tab_id: tab.id,
  });
  return started;
}

async function reconcilePendingAdmissions() {
  if (!admissionJournal) return { ok: true, pending: 0, reconciled: 0 };
  const cfg = await config();
  const registry = await lanesApi.getRegistry();
  const pending = await admissionJournal.pending();
  let reconciled = 0;
  for (const original of pending) {
    let record = original;
    const lane = admissionLane(registry, record);
    if (!lane || !verifiedAdmissionWorker(lane, record)) {
      await admissionJournal.reject(record.context, 'DISPATCH_WORKER_MISMATCH');
      continue;
    }
    if (record.phase === 'PREPARED') record = await observePreparedAdmission(cfg, record, registry);
    if (record && record.phase === 'STARTED') {
      const after = await reconcileAdmissionRecord(cfg, record);
      if (after && after.phase === 'ADMITTED') reconciled += 1;
    }
  }
  return { ok: true, pending: pending.length, reconciled };
}

async function ensureAdmissionForContext(cfg, ctx, lane, current, tabId = null) {
  if (!admissionJournal || !transportApi) return { ok: true, legacy: true };
  transportApi.identity(ctx);
  if (!lane || !current || current.status !== 'current' || current.handoff_verified !== true) {
    throw new Error('DISPATCH_WORKER_MISMATCH');
  }
  const meta = {
    lane_id: lane.lane_id,
    worker_project_key: lane.project_key,
    worker_ref: String(current.handoff_id || ''),
    project_id: cfg.projectId,
    tab_id: tabId || null,
  };
  let record = await admissionJournal.read(ctx);
  // Admission was already persisted. The following syscall/liveness RPC still
  // checks current canonical identity; do not fetch it twice for this preflight.
  if (record && record.phase === 'ADMITTED' && sameAdmissionContext(record.context, ctx) &&
      record.lane_id === lane.lane_id && record.worker_project_key === lane.project_key &&
      record.worker_ref === meta.worker_ref) return { ok: true, admission_cached: true };
  if (record && record.phase === 'PREPARED') {
    const registry = await lanesApi.getRegistry();
    record = await observePreparedAdmission(cfg, record, registry);
  }

  let status = await localApi(cfg, {
    op: 'dispatch_status',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
    task_id: ctx.task_id,
    backend_cl: ctx.backend_cl,
    dispatch_id: ctx.dispatch_id,
    dispatch_generation: Number(ctx.dispatch_generation),
    fence_token: ctx.fence_token,
  });
  if (!status.matched) throw new Error('DISPATCH_STALE');

  const pendingStates = new Set(['READY', 'DISPATCHED', 'ACKED']);
  if (!record && pendingStates.has(String(status.state || '')) && tabId) {
    const ping = await tabsSendMessage(tabId, { type: 'gah-content-ping' }).catch(() => null);
    if (ping && ping.ok && ping.wake_marker && sameAdmissionContext(ping.dispatch_context, ctx)) {
      const prepared = await admissionJournal.prepare(ctx, { ...meta, wake_marker: String(ping.wake_marker) });
      record = prepared.record;
      const probe = await tabsSendMessage(tabId, { type: 'gah-probe-wake-marker', marker: String(ping.wake_marker) }).catch(() => null);
      if (probe && probe.ok && probe.marker_visible && probe.response_started && sameAdmissionContext(probe.dispatch_context, ctx)) {
        record = await admissionJournal.started(ctx, {
          ...meta,
          observed_at: new Date().toISOString(),
          observation: 'verified_current_worker_dispatch',
        });
      }
    }
  }

  if (record && record.phase === 'STARTED') await reconcileAdmissionRecord(cfg, record);
  if (pendingStates.has(String(status.state || ''))) {
    status = await localApi(cfg, {
      op: 'dispatch_status',
      client_id: cfg.clientId,
      project_id: cfg.projectId,
      task_id: ctx.task_id,
      backend_cl: ctx.backend_cl,
      dispatch_id: ctx.dispatch_id,
      dispatch_generation: Number(ctx.dispatch_generation),
      fence_token: ctx.fence_token,
    });
  }
  if (pendingStates.has(String(status.state || ''))) {
    const error = new Error('DISPATCH_ADMISSION_PENDING');
    error.code = 'DISPATCH_ADMISSION_PENDING';
    throw error;
  }
  return status;
}

function topologyComparable(lanes) {
  return (lanes || []).map(x => ({
    lane_id: String(x.lane_id || ''),
    display_name: String(x.display_name || ''),
    project_key: String(x.project_key || ''),
    project_root_url: String(x.project_root_url || '').replace(/\/$/, ''),
    enabled: Boolean(x.enabled),
  })).sort((a,b) => a.lane_id.localeCompare(b.lane_id));
}

function sameTopology(a, b) {
  return JSON.stringify(topologyComparable(a)) === JSON.stringify(topologyComparable(b));
}

function runtimeTopologySummary(registry) {
  const lanes = (registry && registry.lanes) || [];
  return {
    desired_version: Number(registry && registry.desired_version || 0),
    desired_lane_count: lanes.filter(lane => !lane.pending_remove).length,
    desired_enabled_count: lanes.filter(lane => !lane.pending_remove && lane.enabled).length,
    lane_runtime: lanes.filter(lane => !lane.pending_remove).map(lane => {
      const pool = lane.pool_state || lanesApi.emptyPool(lane);
      const current = pool.current || null;
      return {
        lane_id: lane.lane_id,
        project_key: lane.project_key,
        enabled: Boolean(lane.enabled),
        current_verified: Boolean(current && current.status === 'current' && current.handoff_verified === true),
        managed_count: Array.isArray(pool.managed) ? pool.managed.length : 0,
        handoff_status: pool.handoff ? String(pool.handoff.status || '') : null,
      };
    }),
    bootstrap_parallel_status: registry && registry.bootstrap_parallel
      ? String(registry.bootstrap_parallel.status || '')
      : null,
  };
}


async function resolveWakeLane(wake, registry) {
  const laneId = String(wake.lane_id || '');
  const key = String(wake.worker_project_key || '');
  if (laneId || key) {
    if (!laneId || !key) throw new Error('Lane-addressed wake must include lane_id and worker_project_key together');
    const lane = lanesApi.findLane(registry, laneId, key);
    if (!lane) throw new Error(`Wake targets unregistered lane ${laneId}/${key}`);
    return { lane, legacy: false };
  }
  const desired = lanesApi.publicDesired(registry);
  const lane = lanesApi.controlLane(registry, desired);
  if (!lane) throw new Error('Legacy wake has no available control lane');
  return { lane, legacy: true };
}

async function routeWake(cfg, claim, registry) {
  const wake = claim.wake;
  const resolved = await resolveWakeLane(wake, registry);
  const lane = resolved.lane;
  if ((!lane.enabled && !lane.pending_remove) || (lane.pending_remove && wake.kind !== 'topology_reconcile')) {
    throw new Error(`Lane ${lane.lane_id} is not eligible for this wake`);
  }

  const dispatchGate = await dispatchSuppression(cfg, wake);
  if (dispatchGate.applicable && dispatchGate.suppress) {
    await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id });
    await report(cfg, 'wake.suppressed', 'info', {
      wake_id: wake.wake_id,
      lane_id: lane.lane_id,
      dispatch_id: wake.dispatch_id || null,
      dispatch_generation: wake.dispatch_generation || null,
      scheduler_state: dispatchGate.state || null,
      reason: dispatchGate.reason || 'scheduler_suppressed',
    });
    return {
      ok: true,
      suppressed: true,
      wake_id: wake.wake_id,
      lane_id: lane.lane_id,
      scheduler_state: dispatchGate.state || null,
      reason: dispatchGate.reason || null,
    };
  }

  const admissionCtx = dispatchGate.applicable ? admissionContextFromWake(wake) : null;
  if (admissionCtx && admissionJournal) {
    let existing = await admissionJournal.read(admissionCtx);
    if (existing && ['PREPARED', 'STARTED', 'ADMITTED', 'REJECTED'].includes(String(existing.phase || ''))) {
      if (existing.phase === 'PREPARED') existing = await observePreparedAdmission(cfg, existing, registry);
      if (existing && existing.phase === 'STARTED') existing = await reconcileAdmissionRecord(cfg, existing);
      if (existing && existing.phase === 'REJECTED') {
        await setLastSubmittedWake(lane.lane_id, wake.wake_id);
        await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id });
        return {
          ok: true,
          suppressed: true,
          admission_phase: 'REJECTED',
          wake_id: wake.wake_id,
          lane_id: lane.lane_id,
          reason: existing.last_error || 'admission_rejected',
        };
      }
      if (existing) {
        await setLastSubmittedWake(lane.lane_id, wake.wake_id);
        await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id });
        return {
          ok: true,
          deduped: true,
          admission_phase: existing.phase,
          admission_pending: existing.phase !== 'ADMITTED',
          wake_id: wake.wake_id,
          lane_id: lane.lane_id,
        };
      }
    }
  }

  if (await lastSubmittedWake(lane.lane_id) === wake.wake_id) {
    await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id });
    return { ok: true, deduped: true, wake_id: wake.wake_id, lane_id: lane.lane_id };
  }

  let tab = await findCurrentWorkerTab(lane);
  if (!tab || !tab.id) {
    const runtime = globalThis.CAHWorkerRuntime;
    if (!runtime || typeof runtime.createWorker !== 'function') throw new Error('Lane Worker runtime is unavailable');
    const created = await runtime.createWorker(lane.lane_id);
    await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id });
    await report(cfg, 'wake.worker_bootstrap', created && created.ok ? 'info' : 'warn', {
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
      wake_id: wake.wake_id,
      create_result: created || null,
    });
    return { ok: Boolean(created && created.ok), bootstrap: true, lane_id: lane.lane_id, wake_id: wake.wake_id, create_result: created };
  }

  async function replaceUnusableCurrentWorker(reason) {
    const runtime = globalThis.CAHWorkerRuntime;
    if (!runtime || typeof runtime.createWorker !== 'function') {
      await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
      throw new Error(`Lane Worker runtime unavailable during stale-current recovery: ${reason}`);
    }
    const created = await runtime.createWorker(lane.lane_id);
    await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
    await report(cfg, 'wake.worker_replacement_requested', created && created.ok ? 'warn' : 'error', {
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
      wake_id: wake.wake_id,
      stale_tab_id: tab.id,
      reason: String(reason || '').slice(0, 1000),
      create_result: created || null,
    });
    return {
      ok: Boolean(created && created.ok),
      bootstrap: true,
      replacement: true,
      lane_id: lane.lane_id,
      wake_id: wake.wake_id,
      stale_tab_id: tab.id,
      reason,
      create_result: created,
    };
  }

  // Canonical Worker lifecycle is authoritative over the browser's cached
  // "current" pointer. A Worker that has published context_compacted HANDOFF is
  // an outgoing execution context and must not accept a newer semantic dispatch.
  const poolBeforeDispatch = lane.pool_state || {};
  const currentBeforeDispatch = poolBeforeDispatch.current || null;
  if (currentBeforeDispatch && currentBeforeDispatch.handoff_id) {
    const lifecycle = await localApi(cfg, {
      op: 'worker_takeover_status',
      client_id: cfg.clientId,
      project_id: cfg.projectId,
      handoff_id: String(currentBeforeDispatch.handoff_id),
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
    });
    const outgoing = Boolean(
      lifecycle
      && lifecycle.rollover_requested
      && String(lifecycle.rollover_request_handoff_id || '') === String(currentBeforeDispatch.handoff_id)
    );
    if (outgoing) {
      const runtime = globalThis.CAHWorkerRuntime;
      const localHandoff = poolBeforeDispatch.handoff || null;
      let handoffAction = { ok: true, idle: 'handoff_already_pending' };
      if (!localHandoff || !['creating', 'awaiting_git_takeover'].includes(String(localHandoff.status || ''))) {
        if (!runtime || typeof runtime.createWorker !== 'function') {
          await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
          throw new Error('Lane Worker runtime unavailable while canonical handoff is pending');
        }
        handoffAction = await runtime.createWorker(lane.lane_id, lifecycle.handoff_packet_ref || null);
      }
      await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id });
      await report(cfg, 'wake.deferred_for_worker_handoff', 'info', {
        wake_id: wake.wake_id,
        lane_id: lane.lane_id,
        outgoing_worker_ref: currentBeforeDispatch.handoff_id,
        handoff_packet_ref: lifecycle.handoff_packet_ref || null,
        local_handoff_status: localHandoff ? localHandoff.status || null : null,
        handoff_action: handoffAction || null,
      });
      return {
        ok: true,
        deferred_for_handoff: true,
        wake_id: wake.wake_id,
        lane_id: lane.lane_id,
        outgoing_worker_ref: currentBeforeDispatch.handoff_id,
        handoff_action: handoffAction,
      };
    }
  }

  let attachment = null;
  if (wake.attachment_ref) {
    const artifact = await localApi(cfg, {
      op: 'artifact_read',
      client_id: cfg.clientId,
      project_id: cfg.projectId,
      artifact_ref: String(wake.attachment_ref),
    });
    if (!artifact || !artifact.ok) {
      const reason = (artifact && artifact.error) || 'visual attachment read failed';
      await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
      throw new Error(reason);
    }
    attachment = {
      ref: String(artifact.artifact_ref || wake.attachment_ref),
      file_name: String(artifact.file_name || 'gah-image'),
      mime_type: String(artifact.mime_type || ''),
      size_bytes: Number(artifact.size_bytes || 0),
      sha256: String(artifact.sha256 || ''),
      base64: String(artifact.base64 || ''),
    };
  }

  const poolAtSend = lane.pool_state || {};
  const currentAtSend = poolAtSend.current || null;
  const workerRef = currentAtSend && currentAtSend.handoff_id ? String(currentAtSend.handoff_id) : '';
  const wakeMarker = marker(wake);

  if (uiRecoveryApi && typeof uiRecoveryApi.preflight === 'function') {
    await uiRecoveryApi.preflight(tab.id, {
      context: 'worker_semantic_dispatch',
      requireComposer: true,
      timeoutMs: 8000,
    });
  }

  let prepared = null;
  if (admissionCtx && admissionJournal) {
    prepared = await admissionJournal.prepare(admissionCtx, {
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
      worker_ref: workerRef,
      project_id: cfg.projectId,
      tab_id: tab.id,
      wake_marker: wakeMarker,
      wake_id: wake.wake_id,
      message_id: claim.message_id,
    });
  }

  let response;
  try {
    response = await tabsSendMessage(tab.id, {
      type: 'gah-submit-wake',
      marker: wakeMarker,
      text: wakeText(wake),
      attachment,
    });
  } catch (err) {
    const raw = String(err && err.message || err);
    const definitelyUnsent = /Receiving end does not exist|Could not establish connection/i.test(raw);
    if (admissionCtx && admissionJournal && definitelyUnsent) {
      await admissionJournal.forgetUnsent(admissionCtx);
      const reason = `Worker content script unavailable: ${raw}`;
      return await replaceUnusableCurrentWorker(reason);
    }
    if (admissionCtx && admissionJournal) {
      await setLastSubmittedWake(lane.lane_id, wake.wake_id);
      await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
      await report(cfg, 'dispatch.send_outcome_unknown', 'warn', {
        wake_id: wake.wake_id,
        lane_id: lane.lane_id,
        dispatch_id: wake.dispatch_id || null,
        error: raw.slice(0, 1000),
      });
      return { ok: true, wake_id: wake.wake_id, lane_id: lane.lane_id, admission_phase: 'PREPARED', send_outcome_unknown: true };
    }
    const reason = `Worker content script unavailable: ${raw}`;
    return await replaceUnusableCurrentWorker(reason);
  }
  if (admissionCtx && admissionJournal) {
    await report(cfg, 'dispatch.submission_observed', 'info', {
      wake_id: wake.wake_id,
      lane_id: lane.lane_id,
      dispatch_id: wake.dispatch_id || null,
      dispatch_generation: wake.dispatch_generation || null,
      worker_ref: workerRef || null,
      response_started: Boolean(response && response.response_started),
      journal_fresh: Boolean(prepared && prepared.fresh),
    });
  }
  if (!response || !response.ok) {
    const reason = (response && response.error) || 'Worker wake submission failed';
    const definitePreSend = Boolean(response && response.definite_pre_send);
    if (admissionCtx && admissionJournal && !definitePreSend) {
      await setLastSubmittedWake(lane.lane_id, wake.wake_id);
      await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
      await report(cfg, 'dispatch.send_outcome_unknown', 'warn', {
        wake_id: wake.wake_id,
        lane_id: lane.lane_id,
        dispatch_id: wake.dispatch_id || null,
        error: String(reason).slice(0, 1000),
      });
      return { ok: true, wake_id: wake.wake_id, lane_id: lane.lane_id, admission_phase: 'PREPARED', send_outcome_unknown: true };
    }
    if (admissionCtx && admissionJournal) await admissionJournal.forgetUnsent(admissionCtx);
    const recoverable = /Expected exactly one visible ChatGPT composer, found 0|Expected exactly one visible enabled send button, found 0|content script unavailable|Receiving end does not exist/i.test(String(reason));
    if (recoverable) return await replaceUnusableCurrentWorker(reason);
    await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id }).catch(() => null);
    throw new Error(reason);
  }

  let admissionPhase = null;
  if (admissionCtx && admissionJournal && response.response_started) {
    let record = await admissionJournal.started(admissionCtx, {
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
      worker_ref: workerRef,
      project_id: cfg.projectId,
      tab_id: tab.id,
      wake_marker: wakeMarker,
      wake_id: wake.wake_id,
      message_id: claim.message_id,
      observed_at: new Date().toISOString(),
      observation: 'send_response_started',
    });
    record = await reconcileAdmissionRecord(cfg, record);
    admissionPhase = record && record.phase || 'STARTED';
  }

  await setLastSubmittedWake(lane.lane_id, wake.wake_id);
  await storageSet({ lastError: '', lastTransport: 'local' });
  await localApi(cfg, { op: 'consume', client_id: cfg.clientId, message_id: claim.message_id });
  await report(cfg, 'wake.submitted', 'info', {
    wake_id: wake.wake_id,
    lane_id: lane.lane_id,
    worker_project_key: lane.project_key,
    tab_id: tab.id,
    legacy_routing: resolved.legacy,
    attachment_ref: attachment ? attachment.ref : null,
    attachment_sha256: attachment ? attachment.sha256 : null,
    attachment_method: response.attachment_method || null,
  });
  return {
    ok: true,
    wake_id: wake.wake_id,
    lane_id: lane.lane_id,
    transport: 'local',
    attachment_ref: attachment ? attachment.ref : null,
    attachment_sha256: attachment ? attachment.sha256 : null,
    attachment_method: response.attachment_method || null,
    admission_phase: admissionPhase,
    admission_pending: Boolean(admissionPhase && admissionPhase !== 'ADMITTED'),
  };
}

async function pollOnce(laneId = '') {
  const cfg = await config();
  await storageSet({ lastPollAt: new Date().toISOString() });
  if (!cfg.enabled) return { ok: true, idle: 'disabled' };

  const registry = await lanesApi.getRegistry();
  if (!(registry.lanes || []).length) return { ok: true, idle: 'no_lanes' };

  const claim = await localApi(cfg, {
    op: 'claim',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
    lease_seconds: cfg.leaseSeconds,
    ...(laneId ? { lane_id: laneId } : {}),
  });
  await storageSet({ lastTransport: 'local' });
  if (!claim.wake) return { ok: true, idle: 'empty', transport: 'local' };

  try {
    return await routeWake(cfg, claim, registry);
  } catch (err) {
    try { await localApi(cfg, { op: 'release', client_id: cfg.clientId, message_id: claim.message_id }); } catch (_) {}
    throw err;
  }
}

async function safePoll(laneId = '') {
  const run = async () => {
    const before = await config();
    try {
      const result = await pollOnce(laneId);
      await storageSet({ lastError: '' });
      return result;
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      await storageSet({ lastError: message });
      if (before.lastError !== message) await report(before, 'wake.poll_error', 'error', {
        lane_id: laneId || null,
        message,
        outcome_unknown: Boolean(err && err.outcomeUnknown),
        code: err && err.code || null,
      });
      return { ok: false, error: message, outcome_unknown: Boolean(err && err.outcomeUnknown) };
    }
  };
  return pollGate && laneId ? pollGate.run(laneId, run) : run();
}

async function pollEnabledLanes() {
  const registry = await lanesApi.getRegistry();
  const lanes = (registry.lanes || [])
    .filter(lane => lane && lane.enabled && !lane.pending_remove)
    .sort((a, b) => String(a.lane_id).localeCompare(String(b.lane_id)));
  const results = await Promise.all(lanes.map(lane => safePoll(String(lane.lane_id || ''))));
  return { ok: results.every(item => item && item.ok !== false), results };
}

async function topologyStatus() {
  const cfg = await config();
  const registry = await lanesApi.getRegistry();
  const desired = lanesApi.publicDesired(registry);
  const remote = await localApi(cfg, {
    op: 'topology_status',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
  });
  const canonical = remote.lanes && Array.isArray(remote.lanes.lanes) ? remote.lanes.lanes : [];
  const control = remote.control_request || null;
  const isSynced = sameTopology(desired, canonical) && (!control || ['DONE', 'ERROR'].includes(String(control.status || '')));
  if (isSynced && (registry.lanes || []).some(x => x.pending_remove)) {
    const canonicalIds = new Set(canonical.map(x => x.lane_id));
    registry.lanes = registry.lanes.filter(x => !x.pending_remove || canonicalIds.has(x.lane_id));
    await lanesApi.saveRegistry(registry);
  }
  return {
    ok: true,
    desired_version: Number(registry.desired_version || 1),
    desired_count: desired.length,
    desired_enabled_count: desired.filter(x => x.enabled).length,
    canonical_version: remote.lanes ? Number(remote.lanes.topology_version || 0) : 0,
    canonical_count: remote.lanes ? Number(remote.lanes.registered_count || canonical.length) : 0,
    canonical_enabled_count: remote.lanes ? Number(remote.lanes.enabled_count || canonical.filter(x => x.enabled).length) : 0,
    synced: isSynced,
    control_request: control,
    canonical_lanes: canonical,
  };
}

async function saveTopology(message) {
  const cfg = await config();
  const registry = await lanesApi.getRegistry();
  const desired = Array.isArray(message.desired_lanes) ? message.desired_lanes : lanesApi.publicDesired(registry);
  const remoteBefore = await topologyStatus();
  const desiredIds = new Set(desired.map(x => x.lane_id));
  for (const lane of registry.lanes || []) {
    if (desiredIds.has(lane.lane_id)) continue;
    const handoff = lane.pool_state && lane.pool_state.handoff;
    if (handoff && ['creating', 'awaiting_git_takeover'].includes(String(handoff.status || ''))) {
      throw new Error(`Cannot remove ${lane.lane_id}: Worker handoff is ${handoff.status}`);
    }
    const canonicalLane = (remoteBefore.canonical_lanes || []).find(x => x.lane_id === lane.lane_id);
    if (canonicalLane && canonicalLane.status === 'RUNNING') {
      throw new Error(`Cannot remove ${lane.lane_id}: canonical lane is RUNNING`);
    }
  }

  const control = lanesApi.controlLane(registry, desired);
  if (!control) throw new Error('No control lane is available to reconcile topology');

  const requestId = `topo-${globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  const staged = await localApi(cfg, {
    op: 'topology_request',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
    request_id: requestId,
    desired_version: Number(message.desired_version || registry.desired_version || 1),
    lanes: desired,
    control_lane_id: control.lane_id,
    worker_project_key: control.project_key,
  });

  const wakeResult = await safePoll();
  return { ok: true, staged, wake: wakeResult };
}

async function laneStatus(laneId) {
  const runtime = globalThis.CAHWorkerRuntime;
  const runtimeStatus = runtime && runtime.workerStatus ? await runtime.workerStatus(laneId) : { ok: false, error: 'Worker runtime unavailable' };
  const topo = await topologyStatus();
  const canonical = (topo.canonical_lanes || []).find(x => x.lane_id === laneId) || null;
  return { ok: true, runtime: runtimeStatus, canonical, topology: { synced: topo.synced, control_request: topo.control_request } };
}

let parallelBootstrapRunning = false;
async function setParallelBootstrapState(status, lastError = null) {
  const registry = await lanesApi.getRegistry();
  if (!registry.bootstrap_parallel) return registry;
  registry.bootstrap_parallel = {
    ...registry.bootstrap_parallel,
    status,
    last_error: lastError,
    completed_at: status === 'DONE' ? new Date().toISOString() : null,
    updated_at: new Date().toISOString(),
  };
  return lanesApi.saveRegistry(registry);
}

async function maintainParallelBootstrap() {
  if (parallelBootstrapRunning) return { ok: true, idle: 'parallel_bootstrap_running' };
  parallelBootstrapRunning = true;
  try {
    let registry = await lanesApi.getRegistry();
    const bootstrap = registry.bootstrap_parallel || null;
    if (!bootstrap || bootstrap.status === 'DONE') return { ok: true, idle: 'parallel_bootstrap_done_or_absent' };

    const targetIds = Array.isArray(bootstrap.target_lane_ids) ? bootstrap.target_lane_ids : [];
    if (!targetIds.length) {
      await setParallelBootstrapState('ERROR', 'bootstrap target_lane_ids missing');
      return { ok: false, error: 'bootstrap target_lane_ids missing' };
    }

    const desired = lanesApi.publicDesired(registry);
    const desiredIds = new Set(desired.map(lane => lane.lane_id));
    for (const laneId of targetIds) {
      if (!desiredIds.has(laneId)) {
        await setParallelBootstrapState('ERROR', `bootstrap target missing from desired topology: ${laneId}`);
        return { ok: false, error: `bootstrap target missing from desired topology: ${laneId}` };
      }
    }

    const topo = await topologyStatus();
    if (!topo.synced) {
      const controlState = topo.control_request ? String(topo.control_request.status || '') : '';
      if (['PENDING', 'APPLYING'].includes(controlState)) {
        await setParallelBootstrapState('APPLYING');
        await safePoll();
        return { ok: true, waiting: 'topology_control', control_state: controlState };
      }
      await setParallelBootstrapState('APPLYING');
      const staged = await saveTopology({
        desired_lanes: desired,
        desired_version: Number(registry.desired_version || 1),
      });
      return { ok: true, staged_topology: staged };
    }

    const runtime = globalThis.CAHWorkerRuntime;
    if (!runtime || typeof runtime.workerStatus !== 'function' || typeof runtime.createWorker !== 'function') {
      throw new Error('Lane Worker runtime unavailable during parallel bootstrap');
    }

    await setParallelBootstrapState('WORKERS');
    registry = await lanesApi.getRegistry();
    let allVerified = true;
    const workerStates = [];
    for (const laneId of targetIds) {
      const lane = lanesApi.findLane(registry, laneId);
      if (!lane || !lane.enabled) continue;
      const state = await runtime.workerStatus(laneId);
      workerStates.push({
        lane_id: laneId,
        current_verified: Boolean(state && state.current_handoff_verified),
        handoff_status: state && state.handoff ? String(state.handoff.status || '') : null,
      });
      if (state && state.current_handoff_verified) continue;

      allVerified = false;
      const handoffStatus = state && state.handoff ? String(state.handoff.status || '') : '';
      if (!['creating', 'awaiting_git_takeover'].includes(handoffStatus)) {
        await runtime.createWorker(laneId);
      }
    }

    if (!allVerified) return { ok: true, waiting: 'worker_takeover', workers: workerStates };

    await setParallelBootstrapState('DONE');
    const cfg = await config();
    await report(cfg, 'topology.parallel_bootstrap_complete', 'info', {
      target_lane_ids: targetIds,
      canonical_count: topo.canonical_count,
      canonical_enabled_count: topo.canonical_enabled_count,
    });
    return { ok: true, completed: true, target_lane_ids: targetIds };
  } catch (err) {
    const message = String(err && err.message || err).slice(0, 1000);
    await setParallelBootstrapState('ERROR', message).catch(() => null);
    const cfg = await config().catch(() => null);
    if (cfg) await report(cfg, 'topology.parallel_bootstrap_error', 'error', { message });
    return { ok: false, error: message };
  } finally {
    parallelBootstrapRunning = false;
  }
}

let extensionMaintenanceRunning = false;
async function extensionRuntimeMaintenance() {
  if (extensionMaintenanceRunning) return { ok: true, idle: 'extension_maintenance_running' };
  extensionMaintenanceRunning = true;
  try {
    const cfg = await config();
    const registry = await lanesApi.getRegistry();
    const topologySummary = runtimeTopologySummary(registry);
    const manifest = ext.runtime.getManifest ? ext.runtime.getManifest() : null;
    const version = manifest && manifest.version ? String(manifest.version) : '';
    if (!version) return { ok: false, error: 'extension version unavailable' };

    const response = await localApi(cfg, {
      op: 'extension_runtime_hello',
      client_id: cfg.clientId,
      project_id: cfg.projectId,
      version,
      extension_id: ext.runtime.id || '',
      ...topologySummary,
      poll_interval_seconds: Number((await ext.alarms.get(POLL_ALARM))?.periodInMinutes || cfg.pollMinutes) * 60,
    });

    if (response.reload_requested) {
      await report(cfg, 'extension.reload_requested', 'info', {
        current_version: version,
        expected_version: response.expected_version || null,
      });
      setTimeout(() => {
        try { ext.runtime.reload(); } catch (_) {}
      }, 250);
      return response;
    }

    if (response.rehydrate_required) {
      const hydrated = await rehydrateManagedTabs();
      const completed = await localApi(cfg, {
        op: 'extension_runtime_ready',
        client_id: cfg.clientId,
        project_id: cfg.projectId,
        request_id: response.control_request_id,
        version,
        extension_id: ext.runtime.id || '',
        managed_tabs_ready: hydrated.ready,
        managed_tabs_failed: hydrated.failed,
      });
      await report(cfg, 'extension.managed_tabs_rehydrated', hydrated.failed ? 'error' : 'info', {
        version,
        total: hydrated.total,
        ready: hydrated.ready,
        failed: hydrated.failed,
        failures: hydrated.failures,
      });
      return { ...response, hydrated, completed };
    }
    return response;
  } finally {
    extensionMaintenanceRunning = false;
  }
}


async function taskCellStatus() {
  if (!taskCellApi) throw new Error('Task Cell registry unavailable');
  const binding = await taskCellApi.getBinding();
  const tabs = await tabsQuery({ url: 'https://chatgpt.com/*' });
  const matches = (tabs || []).filter(tab => taskCellApi.matchesUrl(binding, tab && tab.url));
  return {
    ok: true,
    binding,
    open_tab_count: matches.length,
    open_tabs: matches.map(tab => ({
      id: tab.id || null,
      active: Boolean(tab.active),
      url: String(tab.url || ''),
    })),
    worker_pool_managed: false,
    retirement_managed: false,
    lifecycle: binding.lifecycle,
    release_gate: binding.release_gate,
  };
}

async function openTaskCell() {
  if (!taskCellApi) throw new Error('Task Cell registry unavailable');
  const binding = await taskCellApi.getBinding();
  if (!binding.enabled) throw new Error('Task Cell binding is disabled');

  const tabs = await tabsQuery({ url: 'https://chatgpt.com/*' });
  const existing = (tabs || []).find(tab => taskCellApi.matchesUrl(binding, tab && tab.url));
  if (existing && existing.id) {
    await tabsUpdate(existing.id, { active: true });
    return {
      ok: true,
      reused: true,
      project_key: binding.project_key,
      project_root_url: binding.project_root_url,
      tab_id: existing.id,
    };
  }

  const tab = await tabsCreate({ url: binding.project_root_url, active: true });
  return {
    ok: true,
    reused: false,
    project_key: binding.project_key,
    project_root_url: binding.project_root_url,
    tab_id: tab && tab.id ? tab.id : null,
  };
}

async function verifiedWorkerFromSender(sender) {
  const tabUrl = sender && sender.tab ? String(sender.tab.url || '') : '';
  const registry = await lanesApi.getRegistry();
  for (const lane of registry.lanes || []) {
    const loc = conversationLocation(lane, tabUrl);
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    const current = pool.current || null;
    if (!loc || !current) continue;
    if (String(current.conversation_id || '') !== loc.conversation_id) continue;
    if (current.status !== 'current' || current.handoff_verified !== true) continue;
    return { registry, lane, current };
  }
  return { registry, lane: null, current: null };
}

async function handleWorkerSyscall(message, sender) {
  const cfg = await config();
  const syscall = message && message.syscall;
  if (!syscall || syscall.v !== 1 || syscall.kind !== 'action_submit') {
    throw new Error('Unsupported Worker syscall');
  }

  const resolved = await verifiedWorkerFromSender(sender);
  const matchedLane = resolved.lane;
  const current = resolved.current;
  if (!matchedLane || !current) {
    throw new Error('Worker syscall sender is not the verified current managed Worker');
  }

  const action = syscall.action;
  if (!action || typeof action !== 'object') throw new Error('action_submit syscall requires action');
  const taskId = String(syscall.task_id || action.task_id || '');
  const backendCl = String(syscall.backend_cl || action.backend_cl || '');
  const foregroundCl = String(syscall.foreground_cl || action.foreground_cl || '');
  const dispatchId = String(syscall.dispatch_id || '');
  const dispatchGeneration = Number(syscall.dispatch_generation || 0);
  const fenceToken = String(syscall.fence_token || '');
  const admissionCtx = {
    task_id: taskId,
    backend_cl: backendCl,
    dispatch_id: dispatchId,
    dispatch_generation: dispatchGeneration,
    fence_token: fenceToken,
  };

  let result;
  try {
    await ensureAdmissionForContext(
      cfg, admissionCtx, matchedLane, current,
      sender && sender.tab && sender.tab.id ? sender.tab.id : null
    );
    result = await localApi(cfg, {
      op: 'action_submit',
      client_id: cfg.clientId,
      project_id: cfg.projectId,
      task_id: taskId,
      backend_cl: backendCl,
      foreground_cl: foregroundCl,
      dispatch_id: dispatchId,
      dispatch_generation: dispatchGeneration,
      fence_token: fenceToken,
      worker_ref: String(current.handoff_id || ''),
      lane_id: matchedLane.lane_id,
      worker_project_key: matchedLane.project_key,
      action,
    });
  } catch (err) {
    await report(cfg, 'worker.syscall_action_submit_error', 'error', {
      task_id: taskId || null,
      action_id: action.action_id || null,
      lane_id: matchedLane.lane_id,
      worker_ref: current.handoff_id || null,
      dispatch_id: dispatchId || null,
      dispatch_generation: dispatchGeneration || null,
      error: String(err && err.message || err).slice(0, 1000),
    });
    throw err;
  }

  await report(cfg, 'worker.syscall_action_submit', 'info', {
    task_id: taskId,
    action_id: action.action_id || null,
    lane_id: matchedLane.lane_id,
    worker_ref: current.handoff_id || null,
    dispatch_id: dispatchId || null,
    dispatch_generation: dispatchGeneration || null,
    duplicate: Boolean(result.duplicate),
    commit_sha: result.commit_sha || null,
  });
  return result;
}

function validDispatchContext(ctx) {
  return Boolean(
    ctx && typeof ctx === 'object'
    && String(ctx.task_id || '')
    && String(ctx.backend_cl || '')
    && String(ctx.dispatch_id || '')
    && Number(ctx.dispatch_generation || 0) > 0
    && String(ctx.fence_token || '').length >= 8
  );
}

async function handleWorkerResponseEnded(message, sender) {
  const cfg = await config();
  const ctx = message && message.dispatch_context;
  if (!validDispatchContext(ctx)) throw new Error('Invalid response-end dispatch context');
  const resolved = await verifiedWorkerFromSender(sender);
  if (!resolved.lane || !resolved.current) {
    throw new Error('Response-end sender is not the verified current managed Worker');
  }
  await ensureAdmissionForContext(
    cfg, ctx, resolved.lane, resolved.current,
    sender && sender.tab && sender.tab.id ? sender.tab.id : null
  );
  const result = await localApi(cfg, {
    op: 'dispatch_liveness',
    client_id: cfg.clientId,
    project_id: cfg.projectId,
    task_id: String(ctx.task_id),
    backend_cl: String(ctx.backend_cl),
    dispatch_id: String(ctx.dispatch_id),
    dispatch_generation: Number(ctx.dispatch_generation),
    fence_token: String(ctx.fence_token),
    worker_ref: String(resolved.current.handoff_id || ''),
    lane_id: resolved.lane.lane_id,
    worker_project_key: resolved.lane.project_key,
    response_running: false,
    response_ended: true,
    observed_at: String(message.observed_at || ''),
  });
  await report(cfg, result.recovered ? 'worker.semantic_stall_recovered' : 'worker.semantic_turn_closed', result.recovered ? 'warn' : 'info', {
    lane_id: resolved.lane.lane_id,
    worker_ref: resolved.current.handoff_id || null,
    task_id: String(ctx.task_id),
    dispatch_id: String(ctx.dispatch_id),
    dispatch_generation: Number(ctx.dispatch_generation),
    recovered: Boolean(result.recovered),
    reason: result.reason || null,
    new_dispatch_id: result.dispatch ? result.dispatch.dispatch_id || null : null,
    new_dispatch_generation: result.dispatch ? result.dispatch.generation || null : null,
  });
  return result;
}

const terminalLiveness = new Map();
async function semanticLivenessSweep() {
  const cfg = await config();
  const registry = await lanesApi.getRegistry();
  for (const lane of registry.lanes || []) {
    if (!lane || !lane.enabled || lane.pending_remove) continue;
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    const current = pool.current || null;
    if (!current || current.status !== 'current' || current.handoff_verified !== true) continue;
    let tab = null;
    try {
      tab = await findCurrentWorkerTab(lane);
      if (!tab || !tab.id) continue;
      const ping = await tabsSendMessage(tab.id, { type: 'gah-content-ping' });
      const ctx = ping && ping.dispatch_context;
      if (!validDispatchContext(ctx)) continue;
      const identity = transportApi.key(ctx) + ':' + current.handoff_id;
      if (terminalLiveness.get(lane.lane_id) === identity) continue;
      await ensureAdmissionForContext(cfg, ctx, lane, current, tab.id);
      const result = await localApi(cfg, {
        op: 'dispatch_liveness',
        client_id: cfg.clientId,
        project_id: cfg.projectId,
        task_id: String(ctx.task_id),
        backend_cl: String(ctx.backend_cl),
        dispatch_id: String(ctx.dispatch_id),
        dispatch_generation: Number(ctx.dispatch_generation),
        fence_token: String(ctx.fence_token),
        worker_ref: String(current.handoff_id || ''),
        lane_id: lane.lane_id,
        worker_project_key: lane.project_key,
        response_running: Boolean(ping.response_running),
        response_ended: false,
      });
      if (result && result.matched && ['DONE', 'ERROR', 'CANCELLED'].includes(result.state)) {
        terminalLiveness.set(lane.lane_id, identity);
      }
      if (result && result.recovered) {
        await report(cfg, 'worker.semantic_lease_recovered', 'warn', {
          lane_id: lane.lane_id,
          worker_ref: current.handoff_id || null,
          task_id: String(ctx.task_id),
          dispatch_id: String(ctx.dispatch_id),
          dispatch_generation: Number(ctx.dispatch_generation),
          reason: result.reason || null,
          new_dispatch_id: result.dispatch ? result.dispatch.dispatch_id || null : null,
          new_dispatch_generation: result.dispatch ? result.dispatch.generation || null : null,
        });
      }
    } catch (err) {
      await report(cfg, 'worker.semantic_liveness_error', 'warn', {
        lane_id: lane.lane_id,
        worker_ref: current.handoff_id || null,
        tab_id: tab && tab.id ? tab.id : null,
        error: String(err && err.message || err).slice(0, 1000),
      });
    }
  }
}

async function reschedule() {
  const cfg = await config();
  if (!cfg.enabled) {
    await ext.alarms.clear(ALARM); await ext.alarms.clear(POLL_ALARM); return;
  }
  const pollMinutes = Math.max(0.5, Number(cfg.pollMinutes) || DEFAULTS.pollMinutes);
  // Keep the heavier lifecycle/control maintenance at its original cadence.
  for (const [name, periodInMinutes] of [[ALARM, Math.max(1, pollMinutes)], [POLL_ALARM, pollMinutes]]) {
    const existing = await ext.alarms.get(name);
    // Waking the service worker must not postpone its next scheduled poll.
    if (!existing || existing.periodInMinutes !== periodInMinutes) {
      await ext.alarms.create(name, { periodInMinutes });
    }
  }
}

const maintenanceGate = transportApi.laneGate();
async function runMaintenanceTick() {
  if (!(await config()).enabled) return;
  // Recovery is not a global prerequisite for useful work. Each lane's send
  // path retains its own admission reconciliation and exact dispatch checks.
  pollEnabledLanes().catch(() => null);
  extensionRuntimeMaintenance().catch(() => null);
  maintainParallelBootstrap().catch(() => null);
  maintenanceGate.run('admissions', reconcilePendingAdmissions).catch(() => null);
  maintenanceGate.run('liveness', semanticLivenessSweep).catch(() => null);
}

ext.runtime.onInstalled.addListener(async () => {
  // Migrate the old default once; retain deliberately slower custom settings.
  const saved = await storageGet(['pollMinutes']);
  if (saved.pollMinutes === 1) await storageSet({ pollMinutes: DEFAULTS.pollMinutes });
  await reschedule();
  runMaintenanceTick();
});
if (ext.runtime.onStartup) ext.runtime.onStartup.addListener(() => { reschedule(); runMaintenanceTick(); });
ext.alarms.onAlarm.addListener(alarm => {
  if (alarm && alarm.name === ALARM) runMaintenanceTick();
  else if (alarm && alarm.name === POLL_ALARM) pollEnabledLanes().catch(() => null);
});

ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message) return false;
  if (message.type === 'gah-poll-now') {
    pollEnabledLanes().then(sendResponse); return true;
  }
  if (message.type === 'gah-reschedule') {
    reschedule().then(() => sendResponse({ ok: true })); return true;
  }
  if (message.type === 'gah-topology-save') {
    saveTopology(message).then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) })); return true;
  }
  if (message.type === 'gah-topology-status') {
    topologyStatus().then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) })); return true;
  }
  if (message.type === 'gah-lane-status') {
    laneStatus(String(message.lane_id || '')).then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) })); return true;
  }
  if (message.type === 'gah-task-cell-status') {
    taskCellStatus().then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) })); return true;
  }
  if (message.type === 'gah-task-cell-open') {
    openTaskCell().then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) })); return true;
  }
  if (message.type === 'gah-worker-syscall') {
    handleWorkerSyscall(message, _sender)
      .then(sendResponse)
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (message.type === 'gah-worker-response-ended') {
    handleWorkerResponseEnded(message, _sender)
      .then(sendResponse)
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  if (message.type === 'gah-local-event') {
    config().then(cfg => report(cfg, String(message.event || ''), String(message.level || 'info'), message.data || {}))
      .then(() => sendResponse({ ok: true }))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
  return false;
});

reschedule();
runMaintenanceTick();
