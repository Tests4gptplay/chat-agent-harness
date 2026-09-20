'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const lanesApi = globalThis.CAHLanes;
  const uiRecoveryApi = globalThis.CAHUiRecovery;
  const WAKE_POLL_ALARM = 'gah-wake-poll';
  const HANDOFF_RETRY_AFTER_MS = 90 * 1000;
  const HANDOFF_RETRY_INTERVAL_MS = 60 * 1000;
  const HANDOFF_RETRY_LIMIT = 3;
  let maintenanceRunning = false;

  async function getStorage(keys) { return ext.storage.local.get(keys); }
  async function tabsCreate(create) { return ext.tabs.create(create); }
  async function tabsGet(id) { return ext.tabs.get(id); }
  async function tabsRemove(id) { return ext.tabs.remove(id); }
  async function tabsSendMessage(id, message) { return ext.tabs.sendMessage(id, message); }
  function now() { return new Date().toISOString(); }

  function conversationLocation(lane, url) {
    try {
      const u = new URL(url || '');
      if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return null;
      const prefix = `/g/${lane.project_key}`;
      if (!u.pathname.startsWith(prefix)) return null;
      const rest = u.pathname.slice(prefix.length);
      const m = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
      if (!m) return null;
      u.hash = '';
      return { conversationId: m[1], url: u.toString(), path: u.pathname };
    } catch (_) { return null; }
  }

  async function cfg() {
    const d = await getStorage(['clientId', 'localEndpoint', 'projectId']);
    return {
      clientId: d.clientId || '',
      localEndpoint: d.localEndpoint || lanesApi.DEFAULT_ENDPOINT,
      projectId: d.projectId || lanesApi.DEFAULT_PROJECT_ID,
    };
  }

  async function localApi(c, payload) {
    if (!c.clientId || !c.localEndpoint) throw new Error('Local bridge is not configured');
    const response = await fetch(c.localEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-GAH-Bridge': '1' },
      body: JSON.stringify(payload),
    });
    const text = await response.text();
    let parsed;
    try { parsed = JSON.parse(text); } catch (_) { throw new Error(`local bridge returned non-JSON HTTP ${response.status}`); }
    if (!parsed.ok) throw new Error(parsed.error || `local bridge request failed HTTP ${response.status}`);
    return parsed;
  }

  async function report(event, level, data) {
    const c = await cfg();
    if (!c.clientId) return;
    try {
      await localApi(c, {
        op: 'event',
        client_id: c.clientId,
        project_id: c.projectId,
        event,
        level,
        data: data || {},
      });
    } catch (_) {}
  }

  async function loadLane(laneId) {
    const registry = await lanesApi.getRegistry();
    const lane = (registry.lanes || []).find(x => x.lane_id === laneId);
    if (!lane) throw new Error(`Unknown lane ${laneId}`);
    return { registry, lane };
  }

  async function saveLane(registry, lane) {
    registry.lanes = registry.lanes.map(x => x.lane_id === lane.lane_id ? lane : x);
    await lanesApi.saveRegistry(registry);
    return lane;
  }

  function handoffId() {
    const raw = globalThis.crypto && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `pool-${raw}`;
  }

  function rootProbeReady(probe) {
    if (!probe || Number(probe.composer_count || 0) !== 1) return false;
    const inputs = Array.isArray(probe.input_candidates) ? probe.input_candidates : [];
    const viable = inputs.filter(item => item
      && (
        (item.tag === 'div' && item.role === 'textbox' && item.contenteditable === 'true')
        || item.tag === 'textarea'
        || item.tag === 'input'
      )
      && !item.disabled
      && !item.readonly
      && Number(item.width || 0) >= 250
      && Number(item.height || 0) >= 30);
    if (viable.some(item => item.aria_label === '此项目中的新聊天')) return true;
    return viable.length === 1;
  }

  async function waitForRootReady(tabId, lane, timeoutMs = 15000) {
    const started = Date.now();
    let last = '';
    while (Date.now() - started < timeoutMs) {
      try {
        if (uiRecoveryApi && typeof uiRecoveryApi.preflight === 'function') {
          await uiRecoveryApi.preflight(tabId, {
            context: 'lane_worker_root_bootstrap',
            requireComposer: false,
            timeoutMs: 2500,
          });
        }
        const response = await tabsSendMessage(tabId, { type: 'gah-worker-root-probe', project_key: lane.project_key });
        if (response && response.ok && rootProbeReady(response.probe || {})) return response.probe;
        if (response && response.error) last = String(response.error);
      } catch (e) { last = String(e && e.message || e); }
      await new Promise(r => setTimeout(r, 300));
    }
    throw new Error(`Timed out waiting for lane Worker root: ${lane.lane_id}${last ? `: ${last}` : ''}`);
  }

  async function waitForConversationRoute(tabId, lane, timeoutMs = 25000) {
    const started = Date.now();
    let lastPath = '';
    while (Date.now() - started < timeoutMs) {
      const tab = await tabsGet(tabId);
      if (!tab) throw new Error('Lane Worker handoff tab disappeared');
      if (tab.url) {
        try { lastPath = new URL(tab.url).pathname; } catch (_) {}
        const loc = conversationLocation(lane, tab.url);
        if (loc) return { tab, loc };
      }
      await new Promise(r => setTimeout(r, 250));
    }
    throw new Error(`Timed out waiting for lane Worker conversation URL; last path=${lastPath || '<unknown>'}`);
  }

  async function createWorker(laneId, handoffPacketRef = null) {
    const { registry, lane } = await loadLane(laneId);
    if (!lane.enabled && !lane.pending_remove) throw new Error(`Lane ${laneId} is disabled`);
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    if (pool.handoff && ['creating', 'awaiting_git_takeover'].includes(pool.handoff.status)) {
      return { ok: false, error: `Lane handoff is already ${pool.handoff.status}` };
    }

    const c = await cfg();
    const id = handoffId();
    const marker = `GAH_WAKE v=1 id=${id} project=${String(c.projectId).replace(/\s+/g, '_')}`;
    const fromId = pool.current ? pool.current.conversation_id : null;
    let tab = null;
    let submitted = false;

    lane.pool_state = {
      ...pool,
      handoff: {
        handoff_id: id,
        status: 'creating',
        from_conversation_id: fromId,
        handoff_packet_ref: handoffPacketRef || null,
        started_at: now(),
      },
      updated_at: now(),
    };
    await saveLane(registry, lane);
    await report('worker.rollover_requested', 'info', {
      lane_id: lane.lane_id, worker_project_key: lane.project_key, handoff_id: id,
      from_conversation_id: fromId, managed_count: Array.isArray(pool.managed) ? pool.managed.length : 0,
    });

    try {
      tab = await tabsCreate({ url: lane.project_root_url, active: false });
      if (!tab || !tab.id) throw new Error('Failed to open lane Project root');
      await waitForRootReady(tab.id, lane);
      const submit = await tabsSendMessage(tab.id, {
        type: 'gah-worker-submit-root-handoff',
        worker_project_key: lane.project_key,
        lane_id: lane.lane_id,
        marker,
        handoff_packet_ref: handoffPacketRef,
      });
      if (!submit || !submit.ok) throw new Error((submit && submit.error) || 'Lane Worker bootstrap submission failed');
      submitted = true;

      const routed = await waitForConversationRoute(tab.id, lane);
      const loc = routed.loc;
      const managed = (Array.isArray(pool.managed) ? pool.managed : [])
        .filter(item => item && item.conversation_id !== loc.conversationId);
      managed.push({
        conversation_id: loc.conversationId,
        url: loc.url,
        project_key: lane.project_key,
        lane_id: lane.lane_id,
        created_at: now(),
        status: 'handoff_pending',
        managed_by: 'gah-worker',
        handoff_verified: false,
        handoff_id: id,
      });

      lane.pool_state = {
        ...pool,
        project_key: lane.project_key,
        project_root: lane.project_root_url,
        managed,
        handoff: {
          handoff_id: id,
          status: 'awaiting_git_takeover',
          from_conversation_id: fromId,
          to_conversation_id: loc.conversationId,
          handoff_packet_ref: handoffPacketRef || null,
          created_at: now(),
        },
        updated_at: now(),
      };
      lane.pending_tab_id = tab.id;
      await saveLane(registry, lane);
      await report('worker.chat_created', 'info', {
        lane_id: lane.lane_id, worker_project_key: lane.project_key,
        handoff_id: id, conversation_id: loc.conversationId, managed_count: managed.length,
      });
      return { ok: true, lane_id: lane.lane_id, handoff_id: id, conversation_id: loc.conversationId, status: 'awaiting_git_takeover' };
    } catch (err) {
      lane.pool_state = pool;
      lane.pending_tab_id = null;
      await saveLane(registry, lane);
      if (tab && tab.id) { try { await tabsRemove(tab.id); } catch (_) {} }
      const message = String(err && err.message || err).slice(0, 1000);
      await report('worker.create_error', 'error', {
        lane_id: lane.lane_id, worker_project_key: lane.project_key, handoff_id: id, message, submitted,
      });
      return { ok: false, error: message, lane_id: lane.lane_id };
    }
  }

  async function maybeRetryPendingTakeover(registry, lane, pool, handoff, id, toId) {
    const createdMs = Date.parse(handoff.created_at || handoff.started_at || '');
    const elapsedMs = Number.isFinite(createdMs) ? Date.now() - createdMs : 0;
    const retryCount = Number(handoff.takeover_retry_count || 0);
    const lastAttemptMs = Date.parse(handoff.last_takeover_attempt_at || '');
    const sinceLastAttemptMs = Number.isFinite(lastAttemptMs) ? Date.now() - lastAttemptMs : Infinity;

    if (elapsedMs < HANDOFF_RETRY_AFTER_MS) return { attempted: false, reason: 'handoff_retry_not_due' };
    if (retryCount >= HANDOFF_RETRY_LIMIT) return { attempted: false, reason: 'handoff_retry_limit' };
    if (sinceLastAttemptMs < HANDOFF_RETRY_INTERVAL_MS) return { attempted: false, reason: 'handoff_retry_throttled' };

    const tabId = Number(lane.pending_tab_id || 0);
    if (!tabId) return { attempted: false, reason: 'handoff_pending_tab_missing' };

    let tab;
    try {
      tab = await tabsGet(tabId);
    } catch (err) {
      await report('worker.handoff_retry_error', 'warn', {
        lane_id: lane.lane_id,
        handoff_id: id,
        conversation_id: toId,
        message: `Pending handoff tab unavailable: ${String(err && err.message || err).slice(0, 700)}`,
      });
      return { attempted: false, reason: 'handoff_pending_tab_unavailable' };
    }

    const loc = conversationLocation(lane, tab && tab.url);
    if (!loc || loc.conversationId !== toId) {
      return { attempted: false, reason: 'handoff_pending_route_mismatch' };
    }

    let ping = null;
    try {
      ping = await tabsSendMessage(tabId, { type: 'gah-content-ping' });
    } catch (err) {
      await report('worker.handoff_retry_error', 'warn', {
        lane_id: lane.lane_id,
        handoff_id: id,
        conversation_id: toId,
        message: `Pending Worker content script unavailable: ${String(err && err.message || err).slice(0, 700)}`,
      });
      return { attempted: false, reason: 'handoff_pending_content_unavailable' };
    }

    const attemptAt = now();
    if (ping && ping.response_running) {
      lane.pool_state = {
        ...pool,
        handoff: { ...handoff, last_takeover_attempt_at: attemptAt },
        updated_at: attemptAt,
      };
      await saveLane(registry, lane);
      await report('worker.handoff_retry_deferred', 'info', {
        lane_id: lane.lane_id,
        handoff_id: id,
        conversation_id: toId,
        reason: 'assistant_response_running',
        assistant_count: Number(ping.assistant_count || 0),
      });
      return { attempted: false, reason: 'assistant_response_running' };
    }

    const c = await cfg();
    const attempt = retryCount + 1;
    const wakeMarker = `GAH_WAKE v=1 id=${id} project=${String(c.projectId).replace(/\s+/g, '_')}`;
    const retryText = [
      wakeMarker,
      'GAH_BOOTSTRAP repo=example-owner/cah-private state=state/chatgpt.json',
      `GAH_LANE lane_id=${lane.lane_id} project_key=${lane.project_key}`,
      `GAH_TAKEOVER_RETRY handoff_id=${id} attempt=${attempt}`,
      'Takeover recovery only. Read canonical state/lanes.json and state/chatgpt.json. Before any task work, write this exact pool-* handoff id to this lane last_pool_takeover_id (lane-00 may mirror state/chatgpt.json), checkpoint the takeover, then stop. Do not perform active task semantic work in this response; the queued fenced dispatch will be delivered after takeover verification.',
    ].join('\n');

    let submitted;
    try {
      submitted = await tabsSendMessage(tabId, {
        type: 'gah-submit-wake',
        marker: wakeMarker,
        text: retryText,
      });
    } catch (err) {
      submitted = { ok: false, error: String(err && err.message || err) };
    }

    lane.pool_state = {
      ...pool,
      handoff: {
        ...handoff,
        takeover_retry_count: attempt,
        last_takeover_attempt_at: attemptAt,
      },
      updated_at: attemptAt,
    };
    await saveLane(registry, lane);

    await report('worker.handoff_retry_submitted', submitted && submitted.ok ? 'warn' : 'error', {
      lane_id: lane.lane_id,
      handoff_id: id,
      conversation_id: toId,
      attempt,
      response_started: Boolean(submitted && submitted.response_started),
      error: submitted && !submitted.ok ? String(submitted.error || 'retry submission failed').slice(0, 700) : null,
    });

    return {
      attempted: true,
      ok: Boolean(submitted && submitted.ok),
      attempt,
      response_started: Boolean(submitted && submitted.response_started),
      error: submitted && !submitted.ok ? String(submitted.error || '') : null,
    };
  }

  async function verifyLane(laneId) {
    const { registry, lane } = await loadLane(laneId);
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    const handoff = pool.handoff || null;
    if (!handoff || handoff.status !== 'awaiting_git_takeover') return { ok: true, idle: 'no_pending_handoff', lane_id: laneId };
    const id = String(handoff.handoff_id || '');
    const toId = String(handoff.to_conversation_id || '');
    if (!id.startsWith('pool-') || !toId) throw new Error('Lane pending handoff metadata is incomplete');

    const c = await cfg();
    const ack = await localApi(c, {
      op: 'worker_takeover_status',
      client_id: c.clientId,
      project_id: c.projectId,
      handoff_id: id,
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
    });
    if (!ack.matched || ack.last_pool_takeover_id !== id) {
      const retry = await maybeRetryPendingTakeover(registry, lane, pool, handoff, id, toId);
      return {
        ok: true,
        verified: false,
        lane_id: laneId,
        handoff_id: id,
        last_pool_takeover_id: ack.last_pool_takeover_id || null,
        retry,
      };
    }

    if (handoff.handoff_packet_ref) {
      const completed = await localApi(c, {
        op: 'worker_handoff_complete',
        client_id: c.clientId,
        project_id: c.projectId,
        lane_id: lane.lane_id,
        worker_project_key: lane.project_key,
        successor_worker_ref: id,
        handoff_packet_ref: String(handoff.handoff_packet_ref),
      });
      if (!completed || !completed.ok) {
        throw new Error((completed && completed.error) || 'Canonical semantic handoff completion failed');
      }
      await report('worker.semantic_handoff_completed', 'info', {
        lane_id: lane.lane_id,
        worker_project_key: lane.project_key,
        successor_worker_ref: id,
        handoff_packet_ref: String(handoff.handoff_packet_ref),
        dispatch_id: completed.dispatch ? completed.dispatch.dispatch_id || null : null,
        dispatch_generation: completed.dispatch ? completed.dispatch.generation || null : null,
        already_recovered: Boolean(completed.already_recovered),
      });
    }

    const managed = Array.isArray(pool.managed) ? pool.managed : [];
    const candidates = managed.filter(item => item && item.handoff_id === id && item.conversation_id === toId && item.status === 'handoff_pending');
    if (candidates.length !== 1) throw new Error(`Expected one pending Worker for ${laneId}/${id}, found ${candidates.length}`);
    const promoted = { ...candidates[0], status: 'current', handoff_verified: true, verified_at: now() };
    const nextManaged = managed.map(item => {
      if (item.conversation_id === toId && item.handoff_id === id) return promoted;
      if (item.status === 'current') return { ...item, status: 'standby' };
      return item;
    });
    lane.pool_state = {
      ...pool,
      current: promoted,
      managed: nextManaged,
      handoff: { ...handoff, status: 'verified', verified_at: now(), git_state_updated: ack.state_updated || null },
      updated_at: now(),
    };
    lane.pending_tab_id = null;
    await saveLane(registry, lane);
    await report('worker.handoff_verified', 'info', {
      lane_id: lane.lane_id, worker_project_key: lane.project_key, handoff_id: id, conversation_id: toId,
    });
    return { ok: true, verified: true, lane_id: laneId, handoff_id: id, conversation_id: toId };
  }

  async function maybeAutoRollover(laneId) {
    const { lane } = await loadLane(laneId);
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    const current = pool.current;
    if (!current || current.status !== 'current' || current.handoff_verified !== true) return { ok: true, idle: 'no_verified_current', lane_id: laneId };
    const c = await cfg();
    const state = await localApi(c, {
      op: 'worker_takeover_status',
      client_id: c.clientId,
      project_id: c.projectId,
      handoff_id: current.handoff_id,
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
    });
    const supportedRollover = ['context_compacted', 'semantic_stall'].includes(String(state.rollover_reason || ''));
    if (!state.rollover_requested || state.rollover_request_handoff_id !== current.handoff_id || !supportedRollover) {
      return { ok: true, idle: 'no_rollover_request', lane_id: laneId };
    }
    return createWorker(laneId, state.handoff_packet_ref || null);
  }

  async function workerStatus(laneId) {
    const { lane } = await loadLane(laneId);
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    return {
      ok: true,
      lane_id: lane.lane_id,
      display_name: lane.display_name,
      worker_project_key: lane.project_key,
      worker_project_root: lane.project_root_url,
      enabled: Boolean(lane.enabled),
      pending_remove: Boolean(lane.pending_remove),
      managed_count: Array.isArray(pool.managed) ? pool.managed.length : 0,
      current_conversation_id: pool.current ? pool.current.conversation_id : null,
      current_handoff_verified: Boolean(pool.current && pool.current.handoff_verified),
      handoff: pool.handoff || null,
      pending_tab_id: lane.pending_tab_id,
    };
  }

  async function periodicMaintenance() {
    if (maintenanceRunning) return;
    maintenanceRunning = true;
    try {
      const registry = await lanesApi.getRegistry();
      for (const lane of registry.lanes || []) {
        try {
          await verifyLane(lane.lane_id);
          await maybeAutoRollover(lane.lane_id);
        } catch (err) {
          await report('worker.maintenance_error', 'warn', {
            lane_id: lane.lane_id, message: String(err && err.message || err).slice(0, 800),
          });
        }
      }
    } finally { maintenanceRunning = false; }
  }

  globalThis.CAHWorkerRuntime = Object.freeze({ createWorker, verifyLane, workerStatus, periodicMaintenance });

  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => {
      if (alarm && alarm.name === WAKE_POLL_ALARM) periodicMaintenance();
    });
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message) return false;
    if (message.type === 'gah-worker-create-managed-run') {
      createWorker(String(message.lane_id || ''))
        .then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
    if (message.type === 'gah-worker-verify-handoff') {
      verifyLane(String(message.lane_id || ''))
        .then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
    if (message.type === 'gah-worker-status') {
      workerStatus(String(message.lane_id || ''))
        .then(sendResponse).catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
    return false;
  });

  periodicMaintenance();
})();
