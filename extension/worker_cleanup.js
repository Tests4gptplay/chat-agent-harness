'use strict';

if (typeof importScripts === 'function' && typeof window === 'undefined') {
  importScripts('worker_retirement.js');
}

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const WORKER_PROJECT_KEY = 'g-p-examplelane00';
  const WORKER_PROJECT_ROOT = 'https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project';
  const WORKER_SOFT_LIMIT = 5;
  let cleanupAllRunning = false;

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
      return { conversationId: match[1], url: u.toString() };
    } catch (_) {
      return null;
    }
  }

  function exactManagedRecord(item) {
    if (!item || item.managed_by !== 'gah-worker' || item.project_key !== WORKER_PROJECT_KEY) return false;
    const id = String(item.conversation_id || '');
    if (!/^[A-Za-z0-9-]{8,128}$/.test(id)) return false;
    const loc = conversationLocation(item.url);
    return Boolean(loc && loc.conversationId === id);
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
    throw new Error(`Timed out waiting for Worker cleanup content script${last ? `: ${last}` : ''}`);
  }

  async function waitForDeletedRoute(tabId, conversationId, timeoutMs = 15000) {
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

  function emptyPool(pool) {
    return {
      v: Number(pool && pool.v || 1),
      project_key: WORKER_PROJECT_KEY,
      project_root: String(pool && pool.project_root || WORKER_PROJECT_ROOT),
      soft_limit: Number(pool && pool.soft_limit || WORKER_SOFT_LIMIT),
      current: null,
      managed: [],
      updated_at: isoNow(),
    };
  }

  async function cleanupAllManaged() {
    if (cleanupAllRunning) return { ok: false, error: 'Managed Worker cleanup is already running' };
    cleanupAllRunning = true;
    let tempTabId = null;
    try {
      const c = await cfg();
      const pool = c.workerPoolState;
      if (!pool || pool.project_key !== WORKER_PROJECT_KEY) {
        return { ok: false, error: 'Fixed Worker pool is not available' };
      }

      const managed = Array.isArray(pool.managed) ? pool.managed.slice() : [];
      if (managed.some(item => !exactManagedRecord(item))) {
        await report('worker.cleanup_all_skipped', 'error', { reason: 'unsafe_managed_record', managed_count: managed.length });
        return { ok: false, error: 'Managed Worker cleanup refused an unsafe or non-exact record' };
      }

      const currentId = pool.current ? String(pool.current.conversation_id || '') : '';
      managed.sort((a, b) => {
        const ac = String(a.conversation_id || '') === currentId ? 1 : 0;
        const bc = String(b.conversation_id || '') === currentId ? 1 : 0;
        if (ac !== bc) return ac - bc;
        return String(a.created_at || '').localeCompare(String(b.created_at || ''));
      });

      await report('worker.cleanup_all_requested', 'info', { managed_count: managed.length });

      for (const record of managed) {
        const tab = await tabsCreate({ url: record.url, active: false });
        if (!tab || !tab.id) throw new Error(`Failed to open managed Worker ${record.conversation_id}`);
        tempTabId = tab.id;

        const response = await waitForMessage(tempTabId, {
          type: 'gah-worker-retire-conversation',
          worker_project_key: WORKER_PROJECT_KEY,
          conversation_id: record.conversation_id,
        });
        if (!response || !response.ok) {
          throw new Error((response && response.error) || `Worker cleanup UI action failed for ${record.conversation_id}`);
        }
        if (!response.delete_clicked) throw new Error(`Worker cleanup did not click delete for ${record.conversation_id}`);

        const deleted = await waitForDeletedRoute(tempTabId, record.conversation_id);
        if (!deleted) throw new Error(`Worker cleanup did not leave deleted route for ${record.conversation_id}`);

        const fresh = await cfg();
        const latest = fresh.workerPoolState;
        if (!latest || latest.project_key !== WORKER_PROJECT_KEY) throw new Error('Worker pool disappeared during cleanup');
        const remaining = (Array.isArray(latest.managed) ? latest.managed : [])
          .filter(item => item && item.conversation_id !== record.conversation_id);
        const deletingCurrent = latest.current && latest.current.conversation_id === record.conversation_id;
        const next = {
          ...latest,
          current: deletingCurrent ? null : latest.current,
          managed: remaining,
          handoff: deletingCurrent ? null : latest.handoff,
          updated_at: isoNow(),
        };
        await setStorage({ workerPoolState: next, workerPendingTabId: null });
        await report('worker.cleanup_all_deleted', 'info', {
          conversation_id: record.conversation_id,
          remaining_count: remaining.length,
        });

        try { await tabsRemove(tempTabId); } catch (_) {}
        tempTabId = null;
      }

      await setStorage({ workerPoolState: emptyPool(pool), workerPendingTabId: null });
      await report('worker.cleanup_all_complete', 'info', { deleted_count: managed.length, managed_count: 0 });
      return { ok: true, deleted_count: managed.length, managed_count: 0 };
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      await report('worker.cleanup_all_error', 'error', { message });
      return { ok: false, error: message };
    } finally {
      if (Number.isInteger(tempTabId)) {
        try { await tabsRemove(tempTabId); } catch (_) {}
      }
      cleanupAllRunning = false;
    }
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'gah-worker-cleanup-all-managed') return false;
    cleanupAllManaged()
      .then(sendResponse)
      .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  });
})();
