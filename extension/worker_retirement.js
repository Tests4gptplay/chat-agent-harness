'use strict';

if (typeof importScripts === 'function' && typeof window === 'undefined') {
  importScripts('worker_runtime_v2.js');
}

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const WORKER_PROJECT_KEY = 'g-p-examplelane00';
  const WORKER_SOFT_LIMIT = 5;
  const WAKE_POLL_ALARM = 'gah-wake-poll';
  let cleanupRunning = false;

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
      return { conversationId: match[1], url: u.toString(), path: u.pathname };
    } catch (_) {
      return null;
    }
  }

  async function cfg() {
    const d = await getStorage(['clientId', 'localEndpoint', 'projectId', 'workerPoolState']);
    return {
      clientId: d.clientId || '',
      localEndpoint: d.localEndpoint || 'http://127.0.0.1:8765/api',
      projectId: d.projectId || 'git-agent-harness',
      workerPoolState: d.workerPoolState || null,
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
    try { parsed = JSON.parse(text); } catch (_) { throw new Error(`local bridge returned non-JSON HTTP ${response.status}`); }
    if (!parsed.ok) throw new Error(parsed.error || `local bridge request failed HTTP ${response.status}`);
    return parsed;
  }

  async function waitForMessage(tabId, message, timeoutMs = 10000) {
    const started = Date.now();
    let last = '';
    while (Date.now() - started < timeoutMs) {
      try {
        const response = await tabsSendMessage(tabId, message);
        if (response) return response;
      } catch (err) {
        last = String(err && err.message || err);
      }
      await new Promise(r => setTimeout(r, 200));
    }
    throw new Error(`Timed out waiting for Worker retirement content script${last ? `: ${last}` : ''}`);
  }

  async function waitForRetirementRoute(tabId, conversationId, timeoutMs = 15000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      try {
        const tab = await tabsGet(tabId);
        if (!tab) return true;
        const loc = conversationLocation(tab.url || '');
        if (!loc || loc.conversationId !== conversationId) return true;
      } catch (_) {
        return true;
      }
      await new Promise(r => setTimeout(r, 200));
    }
    return false;
  }

  function eligibleCandidate(pool) {
    const current = pool && pool.current;
    if (!current || current.project_key !== WORKER_PROJECT_KEY || !current.handoff_verified || current.status !== 'current') return null;
    if (!pool.handoff || pool.handoff.status !== 'verified' || pool.handoff.handoff_id !== current.handoff_id) return null;
    const managed = Array.isArray(pool.managed) ? pool.managed : [];
    if (managed.length <= Number(pool.soft_limit || WORKER_SOFT_LIMIT)) return null;

    const candidates = managed.filter(item => item
      && item.managed_by === 'gah-worker'
      && item.project_key === WORKER_PROJECT_KEY
      && item.status === 'standby'
      && item.handoff_verified === true
      && item.conversation_id !== current.conversation_id
      && conversationLocation(item.url)
      && conversationLocation(item.url).conversationId === item.conversation_id);

    candidates.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
    return candidates[0] || null;
  }

  async function cleanupOnce() {
    if (cleanupRunning) return { ok: true, idle: 'cleanup_running' };
    cleanupRunning = true;
    let tempTabId = null;
    let candidate = null;
    let deleteClicked = false;
    try {
      const c = await cfg();
      const pool = c.workerPoolState;
      if (!pool || pool.project_key !== WORKER_PROJECT_KEY) return { ok: true, idle: 'no_worker_pool' };
      candidate = eligibleCandidate(pool);
      if (!candidate) return { ok: true, idle: 'no_retirement_candidate' };

      const current = pool.current;
      const ack = await localWorkerApi(c, {
        op: 'worker_takeover_status',
        client_id: c.clientId,
        project_id: c.projectId,
        handoff_id: current.handoff_id,
      });
      if (!ack.matched || ack.handoff_id !== current.handoff_id || ack.last_pool_takeover_id !== current.handoff_id) {
        await report('worker.cleanup_skipped', 'warn', {
          reason: 'git_ack_mismatch',
          candidate_conversation_id: candidate.conversation_id,
          current_handoff_id: current.handoff_id,
        });
        return { ok: true, skipped: 'git_ack_mismatch' };
      }

      const retiringManaged = pool.managed.map(item => item && item.conversation_id === candidate.conversation_id
        ? { ...item, status: 'retire_pending', retire_started_at: isoNow() }
        : item);
      await setStorage({ workerPoolState: { ...pool, managed: retiringManaged, updated_at: isoNow() } });
      await report('worker.retire_pending', 'info', {
        conversation_id: candidate.conversation_id,
        current_conversation_id: current.conversation_id,
        managed_count: retiringManaged.length,
        soft_limit: Number(pool.soft_limit || WORKER_SOFT_LIMIT),
      });

      const tab = await tabsCreate({ url: candidate.url, active: false });
      if (!tab || !tab.id) throw new Error('Failed to open exact retirement candidate');
      tempTabId = tab.id;

      const response = await waitForMessage(tempTabId, {
        type: 'gah-worker-retire-conversation',
        worker_project_key: WORKER_PROJECT_KEY,
        conversation_id: candidate.conversation_id,
      });
      if (!response || !response.ok) throw new Error((response && response.error) || 'Worker retirement UI action failed');
      deleteClicked = Boolean(response.delete_clicked);

      const retired = await waitForRetirementRoute(tempTabId, candidate.conversation_id);
      if (!retired) throw new Error('Worker retirement was clicked but exact conversation route did not disappear');

      const fresh = await cfg();
      const latest = fresh.workerPoolState;
      if (!latest || latest.project_key !== WORKER_PROJECT_KEY) throw new Error('Worker pool disappeared during retirement');
      if (!latest.current || latest.current.conversation_id !== current.conversation_id || !latest.current.handoff_verified) {
        throw new Error('Current Worker changed during retirement');
      }
      const remaining = (Array.isArray(latest.managed) ? latest.managed : []).filter(item => item && item.conversation_id !== candidate.conversation_id);
      await setStorage({ workerPoolState: { ...latest, managed: remaining, updated_at: isoNow() } });
      await report('worker.chat_retired', 'info', {
        conversation_id: candidate.conversation_id,
        current_conversation_id: current.conversation_id,
        managed_count: remaining.length,
        soft_limit: Number(latest.soft_limit || WORKER_SOFT_LIMIT),
      });
      return { ok: true, retired: candidate.conversation_id, managed_count: remaining.length };
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      if (candidate && !deleteClicked) {
        try {
          const c = await cfg();
          const pool = c.workerPoolState;
          if (pool && Array.isArray(pool.managed)) {
            const managed = pool.managed.map(item => item && item.conversation_id === candidate.conversation_id && item.status === 'retire_pending'
              ? { ...item, status: 'standby', retire_started_at: undefined }
              : item);
            await setStorage({ workerPoolState: { ...pool, managed, updated_at: isoNow() } });
          }
        } catch (_) {}
      }
      await report('worker.cleanup_error', 'error', {
        message,
        candidate_conversation_id: candidate ? candidate.conversation_id : null,
        delete_clicked: deleteClicked,
      });
      return { ok: false, error: message };
    } finally {
      if (Number.isInteger(tempTabId)) {
        try { await tabsRemove(tempTabId); } catch (_) {}
      }
      cleanupRunning = false;
    }
  }

  function scheduleCleanup() {
    setTimeout(() => cleanupOnce(), 100);
  }

  if (ext.storage && ext.storage.onChanged) {
    ext.storage.onChanged.addListener((changes, areaName) => {
      if (areaName === 'local' && changes.workerPoolState) scheduleCleanup();
    });
  }
  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => {
      if (alarm && alarm.name === WAKE_POLL_ALARM) cleanupOnce();
    });
  }

  cleanupOnce();
})();
