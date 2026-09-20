'use strict';

if (typeof importScripts === 'function' && typeof window === 'undefined') {
  importScripts('background.js');
}

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const WORKER_PROJECT_KEY = 'g-p-examplelane00';
  const WORKER_PROJECT_ROOT = 'https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project';
  const WORKER_SOFT_LIMIT = 5;
  const WAKE_POLL_ALARM = 'gah-wake-poll';
  let verifyRunning = false;
  let autoRolloverRunning = false;

  async function getStorage(keys) { return ext.storage.local.get(keys); }
  async function setStorage(value) { return ext.storage.local.set(value); }
  async function tabsCreate(create) { return ext.tabs.create(create); }
  async function tabsGet(id) { return ext.tabs.get(id); }
  async function tabsRemove(id) { return ext.tabs.remove(id); }
  async function tabsSendMessage(id, message) { return ext.tabs.sendMessage(id, message); }

  function isoNow() { return new Date().toISOString(); }

  function conversationLocation(url) {
    try {
      const u = new URL(url || '');
      if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return null;
      const prefix = `/g/${WORKER_PROJECT_KEY}`;
      if (!u.pathname.startsWith(prefix)) return null;
      const rest = u.pathname.slice(prefix.length);
      const match = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
      if (!match) return null;
      u.hash = '';
      return { conversationId: match[1], url: u.toString(), path: u.pathname };
    } catch (_) {
      return null;
    }
  }

  async function cfg() {
    const d = await getStorage(['clientId', 'localEndpoint', 'projectId', 'workerPoolState', 'workerPendingTabId']);
    return {
      clientId: d.clientId || '',
      localEndpoint: d.localEndpoint || 'http://127.0.0.1:8765/api',
      projectId: d.projectId || 'git-agent-harness',
      workerPoolState: d.workerPoolState || null,
      workerPendingTabId: d.workerPendingTabId ?? null,
    };
  }

  async function report(event, level, data) {
    const c = await cfg();
    if (!c.clientId || !c.localEndpoint) return;
    try {
      await fetch(c.localEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-GAH-Bridge': '1' },
        body: JSON.stringify({
          op: 'event',
          client_id: c.clientId,
          project_id: c.projectId,
          event,
          level,
          data: data || {},
        }),
      });
    } catch (_) {}
  }

  async function localWorkerApi(c, payload) {
    if (!c.clientId || !c.localEndpoint) throw new Error('Local Worker bridge is not configured');
    const response = await fetch(c.localEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-GAH-Bridge': '1' },
      body: JSON.stringify(payload),
    });
    const text = await response.text();
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (_) {
      throw new Error(`local bridge returned non-JSON HTTP ${response.status}`);
    }
    if (!parsed.ok) throw new Error(parsed.error || `local bridge request failed HTTP ${response.status}`);
    return parsed;
  }

  function basePool(c) {
    if (c.workerPoolState && c.workerPoolState.project_key === WORKER_PROJECT_KEY) return c.workerPoolState;
    return {
      v: 1,
      project_key: WORKER_PROJECT_KEY,
      project_root: WORKER_PROJECT_ROOT,
      soft_limit: WORKER_SOFT_LIMIT,
      current: null,
      managed: [],
      updated_at: isoNow(),
    };
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
      && item.tag === 'div'
      && item.role === 'textbox'
      && item.contenteditable === 'true'
      && !item.disabled
      && !item.readonly
      && Number(item.width || 0) >= 250
      && Number(item.height || 0) >= 30);
    if (viable.some(item => item.aria_label === '此项目中的新聊天')) return true;
    return viable.length === 1;
  }

  async function waitForRootReady(tabId, timeoutMs = 15000) {
    const started = Date.now();
    let lastProbe = null;
    let lastError = '';
    while (Date.now() - started < timeoutMs) {
      try {
        const response = await tabsSendMessage(tabId, {
          type: 'gah-worker-root-probe',
          project_key: WORKER_PROJECT_KEY,
        });
        if (response && response.ok) {
          lastProbe = response.probe || {};
          if (rootProbeReady(lastProbe)) return lastProbe;
        } else if (response && response.error) {
          lastError = String(response.error);
        }
      } catch (err) {
        lastError = String(err && err.message || err);
      }
      await new Promise(r => setTimeout(r, 300));
    }
    const detail = lastProbe
      ? ` composer_count=${Number(lastProbe.composer_count || 0)} input_candidate_count=${Number(lastProbe.input_candidate_count || 0)}`
      : (lastError ? ` ${lastError}` : '');
    throw new Error(`Timed out waiting for Harness Worker root textbox.${detail}`);
  }

  async function waitForConversationRoute(tabId, timeoutMs = 25000) {
    const started = Date.now();
    let lastPath = '';
    while (Date.now() - started < timeoutMs) {
      const tab = await tabsGet(tabId);
      if (!tab) throw new Error('Harness Worker handoff tab disappeared');
      if (tab.url) {
        try { lastPath = new URL(tab.url).pathname; } catch (_) {}
        const loc = conversationLocation(tab.url);
        if (loc) return { tab, loc };
      }
      await new Promise(r => setTimeout(r, 250));
    }
    throw new Error(`Timed out waiting for Harness Worker conversation URL; last path=${lastPath || '<unknown>'}`);
  }

  async function createWorkerReplacement() {
    const c = await cfg();
    const originalPool = basePool(c);
    const existing = originalPool.handoff;
    if (existing && ['creating', 'awaiting_git_takeover'].includes(existing.status)) {
      throw new Error(`Harness Worker handoff is already ${existing.status}`);
    }

    const id = handoffId();
    const marker = `GAH_WAKE v=1 id=${id} project=${String(c.projectId || 'git-agent-harness').replace(/\s+/g, '_')}`;
    const fromConversationId = originalPool.current ? originalPool.current.conversation_id : null;
    let tab = null;
    let submitted = false;

    const creating = {
      ...originalPool,
      handoff: {
        handoff_id: id,
        status: 'creating',
        from_conversation_id: fromConversationId,
        started_at: isoNow(),
      },
      updated_at: isoNow(),
    };
    await setStorage({ workerPoolState: creating, workerPendingTabId: null });
    await report('worker.rollover_requested', 'info', {
      worker_project_key: WORKER_PROJECT_KEY,
      handoff_id: id,
      from_conversation_id: fromConversationId,
      managed_count: Array.isArray(originalPool.managed) ? originalPool.managed.length : 0,
      soft_limit: WORKER_SOFT_LIMIT,
    });

    try {
      tab = await tabsCreate({ url: WORKER_PROJECT_ROOT, active: false });
      if (!tab || !tab.id) throw new Error('Failed to open Harness Worker Project root');
      const ready = await waitForRootReady(tab.id);
      await report('worker.root_ready', 'info', {
        handoff_id: id,
        composer_count: Number(ready.composer_count || 0),
        input_candidate_count: Number(ready.input_candidate_count || 0),
      });

      const submit = await tabsSendMessage(tab.id, {
        type: 'gah-worker-submit-root-handoff',
        worker_project_key: WORKER_PROJECT_KEY,
        marker,
      });
      if (!submit || !submit.ok) throw new Error((submit && submit.error) || 'Harness Worker handoff submission failed');
      submitted = true;
      await report('worker.resume_submitted', 'info', {
        handoff_id: id,
        method: submit.method || null,
        selector: submit.selector || null,
        cleared_existing_draft: Boolean(submit.cleared_existing_draft),
        cleared_draft_length: Number(submit.prior_length || 0),
      });

      const routed = await waitForConversationRoute(tab.id);
      const loc = routed.loc;
      const priorManaged = Array.isArray(originalPool.managed) ? originalPool.managed : [];
      const managed = priorManaged.filter(item => item && item.conversation_id !== loc.conversationId);
      managed.push({
        conversation_id: loc.conversationId,
        url: loc.url,
        project_key: WORKER_PROJECT_KEY,
        created_at: isoNow(),
        status: 'handoff_pending',
        managed_by: 'gah-worker',
        handoff_verified: false,
        handoff_id: id,
      });

      const pending = {
        ...originalPool,
        managed,
        handoff: {
          handoff_id: id,
          status: 'awaiting_git_takeover',
          from_conversation_id: fromConversationId,
          to_conversation_id: loc.conversationId,
          created_at: isoNow(),
        },
        updated_at: isoNow(),
      };
      await setStorage({ workerPoolState: pending, workerPendingTabId: tab.id });
      await report('worker.chat_created', 'info', {
        worker_project_key: WORKER_PROJECT_KEY,
        handoff_id: id,
        conversation_id: loc.conversationId,
        managed_count: managed.length,
        soft_limit: WORKER_SOFT_LIMIT,
        status: 'handoff_pending',
      });
      return {
        ok: true,
        worker_project_key: WORKER_PROJECT_KEY,
        handoff_id: id,
        conversation_id: loc.conversationId,
        managed_count: managed.length,
        status: 'awaiting_git_takeover',
      };
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      await setStorage({ workerPoolState: originalPool, workerPendingTabId: null });
      await report('worker.create_error', 'error', {
        worker_project_key: WORKER_PROJECT_KEY,
        handoff_id: id,
        message,
        submitted,
      });
      if (tab && tab.id) {
        try { await tabsRemove(tab.id); } catch (_) {}
      }
      throw err;
    }
  }

  async function verifyPendingHandoff() {
    const c = await cfg();
    const pool = basePool(c);
    const handoff = pool.handoff || null;
    if (!handoff || handoff.status !== 'awaiting_git_takeover') {
      return { ok: true, idle: 'no_pending_handoff' };
    }

    const id = String(handoff.handoff_id || '');
    const toConversationId = String(handoff.to_conversation_id || '');
    if (!id.startsWith('pool-') || !toConversationId) {
      throw new Error('Pending Worker handoff metadata is incomplete');
    }

    const ack = await localWorkerApi(c, {
      op: 'worker_takeover_status',
      client_id: c.clientId,
      project_id: c.projectId,
      handoff_id: id,
    });
    if (!ack.matched || ack.handoff_id !== id || ack.last_pool_takeover_id !== id) {
      return {
        ok: true,
        verified: false,
        handoff_id: id,
        last_pool_takeover_id: ack.last_pool_takeover_id || null,
      };
    }

    const priorManaged = Array.isArray(pool.managed) ? pool.managed : [];
    const candidates = priorManaged.filter(item => item
      && item.project_key === WORKER_PROJECT_KEY
      && item.managed_by === 'gah-worker'
      && item.status === 'handoff_pending'
      && item.handoff_verified === false
      && item.handoff_id === id
      && item.conversation_id === toConversationId);
    if (candidates.length !== 1) {
      throw new Error(`Expected one pending Worker for takeover ${id}, found ${candidates.length}`);
    }

    const candidate = candidates[0];
    const loc = conversationLocation(candidate.url);
    if (!loc || loc.conversationId !== toConversationId) {
      throw new Error('Pending Worker URL does not match the handoff conversation');
    }

    const verifiedAt = isoNow();
    const promoted = {
      ...candidate,
      status: 'current',
      handoff_verified: true,
      verified_at: verifiedAt,
    };
    const managed = priorManaged.map(item => {
      if (!item) return item;
      if (item.conversation_id === toConversationId && item.handoff_id === id) return promoted;
      if (item.status === 'current') return { ...item, status: 'standby' };
      return item;
    });
    const verified = {
      ...pool,
      current: promoted,
      managed,
      handoff: {
        ...handoff,
        status: 'verified',
        verified_at: verifiedAt,
        git_state_updated: ack.state_updated || null,
      },
      updated_at: verifiedAt,
    };
    await setStorage({ workerPoolState: verified, workerPendingTabId: null });
    await report('worker.handoff_verified', 'info', {
      worker_project_key: WORKER_PROJECT_KEY,
      handoff_id: id,
      conversation_id: toConversationId,
      git_state_updated: ack.state_updated || null,
    });
    return {
      ok: true,
      verified: true,
      handoff_id: id,
      conversation_id: toConversationId,
    };
  }

  async function safeVerifyPendingHandoff() {
    if (verifyRunning) return { ok: true, idle: 'verify_running' };
    verifyRunning = true;
    try {
      return await verifyPendingHandoff();
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      await report('worker.handoff_verify_error', 'error', { message });
      return { ok: false, error: message };
    } finally {
      verifyRunning = false;
    }
  }

  async function maybeAutoRollover() {
    if (autoRolloverRunning) return { ok: true, idle: 'rollover_check_running' };
    autoRolloverRunning = true;
    try {
      const c = await cfg();
      const pool = basePool(c);
      const current = pool.current || null;
      const handoff = pool.handoff || null;
      if (!current
          || current.project_key !== WORKER_PROJECT_KEY
          || current.status !== 'current'
          || current.handoff_verified !== true
          || !handoff
          || handoff.status !== 'verified'
          || handoff.handoff_id !== current.handoff_id) {
        return { ok: true, idle: 'no_verified_current_worker' };
      }

      const id = String(current.handoff_id || '');
      if (!id.startsWith('pool-')) return { ok: true, idle: 'current_handoff_invalid' };
      const state = await localWorkerApi(c, {
        op: 'worker_takeover_status',
        client_id: c.clientId,
        project_id: c.projectId,
        handoff_id: id,
      });
      if (!state.rollover_requested
          || state.rollover_request_handoff_id !== id
          || state.rollover_reason !== 'context_compacted') {
        return { ok: true, idle: 'no_compaction_rollover_request' };
      }

      await report('worker.compaction_rollover_triggered', 'info', {
        handoff_id: id,
        conversation_id: current.conversation_id,
        reason: 'context_compacted',
      });
      return await createWorkerReplacement();
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      await report('worker.compaction_rollover_error', 'warn', { message });
      return { ok: false, error: message };
    } finally {
      autoRolloverRunning = false;
    }
  }

  async function workerStatus() {
    const c = await cfg();
    const pool = basePool(c);
    return {
      ok: true,
      worker_project_key: WORKER_PROJECT_KEY,
      worker_project_root: WORKER_PROJECT_ROOT,
      soft_limit: WORKER_SOFT_LIMIT,
      managed_count: Array.isArray(pool.managed) ? pool.managed.length : 0,
      current_conversation_id: pool.current ? pool.current.conversation_id : null,
      current_handoff_verified: Boolean(pool.current && pool.current.handoff_verified),
      handoff: pool.handoff || null,
      pending_tab_id: c.workerPendingTabId,
    };
  }

  async function periodicWorkerMaintenance() {
    await safeVerifyPendingHandoff();
    await maybeAutoRollover();
  }

  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => {
      if (alarm && alarm.name === WAKE_POLL_ALARM) periodicWorkerMaintenance();
    });
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message) return false;
    if (message.type === 'gah-worker-create-managed-run') {
      createWorkerReplacement()
        .then(sendResponse)
        .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
      return true;
    }
    if (message.type === 'gah-worker-verify-handoff') {
      safeVerifyPendingHandoff().then(sendResponse);
      return true;
    }
    if (message.type === 'gah-worker-status') {
      workerStatus()
        .then(sendResponse)
        .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
      return true;
    }
    return false;
  });

  periodicWorkerMaintenance();
})();
