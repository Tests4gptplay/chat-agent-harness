'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const lanesApi = globalThis.CAHLanes;
  const taskCellApi = globalThis.CAHTaskCell;
  const ALARM = 'gah-wake-poll';
  let maintenanceRunning = false;

  function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

  async function tabsQuery(query) { return ext.tabs.query(query); }
  async function tabsGet(tabId) { return ext.tabs.get(tabId); }
  async function tabsSendMessage(tabId, message) { return ext.tabs.sendMessage(tabId, message); }

  function normalizedUrl(value) {
    try {
      const u = new URL(String(value || ''));
      if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return '';
      u.hash = '';
      u.search = '';
      return u.toString().replace(/\/$/, '');
    } catch (_) { return ''; }
  }

  async function preflight(tabId, options = {}) {
    const id = Number(tabId || 0);
    if (!Number.isInteger(id) || id <= 0) throw new Error('ui recovery requires a valid tab id');
    const context = String(options.context || 'managed_chatgpt_ui').slice(0, 120);
    const requireComposer = Boolean(options.requireComposer);
    const timeoutMs = Math.max(1000, Math.min(15000, Number(options.timeoutMs || 6000)));
    const started = Date.now();
    let last = '';

    while (Date.now() - started < timeoutMs) {
      try {
        const tab = await tabsGet(id);
        if (!tab || !normalizedUrl(tab.url)) throw new Error('managed ChatGPT tab is unavailable');
        const response = await tabsSendMessage(id, {
          type: 'cah-ui-recovery-preflight',
          context,
          require_composer: requireComposer,
        });
        if (response && response.ok) {
          if (!requireComposer || response.composer_ready) return response;
          last = String(response.composer_error || 'composer not ready');
        } else {
          last = String(response && response.error || 'ui recovery preflight rejected');
        }
      } catch (err) {
        last = String(err && err.message || err);
      }
      await sleep(250);
    }

    throw new Error(`UI_RECOVERY_TIMEOUT:${context}${last ? `: ${last}` : ''}`);
  }

  async function managedTabIds() {
    const ids = new Set();
    const tabs = await tabsQuery({ url: ['https://chatgpt.com/*'] });
    const all = Array.isArray(tabs) ? tabs : [];

    if (lanesApi && typeof lanesApi.getRegistry === 'function') {
      try {
        const registry = await lanesApi.getRegistry();
        for (const lane of registry.lanes || []) {
          if (!lane) continue;
          const pending = Number(lane.pending_tab_id || 0);
          if (Number.isInteger(pending) && pending > 0) ids.add(pending);

          const pool = lane.pool_state || {};
          const currentUrl = normalizedUrl(pool.current && pool.current.url);
          if (currentUrl) {
            for (const tab of all) {
              if (tab && tab.id && normalizedUrl(tab.url) === currentUrl) ids.add(tab.id);
            }
          }
        }
      } catch (_) {}
    }

    if (taskCellApi && typeof taskCellApi.getBinding === 'function') {
      try {
        const binding = await taskCellApi.getBinding();
        for (const tab of all) {
          if (tab && tab.id && taskCellApi.matchesUrl(binding, tab.url)) ids.add(tab.id);
        }
      } catch (_) {}
    }

    return [...ids].slice(0, 12);
  }

  async function maintenance() {
    if (maintenanceRunning) return { ok: true, idle: 'already_running' };
    maintenanceRunning = true;
    try {
      const ids = await managedTabIds();
      let recovered = 0;
      for (const tabId of ids) {
        try {
          const result = await preflight(tabId, {
            context: 'background_managed_tab_maintenance',
            requireComposer: false,
            timeoutMs: 2500,
          });
          if (result && result.handled) recovered += 1;
        } catch (_) {}
      }
      return { ok: true, checked: ids.length, recovered };
    } finally {
      maintenanceRunning = false;
    }
  }

  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => {
      if (alarm && alarm.name === ALARM) maintenance().catch(() => null);
    });
  }

  setTimeout(() => maintenance().catch(() => null), 1500);

  globalThis.CAHUiRecovery = Object.freeze({
    preflight,
    maintenance,
  });
})();
