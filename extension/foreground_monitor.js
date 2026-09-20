'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const ALARM = 'gah-wake-poll';
  const WORKER_PROJECT_KEY = 'g-p-examplelane00';
  const HEARTBEAT_MS = 10 * 60 * 1000;
  const TERMINAL = new Set(['SUCCESS', 'FAILURE', 'BLOCKED', 'CANCELLED']);
  let monitorRunning = false;

  async function getStorage(keys) { return ext.storage.local.get(keys); }
  async function setStorage(value) { return ext.storage.local.set(value); }
  async function tabsGet(id) { return ext.tabs.get(id); }
  async function tabsQuery(query) { return ext.tabs.query(query); }
  async function tabsCreate(create) { return ext.tabs.create(create); }
  async function tabsSendMessage(id, message) { return ext.tabs.sendMessage(id, message); }
  function isoNow() { return new Date().toISOString(); }

  function isChatGptUrl(url) {
    try {
      const u = new URL(url || '');
      return u.protocol === 'https:' && u.hostname === 'chatgpt.com';
    } catch (_) {
      return false;
    }
  }

  function isWorkerProjectUrl(url) {
    try {
      const u = new URL(url || '');
      return u.hostname === 'chatgpt.com' && u.pathname.startsWith(`/g/${WORKER_PROJECT_KEY}`);
    } catch (_) {
      return false;
    }
  }

  function conversationKey(url) {
    try {
      const u = new URL(url || '');
      if (u.hostname !== 'chatgpt.com') return '';
      const c = u.pathname.match(/\/c\/([A-Za-z0-9-]+)/);
      if (c) return `c:${c[1]}`;
      const qp = u.searchParams.get('conversation') || u.searchParams.get('conversationId') || u.searchParams.get('conversation_id');
      if (qp) return `q:${qp}`;
      return `path:${u.pathname}${u.search}`;
    } catch (_) {
      return '';
    }
  }

  function safeTaskId(value) {
    const raw = String(value || '').trim();
    const safe = raw.replace(/[^A-Za-z0-9._:-]+/g, '_').slice(0, 128);
    return safe.length >= 3 ? safe : 'task-unknown';
  }

  function taskKey(task) {
    return `${String(task.task_id || '')}|${String(task.started_at || '')}`;
  }

  async function cfg() {
    const d = await getStorage([
      'enabled', 'clientId', 'localEndpoint', 'projectId', 'conversationUrl',
      'boundTabId', 'autoOpen', 'foregroundMonitorState',
    ]);
    return {
      enabled: Boolean(d.enabled),
      clientId: d.clientId || '',
      localEndpoint: d.localEndpoint || 'http://127.0.0.1:8765/api',
      projectId: d.projectId || 'git-agent-harness',
      conversationUrl: d.conversationUrl || '',
      boundTabId: Number.isInteger(d.boundTabId) ? d.boundTabId : null,
      autoOpen: Boolean(d.autoOpen),
      foregroundMonitorState: d.foregroundMonitorState || null,
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

  async function report(c, event, level, data) {
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

  async function findForegroundTab(c, openIfMissing) {
    const configuredKey = c.conversationUrl ? conversationKey(c.conversationUrl) : '';
    if (c.conversationUrl
        && (!isChatGptUrl(c.conversationUrl)
            || isWorkerProjectUrl(c.conversationUrl)
            || !/^(c|q):/.test(configuredKey))) {
      throw new Error('Foreground target must be one exact non-Worker ChatGPT conversation');
    }

    if (Number.isInteger(c.boundTabId)) {
      try {
        const tab = await tabsGet(c.boundTabId);
        if (tab && tab.id && isChatGptUrl(tab.url) && !isWorkerProjectUrl(tab.url)
            && (!configuredKey || conversationKey(tab.url) === configuredKey)) return tab;
      } catch (_) {}
    }

    if (!c.conversationUrl || !isChatGptUrl(c.conversationUrl) || isWorkerProjectUrl(c.conversationUrl)) {
      throw new Error('Foreground ChatGPT conversation is not safely bound');
    }

    const wanted = conversationKey(c.conversationUrl);
    const tabs = await tabsQuery({ url: ['https://chatgpt.com/*'] });
    const existing = tabs.find(tab => tab.url && !isWorkerProjectUrl(tab.url) && conversationKey(tab.url) === wanted);
    if (existing) {
      await setStorage({ boundTabId: existing.id });
      return existing;
    }

    if (!openIfMissing && !c.autoOpen) return null;
    const created = await tabsCreate({ url: c.conversationUrl, active: false });
    if (created && created.id) await setStorage({ boundTabId: created.id });
    return created;
  }

  async function sendWithWait(tabId, text, timeoutMs = 10000) {
    const started = Date.now();
    let last = '';
    while (Date.now() - started < timeoutMs) {
      try {
        const response = await tabsSendMessage(tabId, {
          type: 'gah-submit-foreground-monitor',
          text,
        });
        if (response) return response;
      } catch (err) {
        last = String(err && err.message || err);
      }
      await new Promise(r => setTimeout(r, 250));
    }
    throw new Error(`Foreground content script unavailable${last ? `: ${last}` : ''}`);
  }

  function noticeText(task, event) {
    const id = safeTaskId(task.task_id);
    const status = String(task.status || 'RUNNING');
    if (event === 'started') {
      return [
        `GAH_FOREGROUND v=1 task=${id} event=started status=RUNNING`,
        'Foreground task-monitor signal only. Reply in Chinese with one concise sentence that the background task has started and no user action is needed. Do not duplicate or restart the background work in this foreground turn.',
      ].join('\n');
    }
    if (event === 'heartbeat') {
      return [
        `GAH_FOREGROUND v=1 task=${id} event=heartbeat status=RUNNING`,
        'Foreground task-monitor heartbeat only. Reply in Chinese with one short waiting update. Do not duplicate, restart, or take over the background task; it is still running under Git/Worker authority.',
      ].join('\n');
    }
    return [
      `GAH_FOREGROUND v=1 task=${id} event=terminal status=${status}`,
      'Foreground task-monitor terminal doorbell. Read example-owner/cah-private/state/chatgpt.json directly, inspect foreground_task.result_ref and the referenced durable evidence, then report the final result in this foreground conversation. Do not treat this doorbell alone as proof of success or failure.',
    ].join('\n');
  }

  async function submitNotice(c, task, event) {
    const openIfMissing = event === 'terminal';
    const tab = await findForegroundTab(c, openIfMissing);
    if (!tab || !tab.id) return { ok: false, retry: 'foreground_tab_closed' };
    const response = await sendWithWait(tab.id, noticeText(task, event));
    if (!response || !response.ok) {
      return { ok: false, retry: (response && response.error) || 'foreground_submit_failed' };
    }
    await setStorage({ boundTabId: tab.id });
    return { ok: true, tab_id: tab.id };
  }

  async function processForegroundMonitor() {
    if (monitorRunning) return { ok: true, idle: 'monitor_running' };
    monitorRunning = true;
    try {
      const c = await cfg();
      if (!c.enabled) return { ok: true, idle: 'disabled' };
      if (!c.clientId || !c.localEndpoint) return { ok: true, idle: 'local_bridge_unconfigured' };
      if ((!c.conversationUrl && !Number.isInteger(c.boundTabId))) return { ok: true, idle: 'foreground_unbound' };

      const snapshot = await localApi(c, {
        op: 'foreground_task_status',
        client_id: c.clientId,
        project_id: c.projectId,
      });
      const task = snapshot.foreground_task;
      if (!task) return { ok: true, idle: 'no_foreground_task' };

      const status = String(task.status || '');
      const key = taskKey(task);
      if (!key || !status) return { ok: true, idle: 'invalid_foreground_task' };

      const previous = c.foregroundMonitorState && c.foregroundMonitorState.key === key
        ? c.foregroundMonitorState
        : { key, task_id: String(task.task_id || ''), last_event: null, last_notice_at: null, terminal_status: null };

      let event = null;
      if (status === 'RUNNING') {
        if (!previous.last_event) {
          event = 'started';
        } else {
          const last = Date.parse(previous.last_notice_at || '');
          if (!Number.isFinite(last) || Date.now() - last >= HEARTBEAT_MS) event = 'heartbeat';
        }
      } else if (TERMINAL.has(status)) {
        if (previous.last_event !== 'terminal' || previous.terminal_status !== status) event = 'terminal';
      } else {
        return { ok: true, idle: 'unsupported_foreground_status' };
      }

      if (!event) return { ok: true, idle: 'notice_not_due' };
      const sent = await submitNotice(c, task, event);
      if (!sent.ok) {
        await report(c, 'foreground.notice_deferred', 'info', {
          task_id: safeTaskId(task.task_id),
          event,
          reason: String(sent.retry || 'unknown').slice(0, 300),
        });
        return sent;
      }

      const next = {
        key,
        task_id: String(task.task_id || ''),
        last_event: event,
        last_notice_at: isoNow(),
        terminal_status: event === 'terminal' ? status : null,
        updated_at: isoNow(),
      };
      await setStorage({ foregroundMonitorState: next });
      await report(c, 'foreground.notice_submitted', 'info', {
        task_id: safeTaskId(task.task_id),
        event,
        status,
        tab_id: sent.tab_id,
      });
      return { ok: true, event, status, task_id: task.task_id };
    } catch (err) {
      const c = await cfg().catch(() => null);
      const message = String(err && err.message || err).slice(0, 1000);
      if (c) await report(c, 'foreground.monitor_error', 'warn', { message });
      return { ok: false, error: message };
    } finally {
      monitorRunning = false;
    }
  }

  if (ext.alarms && ext.alarms.onAlarm) {
    ext.alarms.onAlarm.addListener(alarm => {
      if (alarm && alarm.name === ALARM) processForegroundMonitor();
    });
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'gah-foreground-monitor-now') return false;
    processForegroundMonitor().then(sendResponse);
    return true;
  });

  processForegroundMonitor();
})();
