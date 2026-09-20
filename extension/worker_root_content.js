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
    return value ? String(value).slice(0, 120) : null;
  }

  function projectPathMatches(projectKey) {
    if (!projectKey || location.hostname !== 'chatgpt.com') return false;
    const prefix = `/g/${projectKey}`;
    if (!location.pathname.startsWith(prefix)) return false;
    const next = location.pathname.slice(prefix.length, prefix.length + 1);
    return next === '/' || next === '-';
  }

  function rootComposerCandidates() {
    const selectors = [
      '#prompt-textarea',
      'textarea[data-testid="prompt-textarea"]',
      'textarea',
      '[role="textbox"][contenteditable="true"]',
    ];
    const found = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (seen.has(el) || !visible(el) || safeAttr(el, 'aria-disabled') === 'true') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 250 || r.height < 30) continue;
        seen.add(el);
        found.push(el);
      }
    }
    return found;
  }

  function inputDescriptors() {
    return rootComposerCandidates().slice(0, 8).map(el => {
      const rect = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        role: safeAttr(el, 'role'),
        aria_label: safeAttr(el, 'aria-label'),
        contenteditable: safeAttr(el, 'contenteditable'),
        value_length: typeof el.value === 'string' ? el.value.length : null,
        disabled: Boolean(el.disabled || safeAttr(el, 'aria-disabled') === 'true'),
        readonly: Boolean(el.readOnly || safeAttr(el, 'aria-readonly') === 'true'),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    });
  }

  function inProjectMainContent(el) {
    if (!el) return false;
    if (el.closest('nav, aside, [role="navigation"]')) return false;
    return Boolean(el.closest('main, [role="main"]'));
  }

  function projectConversationLinks(projectKey) {
    if (!projectPathMatches(projectKey) || !/\/project\/?$/.test(location.pathname)) {
      throw new Error('Project conversation listing requires the exact Project root');
    }

    const byId = new Map();
    const projectPrefix = `/g/${projectKey}`;
    const rootPattern = new RegExp(`^(/g/${projectKey}(?:-[^/]+)?)/project/?$`);
    const rootMatch = location.pathname.match(rootPattern);
    const projectRoutePrefix = rootMatch ? rootMatch[1] : null;

    for (const el of document.querySelectorAll('a[href]')) {
      if (!inProjectMainContent(el)) continue;

      let u;
      try { u = new URL(el.getAttribute('href'), location.origin); } catch (_) { continue; }
      if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') continue;

      let conversationId = null;
      if (u.pathname.startsWith(projectPrefix)) {
        const rest = u.pathname.slice(projectPrefix.length);
        const match = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
        if (match) conversationId = match[1];
      }
      if (!conversationId) {
        const generic = u.pathname.match(/^\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
        if (generic) conversationId = generic[1];
      }
      if (!conversationId) continue;

      const exactUrl = projectRoutePrefix
        ? `${location.origin}${projectRoutePrefix}/c/${conversationId}`
        : u.toString();

      byId.set(conversationId, {
        conversation_id: conversationId,
        url: exactUrl,
        title: String(el.innerText || el.textContent || '').trim().slice(0, 160) || null,
      });
    }
    return [...byId.values()];
  }

  async function listProjectConversations(message) {
    const projectKey = String(message.project_key || '');
    const started = Date.now();
    let lastSignature = null;
    let stableSince = 0;
    let latest = [];

    while (Date.now() - started < 12000) {
      latest = projectConversationLinks(projectKey);
      const signature = latest.map(item => item.conversation_id).sort().join('|');

      if (signature === lastSignature) {
        if (!stableSince) stableSince = Date.now();
        const stableFor = Date.now() - stableSince;
        const elapsed = Date.now() - started;
        if (latest.length > 0 && stableFor >= 1400) break;
        if (latest.length === 0 && elapsed >= 5000 && stableFor >= 1800) break;
      } else {
        lastSignature = signature;
        stableSince = Date.now();
      }

      await new Promise(r => setTimeout(r, 250));
    }

    return {
      project_key: projectKey,
      conversations: latest,
      count: latest.length,
      discovery_stable: true,
    };
  }


  function conversationIdFromAnchor(projectKey, el) {
    if (!el || !el.getAttribute) return null;
    let u;
    try { u = new URL(el.getAttribute('href'), location.origin); } catch (_) { return null; }
    if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') return null;

    const projectPrefix = `/g/${projectKey}`;
    if (u.pathname.startsWith(projectPrefix)) {
      const rest = u.pathname.slice(projectPrefix.length);
      const match = rest.match(/^(?:-[^/]+)?\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
      if (match) return match[1];
    }
    const generic = u.pathname.match(/^\/c\/([A-Za-z0-9-]+)(?:\/|$)/);
    return generic ? generic[1] : null;
  }

  function findProjectConversationAnchor(projectKey, conversationId) {
    const matches = [...document.querySelectorAll('a[href]')].filter(el =>
      inProjectMainContent(el) && conversationIdFromAnchor(projectKey, el) === conversationId
    );
    if (matches.length !== 1) {
      throw new Error(`Expected exactly one Project chat card for ${conversationId}, found ${matches.length}`);
    }
    return matches[0];
  }

  function signalOf(el) {
    return [
      safeAttr(el, 'data-testid'),
      safeAttr(el, 'aria-label'),
      safeAttr(el, 'title'),
      el.innerText,
      el.textContent,
    ].filter(Boolean).join(' ').trim().toLowerCase();
  }

  function dispatchHover(el) {
    for (const type of ['mouseenter', 'mouseover', 'mousemove']) {
      try {
        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      } catch (_) {}
    }
  }

  function candidateCardAncestors(anchor) {
    const out = [];
    let cur = anchor;
    for (let i = 0; cur && i < 7; i += 1, cur = cur.parentElement) {
      if (!cur || cur === document.body) break;
      if (cur.closest('nav, aside, [role="navigation"]')) break;
      const rect = cur.getBoundingClientRect();
      if (rect.width >= 300 && rect.height >= 28 && rect.height <= 180) out.push(cur);
    }
    return out;
  }

  function findCardOptionsButton(anchor) {
    const ancestors = candidateCardAncestors(anchor);
    for (const card of ancestors) {
      dispatchHover(card);
      const buttons = [...card.querySelectorAll('button, [role="button"]')].filter(el =>
        visible(el) && !el.disabled && safeAttr(el, 'aria-disabled') !== 'true'
      );
      const exact = buttons.filter(el => /conversation-options|more|options|ellipsis|更多|选项|菜单/.test(signalOf(el)));
      if (exact.length === 1) return exact[0];
      if (exact.length > 1) continue;

      const ar = anchor.getBoundingClientRect();
      const plausible = buttons.filter(el => {
        const r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const ay = ar.top + ar.height / 2;
        return r.width <= 64 && r.height <= 64 && cx >= ar.right - 80 && Math.abs(cy - ay) <= 50;
      });
      if (plausible.length === 1) return plausible[0];
    }
    return null;
  }

  async function waitForCardOptionsButton(anchor, timeoutMs = 6000) {
    const started = Date.now();
    let button = null;
    while (Date.now() - started < timeoutMs) {
      button = findCardOptionsButton(anchor);
      if (button) return button;
      dispatchHover(anchor);
      await new Promise(r => setTimeout(r, 120));
    }
    throw new Error('Could not locate Project chat card options button');
  }

  async function waitForDeleteMenuItem(timeoutMs = 5000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const items = [...document.querySelectorAll(
        '[data-testid="delete-chat-menu-item"], [role="menuitem"], button, [role="button"]'
      )].filter(el => {
        if (!visible(el) || el.disabled || safeAttr(el, 'aria-disabled') === 'true') return false;
        const signal = signalOf(el);
        return /(^|\s)(delete|删除)(\s|$)/.test(signal) || signal.includes('delete-chat-menu-item');
      });
      if (items.length === 1) return items[0];
      if (items.length > 1) {
        const menuItems = items.filter(el => el.closest('[role="menu"], [data-radix-menu-content], [data-state="open"]'));
        if (menuItems.length === 1) return menuItems[0];
      }
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error('Could not locate Project chat delete menu item');
  }

  async function waitForDeleteConfirm(timeoutMs = 5000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
      if (dialogs.length === 1) {
        const buttons = [...dialogs[0].querySelectorAll('button, [role="button"]')].filter(el => {
          if (!visible(el) || el.disabled || safeAttr(el, 'aria-disabled') === 'true') return false;
          const signal = signalOf(el);
          return /delete|删除/.test(signal) && !/cancel|取消/.test(signal);
        });
        if (buttons.length === 1) return buttons[0];
      }
      await new Promise(r => setTimeout(r, 100));
    }
    return null;
  }

  async function deleteProjectConversation(message) {
    const projectKey = String(message.project_key || '');
    const conversationId = String(message.conversation_id || '');
    if (!/^g-p-[A-Za-z0-9]+$/.test(projectKey)) throw new Error('Invalid Project key');
    if (!/^[A-Za-z0-9-]{8,128}$/.test(conversationId)) throw new Error('Invalid conversation id');
    if (!projectPathMatches(projectKey) || !/\/project\/?$/.test(location.pathname)) {
      throw new Error('Project chat deletion requires the exact Project root');
    }

    const anchor = findProjectConversationAnchor(projectKey, conversationId);
    anchor.scrollIntoView({ block: 'center', inline: 'nearest' });
    dispatchHover(anchor);
    await new Promise(r => setTimeout(r, 180));

    const options = await waitForCardOptionsButton(anchor);
    options.click();
    await new Promise(r => setTimeout(r, 180));

    const deleteItem = await waitForDeleteMenuItem();
    deleteItem.click();
    await new Promise(r => setTimeout(r, 180));

    const confirm = await waitForDeleteConfirm();
    if (confirm) confirm.click();

    const started = Date.now();
    while (Date.now() - started < 10000) {
      const stillThere = [...document.querySelectorAll('a[href]')].some(el =>
        inProjectMainContent(el) && conversationIdFromAnchor(projectKey, el) === conversationId
      );
      if (!stillThere) {
        return {
          ok: true,
          deleted: true,
          conversation_id: conversationId,
          confirmation_clicked: Boolean(confirm),
        };
      }
      await new Promise(r => setTimeout(r, 200));
    }
    throw new Error('Project chat card remained visible after delete action');
  }

  async function probeWorkerRoot(message) {
    const projectKey = String(message.project_key || '');
    if (!projectPathMatches(projectKey) || !/\/project\/?$/.test(location.pathname)
        || /\/c\/[A-Za-z0-9-]+/.test(location.pathname)) {
      throw new Error('Worker root probe requires the exact bound Project root');
    }
    const inputs = inputDescriptors();
    return {
      project_key: projectKey,
      composer_count: rootComposerCandidates().length,
      input_candidate_count: inputs.length,
      input_candidates: inputs,
    };
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message) return false;
    if (message.type === 'gah-worker-root-probe') {
      probeWorkerRoot(message)
        .then(probe => sendResponse({ ok: true, probe }))
        .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
      return true;
    }
    if (message.type === 'gah-project-list-conversations') {
      listProjectConversations(message)
        .then(result => sendResponse({ ok: true, ...result }))
        .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
      return true;
    }
    if (message.type === 'gah-project-delete-conversation') {
      deleteProjectConversation(message)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
      return true;
    }
    return false;
  });
})();
