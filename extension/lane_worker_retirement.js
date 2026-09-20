'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const lanesApi = globalThis.CAHLanes;
  const ALARM = 'gah-wake-poll';
  let running = false;

  async function getStorage(keys) { return ext.storage.local.get(keys); }
  async function tabsCreate(create) { return ext.tabs.create(create); }
  async function tabsGet(id) { return ext.tabs.get(id); }
  async function tabsRemove(id) { return ext.tabs.remove(id); }
  async function tabsSendMessage(id, message) { return ext.tabs.sendMessage(id, message); }
  function now() { return new Date().toISOString(); }

  async function cfg() {
    const d = await getStorage(['clientId', 'localEndpoint', 'projectId']);
    return {
      clientId: d.clientId || '',
      localEndpoint: d.localEndpoint || lanesApi.DEFAULT_ENDPOINT,
      projectId: d.projectId || lanesApi.DEFAULT_PROJECT_ID,
    };
  }

  async function localApi(c, payload) {
    const response = await fetch(c.localEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-GAH-Bridge': '1' },
      body: JSON.stringify(payload),
    });
    const parsed = JSON.parse(await response.text());
    if (!parsed.ok) throw new Error(parsed.error || 'local bridge request failed');
    return parsed;
  }

  function conversationLocation(lane, url) {
    try {
      const u = new URL(url || '');
      if (u.hostname !== 'chatgpt.com') return null;
      const prefix = `/g/${lane.project_key}`;
      if (!u.pathname.startsWith(prefix)) return null;
      const rest = u.pathname.slice(prefix.length);
      const m = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
      return m ? { conversationId: m[1] } : null;
    } catch (_) { return null; }
  }

  async function waitForMessage(tabId, message, timeoutMs = 10000) {
    const started = Date.now();
    let last = '';
    while (Date.now() - started < timeoutMs) {
      try {
        const response = await tabsSendMessage(tabId, message);
        if (response) return response;
      } catch (e) { last = String(e && e.message || e); }
      await new Promise(r => setTimeout(r, 200));
    }
    throw new Error(`Timed out waiting for retirement content script${last ? `: ${last}` : ''}`);
  }

  async function waitForDeleted(tabId, lane, conversationId, timeoutMs = 15000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      try {
        const tab = await tabsGet(tabId);
        if (!tab) return true;
        const loc = conversationLocation(lane, tab.url || '');
        if (!loc || loc.conversationId !== conversationId) return true;
      } catch (_) { return true; }
      await new Promise(r => setTimeout(r, 200));
    }
    return false;
  }

  function candidateFor(lane) {
    const pool = lane.pool_state || lanesApi.emptyPool(lane);
    const current = pool.current;
    if (!current || current.status !== 'current' || current.handoff_verified !== true) return null;
    if (!pool.handoff || pool.handoff.status !== 'verified' || pool.handoff.handoff_id !== current.handoff_id) return null;
    const managed = Array.isArray(pool.managed) ? pool.managed : [];
    if (managed.length <= Number(pool.soft_limit || lanesApi.SOFT_LIMIT)) return null;
    const candidates = managed.filter(item => item
      && item.managed_by === 'gah-worker'
      && item.project_key === lane.project_key
      && item.status === 'standby'
      && item.handoff_verified === true
      && item.conversation_id !== current.conversation_id
      && conversationLocation(lane, item.url)
      && conversationLocation(lane, item.url).conversationId === item.conversation_id);
    candidates.sort((a,b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
    return candidates[0] || null;
  }

  async function cleanupLane(registry, lane) {
    const candidate = candidateFor(lane);
    if (!candidate) return null;
    const pool = lane.pool_state;
    const current = pool.current;
    const c = await cfg();
    const ack = await localApi(c, {
      op: 'worker_takeover_status',
      client_id: c.clientId,
      project_id: c.projectId,
      handoff_id: current.handoff_id,
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
    });
    if (!ack.matched || ack.last_pool_takeover_id !== current.handoff_id) return null;

    lane.pool_state = {
      ...pool,
      managed: pool.managed.map(item => item && item.conversation_id === candidate.conversation_id
        ? { ...item, status: 'retire_pending', retire_started_at: now() } : item),
      updated_at: now(),
    };
    registry.lanes = registry.lanes.map(x => x.lane_id === lane.lane_id ? lane : x);
    await lanesApi.saveRegistry(registry);

    let tabId = null;
    try {
      const tab = await tabsCreate({ url: candidate.url, active: false });
      if (!tab || !tab.id) throw new Error('Failed to open retirement candidate');
      tabId = tab.id;
      const response = await waitForMessage(tabId, {
        type: 'gah-worker-retire-conversation',
        worker_project_key: lane.project_key,
        conversation_id: candidate.conversation_id,
      });
      if (!response || !response.ok || !response.delete_clicked) throw new Error((response && response.error) || 'retirement delete failed');
      if (!await waitForDeleted(tabId, lane, candidate.conversation_id)) throw new Error('retired route did not disappear');

      const fresh = await lanesApi.getRegistry();
      const freshLane = fresh.lanes.find(x => x.lane_id === lane.lane_id);
      if (!freshLane || !freshLane.pool_state || !freshLane.pool_state.current
          || freshLane.pool_state.current.conversation_id !== current.conversation_id) {
        throw new Error('lane current Worker changed during retirement');
      }
      freshLane.pool_state = {
        ...freshLane.pool_state,
        managed: freshLane.pool_state.managed.filter(item => item && item.conversation_id !== candidate.conversation_id),
        updated_at: now(),
      };
      fresh.lanes = fresh.lanes.map(x => x.lane_id === lane.lane_id ? freshLane : x);
      await lanesApi.saveRegistry(fresh);
      return { lane_id: lane.lane_id, retired: candidate.conversation_id };
    } finally {
      if (Number.isInteger(tabId)) { try { await tabsRemove(tabId); } catch (_) {} }
    }
  }

  async function cleanupOnce() {
    if (running) return;
    running = true;
    try {
      const registry = await lanesApi.getRegistry();
      for (const lane of registry.lanes || []) {
        try { await cleanupLane(registry, lane); } catch (_) {}
      }
    } finally { running = false; }
  }

  if (ext.storage && ext.storage.onChanged) {
    ext.storage.onChanged.addListener((changes, area) => {
      if (area === 'local' && changes[laneApiKey()]) setTimeout(cleanupOnce, 100);
    });
  }
  function laneApiKey() { return lanesApi.STORAGE_KEY; }

  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => { if (alarm && alarm.name === ALARM) cleanupOnce(); });
  }
  cleanupOnce();
})();
