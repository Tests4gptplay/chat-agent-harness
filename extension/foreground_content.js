'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const WORKER_PROJECT_KEY = 'g-p-examplelane00';

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }

  function isWorkerProjectPage() {
    return location.protocol === 'https:'
      && location.hostname === 'chatgpt.com'
      && location.pathname.startsWith(`/g/${WORKER_PROJECT_KEY}`);
  }

  function textOf(el) {
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return el.value || '';
    return el.innerText || el.textContent || '';
  }

  function findComposer() {
    const selectors = [
      '#prompt-textarea',
      'textarea[data-testid="prompt-textarea"]',
      'div[contenteditable="true"][data-lexical-editor="true"]',
      '[contenteditable="true"][role="textbox"]',
    ];
    const found = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (!seen.has(el) && visible(el)) { seen.add(el); found.push(el); }
      }
    }
    if (found.length !== 1) throw new Error(`Expected exactly one visible foreground composer, found ${found.length}`);
    return found[0];
  }

  function setComposerText(el, value) {
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, value);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
      return;
    }
    el.textContent = '';
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    let inserted = false;
    try { inserted = document.execCommand('insertText', false, value); } catch (_) {}
    if (!inserted) {
      el.textContent = value;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    }
  }

  function findSendButton() {
    const selectors = [
      'button[data-testid="send-button"]',
      'button[data-testid="fruitjuice-send-button"]',
    ];
    const found = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (!seen.has(el) && visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true') {
          seen.add(el);
          found.push(el);
        }
      }
    }
    if (found.length !== 1) throw new Error(`Expected exactly one visible enabled foreground send button, found ${found.length}`);
    return found[0];
  }

  function validSignal(text) {
    const first = String(text || '').split('\n', 1)[0];
    return /^GAH_FOREGROUND v=1 task=[A-Za-z0-9._:-]{3,128} event=(started|heartbeat|terminal) status=(RUNNING|SUCCESS|FAILURE|BLOCKED|CANCELLED)$/.test(first);
  }

  async function accepted(signalLine, timeoutMs) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      try {
        const composer = findComposer();
        if (!textOf(composer).includes(signalLine)) return true;
      } catch (_) {
        return true;
      }
      await new Promise(r => setTimeout(r, 100));
    }
    try {
      return !textOf(findComposer()).includes(signalLine);
    } catch (_) {
      return true;
    }
  }

  async function submitForegroundMonitor(message) {
    if (location.protocol !== 'https:' || location.hostname !== 'chatgpt.com') {
      throw new Error('Foreground monitor submission is only allowed on chatgpt.com');
    }
    if (isWorkerProjectPage()) throw new Error('Foreground monitor refuses the fixed Worker Project');
    const text = String(message.text || '');
    if (!validSignal(text)) throw new Error('Invalid foreground monitor signal');
    const signalLine = text.split('\n', 1)[0];

    let composer = findComposer();
    if (textOf(composer).trim()) return { ok: false, error: 'foreground_composer_busy' };
    setComposerText(composer, text);
    await new Promise(r => setTimeout(r, 150));
    if (!textOf(composer).includes(signalLine)) {
      throw new Error('Foreground composer did not accept monitor signal');
    }

    let button;
    try {
      button = findSendButton();
      button.click();
    } catch (err) {
      try { setComposerText(composer, ''); } catch (_) {}
      throw err;
    }

    let ok = await accepted(signalLine, 2500);
    if (!ok) {
      composer = findComposer();
      if (!textOf(composer).includes(signalLine)) {
        ok = true;
      } else {
        button = findSendButton();
        button.click();
        ok = await accepted(signalLine, 5000);
      }
    }
    if (!ok) {
      try { setComposerText(findComposer(), ''); } catch (_) {}
      throw new Error('Foreground send click was not accepted by ChatGPT');
    }
    return { ok: true };
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'gah-submit-foreground-monitor') return false;
    submitForegroundMonitor(message)
      .then(sendResponse)
      .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  });
})();
