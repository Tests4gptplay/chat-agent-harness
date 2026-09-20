'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }

  function safeAttr(el, name) {
    const value = el.getAttribute && el.getAttribute(name);
    return value ? String(value) : null;
  }

  function currentConversationId(projectKey) {
    if (!projectKey || location.protocol !== 'https:' || location.hostname !== 'chatgpt.com') return null;
    const prefix = `/g/${projectKey}`;
    if (!location.pathname.startsWith(prefix)) return null;
    const rest = location.pathname.slice(prefix.length);
    const match = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
    return match ? match[1] : null;
  }

  async function waitForUnique(selector, timeoutMs = 5000) {
    const started = Date.now();
    let lastCount = 0;
    while (Date.now() - started < timeoutMs) {
      const found = [...document.querySelectorAll(selector)].filter(visible);
      lastCount = found.length;
      if (found.length === 1) return found[0];
      if (found.length > 1) throw new Error(`Expected one visible ${selector}, found ${found.length}`);
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error(`Expected one visible ${selector}, found ${lastCount}`);
  }

  function deleteSignal(el) {
    return [
      safeAttr(el, 'data-testid'),
      safeAttr(el, 'aria-label'),
      safeAttr(el, 'title'),
      el.innerText,
      el.textContent,
    ].filter(Boolean).join(' ').trim().toLowerCase();
  }

  async function findConfirmDelete(timeoutMs = 5000) {
    const started = Date.now();
    let lastDialogs = 0;
    let lastCandidates = 0;
    while (Date.now() - started < timeoutMs) {
      const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
      lastDialogs = dialogs.length;
      if (dialogs.length > 1) throw new Error(`Expected at most one visible delete dialog, found ${dialogs.length}`);
      if (dialogs.length === 1) {
        const candidates = [...dialogs[0].querySelectorAll('button, [role="button"]')].filter(el => {
          if (!visible(el) || el.disabled || safeAttr(el, 'aria-disabled') === 'true') return false;
          const signal = deleteSignal(el);
          return /delete|删除/.test(signal) && !/cancel|取消/.test(signal);
        });
        lastCandidates = candidates.length;
        if (candidates.length === 1) return candidates[0];
        if (candidates.length > 1) throw new Error(`Expected one delete confirmation button, found ${candidates.length}`);
      }
      if (currentConversationId(projectKey) === null) return null;
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error(`Delete confirmation not resolved; dialogs=${lastDialogs} candidates=${lastCandidates}`);
  }

  async function retireWorkerConversation(message) {
    const expectedId = String(message.conversation_id || '');
    const projectKey = String(message.worker_project_key || '');
    if (!/^g-p-[A-Za-z0-9]+$/.test(projectKey)) throw new Error('Invalid Worker Project key');
    if (!/^[A-Za-z0-9-]{8,128}$/.test(expectedId)) throw new Error('Invalid Worker conversation id');
    const actualId = currentConversationId(projectKey);
    if (!actualId || actualId !== expectedId) {
      throw new Error('Retirement page does not match the exact Worker conversation id');
    }

    const options = await waitForUnique('button[data-testid="conversation-options-button"]');
    options.click();
    await new Promise(r => setTimeout(r, 180));

    const deleteItem = await waitForUnique('[data-testid="delete-chat-menu-item"]');
    if (deleteItem.disabled || safeAttr(deleteItem, 'aria-disabled') === 'true') {
      throw new Error('Worker delete menu item is disabled');
    }
    deleteItem.click();
    await new Promise(r => setTimeout(r, 180));

    if (currentConversationId(projectKey) !== expectedId) {
      return { ok: true, conversation_id: expectedId, delete_clicked: true, confirmation_clicked: false };
    }

    const confirm = await findConfirmDelete();
    if (confirm) {
      confirm.click();
      return { ok: true, conversation_id: expectedId, delete_clicked: true, confirmation_clicked: true };
    }
    return { ok: true, conversation_id: expectedId, delete_clicked: true, confirmation_clicked: false };
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'gah-worker-retire-conversation') return false;
    retireWorkerConversation(message)
      .then(sendResponse)
      .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  });
})();
