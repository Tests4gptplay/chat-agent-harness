'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const lanesApi = globalThis.CAHLanes;
  const taskCellApi = globalThis.CAHTaskCell;
  const uiRecoveryApi = globalThis.CAHUiRecovery;
  const ALARM = 'gah-wake-poll';
  let runningRequestId = null;

  async function getStorage(keys) { return ext.storage.local.get(keys); }
  async function tabsCreate(create) { return ext.tabs.create(create); }
  async function tabsQuery(query) { return ext.tabs.query(query); }
  async function tabsGet(id) { return ext.tabs.get(id); }
  async function tabsRemove(id) { return ext.tabs.remove(id); }
  async function tabsSendMessage(id, message) { return ext.tabs.sendMessage(id, message); }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function isExactProjectRoot(lane, url) {
    try {
      const u = new URL(String(url || ''));
      if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return false;
      const prefix = `/g/${lane.project_key}`;
      if (!u.pathname.startsWith(prefix)) return false;
      const rest = u.pathname.slice(prefix.length);
      return /^(?:-[^/]+)?\/project\/?$/.test(rest);
    } catch (_) { return false; }
  }

  async function createProjectRootTab(lane, attempts = 4) {
    let last = '';
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const tab = await tabsCreate({ url: lane.project_root_url, active: false });
        if (tab && tab.id) return tab;
        last = 'tab create returned no id';
      } catch (err) {
        last = String(err && err.message || err);
      }
      await sleep(300 + attempt * 250);
    }
    throw new Error(`Failed to open lane Project root${last ? `: ${last}` : ''}`);
  }

  async function waitForProjectRoot(tabId, lane, timeoutMs = 15000) {
    const started = Date.now();
    let lastPath = '';
    let lastError = '';
    while (Date.now() - started < timeoutMs) {
      try {
        const tab = await tabsGet(tabId);
        if (tab && tab.url) {
          try { lastPath = new URL(tab.url).pathname; } catch (_) {}
          if (isExactProjectRoot(lane, tab.url)) {
            await uiPreflight(tabId, 'project_root_ready', false);
            const probe = await tabsSendMessage(tabId, {
              type: 'gah-worker-root-probe',
              project_key: lane.project_key,
            });
            if (probe && probe.ok) return true;
            if (probe && probe.error) lastError = String(probe.error);
          }
        }
      } catch (err) {
        lastError = String(err && err.message || err);
      }
      await sleep(250);
    }
    throw new Error(
      `Timed out waiting for exact Project root: ${lane.lane_id}; ` +
      `last_path=${lastPath || '<unknown>'}${lastError ? `; last_error=${lastError}` : ''}`
    );
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
    try { parsed = JSON.parse(text); } catch (_) {
      throw new Error(`local bridge returned non-JSON HTTP ${response.status}`);
    }
    if (!parsed.ok) throw new Error(parsed.error || parsed.detail || 'local bridge request failed');
    return parsed;
  }

  async function report(c, event, level, data) {
    try {
      await localApi(c, {
        op: 'event',
        client_id: c.clientId,
        project_id: c.projectId,
        event, level, data: data || {},
      });
    } catch (_) {}
  }

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
      return { conversation_id: m[1], url: u.toString() };
    } catch (_) { return null; }
  }

  async function uiPreflight(tabId, context, requireComposer = false) {
    if (!uiRecoveryApi || typeof uiRecoveryApi.preflight !== 'function') return { ok: true, skipped: true };
    return uiRecoveryApi.preflight(tabId, {
      context,
      requireComposer,
      timeoutMs: requireComposer ? 8000 : 4000,
    });
  }

  async function sendWithWait(tabId, message, timeoutMs = 12000) {
    const started = Date.now();
    let last = '';
    while (Date.now() - started < timeoutMs) {
      try {
        const response = await tabsSendMessage(tabId, message);
        if (response) return response;
      } catch (err) {
        last = String(err && err.message || err);
      }
      await sleep(250);
    }
    throw new Error(`content script unavailable${last ? `: ${last}` : ''}`);
  }

  async function listProjectConversations(rootTabId, lane) {
    await uiPreflight(rootTabId, 'project_conversation_list', false);
    const response = await sendWithWait(rootTabId, {
      type: 'gah-project-list-conversations',
      project_key: lane.project_key,
    }, 15000);
    if (!response || !response.ok) throw new Error((response && response.error) || 'Project conversation listing failed');
    return Array.isArray(response.conversations) ? response.conversations : [];
  }

  async function deleteProjectConversationFromRoot(rootTabId, lane, target) {
    await uiPreflight(rootTabId, 'project_conversation_delete', false);
    const response = await sendWithWait(rootTabId, {
      type: 'gah-project-delete-conversation',
      project_key: lane.project_key,
      conversation_id: target.conversation_id,
    }, 15000);
    if (!response || !response.ok || !response.deleted) {
      throw new Error((response && response.error) || 'Project-root conversation delete failed');
    }
    return {
      deleted: true,
      conversation_id: target.conversation_id,
      confirmation_clicked: Boolean(response.confirmation_clicked),
    };
  }

  async function clearLaneProject(control) {
    const c = await cfg();
    const registry = await lanesApi.getRegistry();
    const lane = lanesApi.findLane(registry, String(control.control_lane_id || ''), String(control.worker_project_key || ''));
    if (!lane) throw new Error('lane_clear target is not registered');
    if (String(control.scope || '') !== 'all_project_conversations') throw new Error('Unsupported lane_clear scope');

    let rootTabId = null;
    let deletedCount = 0;
    try {
      const root = await createProjectRootTab(lane);
      rootTabId = root.id;
      await waitForProjectRoot(rootTabId, lane);

      await report(c, 'lane.clear_started', 'info', {
        request_id: control.request_id,
        lane_id: lane.lane_id,
        worker_project_key: lane.project_key,
      });

      // The Project root is authoritative for what still exists. Local pool
      // metadata can legitimately contain stale conversation ids after manual
      // deletion, UI migration, or a prior failed cleanup, so never manufacture
      // delete targets from the pool. Delete one visible card per round and
      // rediscover after every DOM mutation because React may rebuild the list.
      for (let round = 0; round < 40; round += 1) {
        const discovered = await listProjectConversations(rootTabId, lane);
        if (!discovered.length) {
          await sleep(500);
          const verify = await listProjectConversations(rootTabId, lane);
          if (!verify.length) {
            const fresh = await lanesApi.getRegistry();
            const freshLane = lanesApi.findLane(fresh, lane.lane_id, lane.project_key);
            if (!freshLane) throw new Error('Lane disappeared during clear');
            freshLane.pool_state = lanesApi.emptyPool(freshLane);
            freshLane.pending_tab_id = null;
            fresh.lanes = fresh.lanes.map(x => x.lane_id === freshLane.lane_id ? freshLane : x);
            await lanesApi.saveRegistry(fresh);

            const completed = await localApi(c, {
              op: 'lane_clear_complete',
              client_id: c.clientId,
              project_id: c.projectId,
              request_id: control.request_id,
              lane_id: lane.lane_id,
              worker_project_key: lane.project_key,
              deleted_count: deletedCount,
              remaining_count: 0,
            });
            await report(c, 'lane.clear_complete', 'info', {
              request_id: control.request_id,
              lane_id: lane.lane_id,
              worker_project_key: lane.project_key,
              deleted_count: deletedCount,
              bridge_completed: Boolean(completed.completed),
            });
            return { ok: true, deleted_count: deletedCount, remaining_count: 0 };
          }
          continue;
        }

        const target = discovered[0];
        try {
          const result = await deleteProjectConversationFromRoot(rootTabId, lane, target);
          if (result.deleted) deletedCount += 1;
        } catch (err) {
          const message = String(err && err.message || err);
          // A card can disappear between discovery and click because another
          // cleanup/manual action already removed it. Rediscover instead of
          // turning stale local/UI state into a permanent APPLYING loop.
          if (!/found 0|already.*absent|not found/i.test(message)) throw err;
          await report(c, 'lane.clear_target_already_absent', 'info', {
            request_id: control.request_id,
            lane_id: lane.lane_id,
            conversation_id: target && target.conversation_id || null,
            message: message.slice(0, 500),
          });
        }
        await sleep(650);
      }

      const remaining = await listProjectConversations(rootTabId, lane);
      throw new Error(`lane_clear did not reach empty Project; remaining=${remaining.length}`);
    } finally {
      if (Number.isInteger(rootTabId)) {
        try { await tabsRemove(rootTabId); } catch (_) {}
      }
    }
  }

  async function resolveTaskCellTab() {
    if (!taskCellApi) throw new Error('Task Cell registry unavailable');
    const binding = await taskCellApi.getBinding();
    if (!binding.enabled) throw new Error('Task Cell binding is disabled');
    const tabs = await tabsQuery({ url: 'https://chatgpt.com/*' });
    const matches = (tabs || []).filter(tab => taskCellApi.matchesUrl(binding, tab && tab.url));
    if (matches.length === 1) return { binding, tab: matches[0] };
    if (matches.length > 1) {
      const active = matches.filter(tab => tab && tab.active);
      if (active.length === 1) return { binding, tab: active[0] };
      throw new Error(`Expected one Task Cell tab, found ${matches.length}`);
    }

    const created = await tabsCreate({ url: binding.project_root_url, active: false });
    if (!created || !created.id) throw new Error('Failed to open Task Cell Project root');
    const started = Date.now();
    while (Date.now() - started < 15000) {
      const tab = await tabsGet(created.id);
      if (tab && taskCellApi.matchesUrl(binding, tab.url)) return { binding, tab };
      await sleep(250);
    }
    throw new Error('Timed out waiting for Task Cell Project tab');
  }

  async function processTaskCellPrompt(control, c) {
    const requestId = String(control.request_id || '');
    const prompt = String(control.prompt || '').trim();
    if (!requestId || !prompt) throw new Error('task_cell_prompt requires request_id and prompt');
    if (prompt.length > 3600) throw new Error('task_cell_prompt is too long');

    const resolved = await resolveTaskCellTab();
    const tabId = Number(resolved.tab && resolved.tab.id || 0);
    if (!tabId) throw new Error('Task Cell tab id missing');

    const marker = `GAH_WAKE v=1 id=${requestId} project=${String(c.projectId).replace(/\s+/g, '_')}`;
    await uiPreflight(tabId, 'task_cell_prompt', true);
    const probe = await sendWithWait(tabId, { type: 'gah-probe-wake-marker', marker }, 8000);
    if (probe && probe.ok && probe.marker_visible) {
      if (!probe.response_started) {
        return { ok: true, pending: true, request_id: requestId, reason: 'marker_visible_response_pending' };
      }
      const completed = await localApi(c, {
        op: 'task_cell_prompt_complete',
        client_id: c.clientId,
        project_id: c.projectId,
        request_id: requestId,
        task_cell_project_key: resolved.binding.project_key,
        response_started: true,
      });
      return { ok: true, deduped: true, completed: Boolean(completed.completed), request_id: requestId };
    }

    const response = await sendWithWait(tabId, {
      type: 'gah-submit-wake',
      marker,
      text: `${marker}\n${prompt}`,
    }, 20000);
    if (!response || !response.ok) {
      if (response && response.outcome_unknown) {
        await report(c, 'task_cell.prompt_outcome_unknown', 'warn', {
          request_id: requestId,
          task_cell_project_key: resolved.binding.project_key,
          error: String(response.error || '').slice(0, 800),
        });
        return { ok: true, pending: true, request_id: requestId, reason: 'send_outcome_unknown' };
      }
      throw new Error((response && response.error) || 'Task Cell prompt submission failed');
    }

    const completed = await localApi(c, {
      op: 'task_cell_prompt_complete',
      client_id: c.clientId,
      project_id: c.projectId,
      request_id: requestId,
      task_cell_project_key: resolved.binding.project_key,
      response_started: Boolean(response.response_started),
    });
    await report(c, 'task_cell.prompt_delivered', 'info', {
      request_id: requestId,
      task_cell_project_key: resolved.binding.project_key,
      response_started: Boolean(response.response_started),
      bridge_completed: Boolean(completed.completed),
    });
    return {
      ok: true,
      completed: Boolean(completed.completed),
      request_id: requestId,
      response_started: Boolean(response.response_started),
    };
  }

  async function processLanePoolReset(control, c) {
    const registry = await lanesApi.getRegistry();
    const lane = lanesApi.findLane(
      registry,
      String(control.control_lane_id || ''),
      String(control.worker_project_key || '')
    );
    if (!lane) throw new Error('lane_pool_reset target is not registered');

    lane.pool_state = lanesApi.emptyPool(lane);
    lane.pending_tab_id = null;
    registry.lanes = registry.lanes.map(item => item.lane_id === lane.lane_id ? lane : item);
    await lanesApi.saveRegistry(registry);

    const completed = await localApi(c, {
      op: 'lane_pool_reset_complete',
      client_id: c.clientId,
      project_id: c.projectId,
      request_id: String(control.request_id || ''),
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
    });
    await report(c, 'lane.pool_reset_complete', 'warn', {
      request_id: control.request_id,
      lane_id: lane.lane_id,
      worker_project_key: lane.project_key,
      bridge_completed: Boolean(completed.completed),
    });
    return { ok: true, completed: Boolean(completed.completed), lane_id: lane.lane_id };
  }

  function projectConversationId(projectKey, url) {
    try {
      const u = new URL(String(url || ''));
      if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return null;
      const prefix = `/g/${projectKey}`;
      if (u.pathname.startsWith(prefix)) {
        const rest = u.pathname.slice(prefix.length);
        const m = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
        if (m) return m[1];
      }
      return null;
    } catch (_) { return null; }
  }

  async function processTaskCellClear(control, c) {
    if (!taskCellApi) throw new Error('Task Cell registry unavailable');
    const binding = await taskCellApi.getBinding();
    const targetRequestId = String(control.target_request_id || '');
    if (!targetRequestId) throw new Error('task_cell_clear requires target_request_id');
    const marker = `GAH_WAKE v=1 id=${targetRequestId} project=${String(c.projectId).replace(/\s+/g, '_')}`;

    const tabs = await tabsQuery({ url: 'https://chatgpt.com/*' });
    const projectTabs = (tabs || []).filter(tab => taskCellApi.matchesUrl(binding, tab && tab.url));
    const marked = [];
    for (const tab of projectTabs) {
      if (!tab || !tab.id) continue;
      try {
        const probe = await sendWithWait(tab.id, { type: 'gah-probe-wake-marker', marker }, 5000);
        if (probe && probe.ok && probe.marker_visible) marked.push(tab);
      } catch (_) {}
    }
    if (marked.length !== 1) {
      throw new Error(`Expected exactly one marked Task Cell smoke conversation, found ${marked.length}`);
    }

    const targetTab = marked[0];
    const conversationId = projectConversationId(binding.project_key, targetTab.url);
    if (!conversationId) throw new Error('Marked Task Cell tab is not on a conversation route');

    const rootLike = {
      lane_id: 'task-cell',
      project_key: binding.project_key,
      project_root_url: binding.project_root_url,
    };
    let rootTabId = null;
    try {
      const root = await createProjectRootTab(rootLike);
      rootTabId = root.id;
      await waitForProjectRoot(rootTabId, rootLike);
      await deleteProjectConversationFromRoot(rootTabId, rootLike, {
        conversation_id: conversationId,
        url: String(targetTab.url || ''),
      });
    } finally {
      if (Number.isInteger(rootTabId)) {
        try { await tabsRemove(rootTabId); } catch (_) {}
      }
    }
    try { await tabsRemove(targetTab.id); } catch (_) {}

    const completed = await localApi(c, {
      op: 'task_cell_clear_complete',
      client_id: c.clientId,
      project_id: c.projectId,
      request_id: String(control.request_id || ''),
      task_cell_project_key: binding.project_key,
      target_request_id: targetRequestId,
      deleted_conversation_id: conversationId,
    });
    await report(c, 'task_cell.clear_complete', 'info', {
      request_id: control.request_id,
      target_request_id: targetRequestId,
      task_cell_project_key: binding.project_key,
      bridge_completed: Boolean(completed.completed),
    });
    return { ok: true, completed: Boolean(completed.completed), deleted_conversation_id: conversationId };
  }

  async function processControl() {
    if (runningRequestId) return { ok: true, idle: 'control_request_running' };
    const c = await cfg();
    if (!c.clientId) return { ok: true, idle: 'client_unconfigured' };
    const status = await localApi(c, {
      op: 'control_status',
      client_id: c.clientId,
      project_id: c.projectId,
    });
    const control = status.control_request;
    if (!control) return { ok: true, idle: 'no_control_request' };

    const kind = String(control.kind || '');
    const controlStatus = String(control.status || '');
    if (kind === 'task_cell_prompt' && controlStatus === 'PENDING') {
      runningRequestId = String(control.request_id || '');
      try {
        return await processTaskCellPrompt(control, c);
      } catch (err) {
        const message = String(err && err.message || err).slice(0, 1000);
        await report(c, 'task_cell.prompt_error', 'error', {
          request_id: runningRequestId,
          message,
        });
        return { ok: false, error: message };
      } finally {
        runningRequestId = null;
      }
    }

    if (kind === 'lane_pool_reset' && controlStatus === 'PENDING') {
      runningRequestId = String(control.request_id || '');
      try {
        return await processLanePoolReset(control, c);
      } catch (err) {
        const message = String(err && err.message || err).slice(0, 1000);
        await report(c, 'lane.pool_reset_error', 'error', { request_id: runningRequestId, message });
        return { ok: false, error: message };
      } finally {
        runningRequestId = null;
      }
    }

    if (kind === 'task_cell_clear' && controlStatus === 'PENDING') {
      runningRequestId = String(control.request_id || '');
      try {
        return await processTaskCellClear(control, c);
      } catch (err) {
        const message = String(err && err.message || err).slice(0, 1000);
        await report(c, 'task_cell.clear_error', 'error', { request_id: runningRequestId, message });
        return { ok: false, error: message };
      } finally {
        runningRequestId = null;
      }
    }

    if (kind !== 'lane_clear' || !['PENDING', 'APPLYING'].includes(controlStatus)) {
      return { ok: true, idle: 'no_supported_control_request' };
    }

    runningRequestId = String(control.request_id || '');
    try {
      await localApi(c, {
        op: 'lane_clear_begin',
        client_id: c.clientId,
        project_id: c.projectId,
        request_id: runningRequestId,
        lane_id: control.control_lane_id,
        worker_project_key: control.worker_project_key,
      });
      return await clearLaneProject({ ...control, status: 'APPLYING' });
    } catch (err) {
      const message = String(err && err.message || err).slice(0, 1000);
      await report(c, 'lane.clear_error', 'error', {
        request_id: runningRequestId,
        lane_id: control.control_lane_id || null,
        message,
      });
      return { ok: false, error: message };
    } finally {
      runningRequestId = null;
    }
  }

  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => {
      if (alarm && alarm.name === ALARM) {
        processControl().catch(() => null);
      }
    });
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'gah-process-control-now') return false;
    processControl().then(sendResponse).catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  });

  setTimeout(() => {
    processControl().catch(() => null);
  }, 500);
})();
