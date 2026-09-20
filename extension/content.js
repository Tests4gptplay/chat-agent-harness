'use strict';

const ext = globalThis.browser || globalThis.chrome;
const historyRateLimitApi = globalThis.CAHHistoryRateLimit || null;

function visible(el) {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
}

function textOf(el) {
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return el.value || '';
  return el.innerText || el.textContent || '';
}

function historyRateLimitDialogCandidates() {
  const selectors = [
    '[role="dialog"]',
    '[aria-modal="true"]',
  ];
  const found = [];
  const seen = new Set();
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      if (!seen.has(el) && visible(el)) {
        seen.add(el);
        found.push(el);
      }
    }
  }
  return found;
}

function findHistoryAccessRateLimitModal() {
  if (!historyRateLimitApi) return null;
  const matches = [];
  for (const dialog of historyRateLimitDialogCandidates()) {
    const dialogText = textOf(dialog);
    if (!historyRateLimitApi.isHistoryAccessRateLimitText(dialogText)) continue;
    const buttons = [...dialog.querySelectorAll('button')].filter(button =>
      visible(button) && !button.disabled && historyRateLimitApi.isDismissLabel(textOf(button))
    );
    matches.push({ dialog, dialogText, buttons });
  }
  if (!matches.length) return null;
  if (matches.length !== 1) {
    return { ambiguous: true, reason: `expected one history rate-limit dialog, found ${matches.length}` };
  }
  if (matches[0].buttons.length !== 1) {
    return {
      ambiguous: true,
      reason: `expected one affirmative history rate-limit button, found ${matches[0].buttons.length}`,
    };
  }
  return {
    ambiguous: false,
    dialog: matches[0].dialog,
    button: matches[0].buttons[0],
    dialogText: historyRateLimitApi.normalizeText(matches[0].dialogText),
  };
}

async function waitForModalGone(dialog, timeoutMs = 3000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!dialog.isConnected || !visible(dialog)) return true;
    await new Promise(r => setTimeout(r, 100));
  }
  return !dialog.isConnected || !visible(dialog);
}

async function dismissHistoryAccessRateLimitModalIfPresent() {
  if (!historyRateLimitApi) return { handled: false, attempts: 0 };
  let handled = false;
  let attempts = 0;
  while (attempts < historyRateLimitApi.MAX_DISMISS_ATTEMPTS) {
    const match = findHistoryAccessRateLimitModal();
    if (!match) return { handled, attempts };
    if (match.ambiguous) {
      await reportLocal('ui.history_rate_limit_ambiguous', 'warn', {
        reason: match.reason,
      });
      throw new Error(`History-access rate-limit modal is ambiguous: ${match.reason}`);
    }
    attempts += 1;
    match.button.click();
    const gone = await waitForModalGone(match.dialog);
    if (!gone) {
      await reportLocal('ui.history_rate_limit_dismiss_failed', 'warn', { attempt: attempts });
      if (attempts >= historyRateLimitApi.MAX_DISMISS_ATTEMPTS) {
        throw new Error('History-access rate-limit modal did not dismiss');
      }
      continue;
    }
    handled = true;
    await reportLocal('ui.history_rate_limit_dismissed', 'info', {
      attempt: attempts,
      text_prefix: match.dialogText.slice(0, 240),
    });
    await new Promise(r => setTimeout(r, 150));
  }
  const remaining = findHistoryAccessRateLimitModal();
  if (remaining) throw new Error('History-access rate-limit modal repeated beyond retry bound');
  return { handled, attempts };
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
      if (!seen.has(el) && visible(el)) {
        seen.add(el);
        found.push(el);
      }
    }
  }
  if (found.length !== 1) throw new Error(`Expected exactly one visible ChatGPT composer, found ${found.length}`);
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

function assistantMessageCount() {
  return document.querySelectorAll('[data-message-author-role="assistant"]').length;
}

function responseRunningSignal() {
  const selectors = [
    'button[data-testid="stop-button"]',
    'button[aria-label*="Stop"]',
    'button[aria-label*="停止"]',
  ];
  return selectors.some(selector => [...document.querySelectorAll(selector)].some(visible));
}

async function waitForAssistantStart(beforeCount, timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (assistantMessageCount() > beforeCount || responseRunningSignal()) return true;
    await new Promise(r => setTimeout(r, 120));
  }
  return assistantMessageCount() > beforeCount || responseRunningSignal();
}

function wakeMarkerFromText(text) {
  const line = String(text || '').split(/\r?\n/).find(item =>
    /^GAH_WAKE v=1 id=[A-Za-z0-9._-]{8,128} project=\S+$/.test(String(item || '').trim())
  );
  return line ? line.trim() : null;
}

function latestWakeMarker() {
  const messages = [...document.querySelectorAll('[data-message-author-role="user"]')].slice(-12).reverse();
  for (const el of messages) {
    const found = wakeMarkerFromText(String(el.innerText || el.textContent || ''));
    if (found) return found;
  }
  return null;
}

function probeWakeMarker(marker) {
  const exact = String(marker || '').trim();
  if (!/^GAH_WAKE v=1 id=[A-Za-z0-9._-]{8,128} project=\S+$/.test(exact)) {
    throw new Error('Invalid wake marker probe');
  }
  const messages = [...document.querySelectorAll('[data-message-author-role]')];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const el = messages[i];
    if (el.getAttribute('data-message-author-role') !== 'user') continue;
    const text = String(el.innerText || el.textContent || '');
    const lines = text.split(/\r?\n/).map(x => x.trim());
    if (!lines.includes(exact)) continue;
    let supersededByUser = false;
    for (let j = i + 1; j < messages.length; j += 1) {
      const role = messages[j].getAttribute('data-message-author-role');
      if (role === 'user') {
        supersededByUser = true;
        break;
      }
      if (role === 'assistant') {
        return {
          ok: true,
          marker_visible: true,
          response_started: true,
          dispatch_context: parseDispatchContext(text),
        };
      }
    }
    return {
      ok: true,
      marker_visible: true,
      response_started: supersededByUser ? false : responseRunningSignal(),
      dispatch_context: parseDispatchContext(text),
    };
  }
  return { ok: true, marker_visible: false, response_started: false, dispatch_context: null };
}

function definiteWakeError(message) {
  const error = new Error(message);
  error.definitePreSend = true;
  return error;
}

function unknownWakeError(message) {
  const error = new Error(message);
  error.outcomeUnknown = true;
  return error;
}

const SYSCALL_RESCAN_MS = 2000;
const RESPONSE_MONITOR_MS = 500;
const RESPONSE_END_SETTLE_MS = 1200;
let activeDispatchContext = null;
let lastEndedDispatchKey = null;

function parseDispatchContext(text) {
  const m = String(text || '').match(
    /GAH_DISPATCH task_id=(\S+) backend_cl=(\S+) dispatch_id=(\S+) generation=(\d+) fence_token=(\S+)/
  );
  if (!m) return null;
  return {
    task_id: m[1],
    backend_cl: m[2],
    dispatch_id: m[3],
    dispatch_generation: Number(m[4]),
    fence_token: m[5],
  };
}

function latestDispatchContext() {
  const messages = [...document.querySelectorAll('[data-message-author-role="user"]')].slice(-8).reverse();
  for (const el of messages) {
    const ctx = parseDispatchContext(String(el.innerText || el.textContent || ''));
    if (ctx) return ctx;
  }
  return activeDispatchContext;
}

function dispatchContextKey(ctx) {
  if (!ctx) return '';
  return [ctx.backend_cl, ctx.dispatch_id, ctx.dispatch_generation, ctx.fence_token].join('|');
}

async function reportLocal(event, level = 'info', data = {}) {
  try {
    await ext.runtime.sendMessage({ type: 'gah-local-event', event, level, data });
  } catch (_) {}
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
      if (!seen.has(el) && visible(el) && !el.disabled) {
        seen.add(el);
        found.push(el);
      }
    }
  }
  if (found.length !== 1) throw new Error(`Expected exactly one visible enabled send button, found ${found.length}`);
  return found[0];
}


function base64Bytes(value) {
  const raw = atob(String(value || ''));
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
}

function attachmentScope(composer) {
  return composer.closest('form') || composer.parentElement || document.body;
}

function attachmentSignals(scope, fileName) {
  const selectors = [
    '[data-testid*="attachment"]',
    '[data-testid*="file"]',
    '[data-testid*="upload"]',
    'img[src^="blob:"]',
    'img[src*="/files/"]',
    'button[aria-label*="Remove"]',
    'button[aria-label*="移除"]',
    'button[aria-label*="Delete"]',
    'button[aria-label*="删除"]',
  ];
  const nodes = [];
  const seen = new Set();
  for (const selector of selectors) {
    for (const el of scope.querySelectorAll(selector)) {
      if (!seen.has(el)) {
        seen.add(el);
        nodes.push(el);
      }
    }
  }
  const text = String(scope.innerText || scope.textContent || '');
  return {
    count: nodes.length,
    fileNameVisible: Boolean(fileName && text.includes(fileName)),
  };
}

async function waitForAttachmentSignal(composer, fileName, before, timeoutMs = 8000) {
  const scope = attachmentScope(composer);
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const current = attachmentSignals(scope, fileName);
    if (current.fileNameVisible || current.count > before.count) return current;
    await new Promise(r => setTimeout(r, 120));
  }
  const current = attachmentSignals(scope, fileName);
  return (current.fileNameVisible || current.count > before.count) ? current : null;
}

function chooseFileInput(composer) {
  const form = composer.closest('form');
  const local = form ? [...form.querySelectorAll('input[type="file"]')] : [];
  if (local.length) return local[local.length - 1];
  const all = [...document.querySelectorAll('input[type="file"]')];
  const imageFriendly = all.filter(el => {
    const accept = String(el.getAttribute('accept') || '').toLowerCase();
    return !accept || accept.includes('image') || accept.includes('*/*');
  });
  if (imageFriendly.length) return imageFriendly[imageFriendly.length - 1];
  return all.length ? all[all.length - 1] : null;
}

async function attachWakeImage(composer, attachment) {
  if (!attachment || typeof attachment !== 'object') return null;
  const ref = String(attachment.ref || '');
  const fileName = String(attachment.file_name || 'gah-image');
  const mimeType = String(attachment.mime_type || '');
  const expectedSha = String(attachment.sha256 || '').toLowerCase();
  const sizeBytes = Number(attachment.size_bytes || 0);
  const b64 = String(attachment.base64 || '');
  if (!ref || !fileName || !/^image\/(png|jpeg|webp)$/.test(mimeType)) {
    throw new Error('Invalid CAH visual attachment metadata');
  }
  if (!expectedSha.match(/^[a-f0-9]{64}$/)) throw new Error('Invalid CAH visual attachment SHA-256');
  if (sizeBytes < 1 || sizeBytes > 2 * 1024 * 1024) throw new Error('CAH visual attachment size unsupported');

  const bytes = base64Bytes(b64);
  if (bytes.length !== sizeBytes) throw new Error('CAH visual attachment size mismatch');
  const actualSha = await sha256Hex(bytes);
  if (actualSha !== expectedSha) throw new Error('CAH visual attachment SHA-256 mismatch');

  const scope = attachmentScope(composer);
  const before = attachmentSignals(scope, fileName);
  const file = new File([bytes], fileName, { type: mimeType, lastModified: Date.now() });
  let method = null;

  const input = chooseFileInput(composer);
  if (input) {
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'files');
      if (descriptor && descriptor.set) descriptor.set.call(input, dt.files);
      else input.files = dt.files;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      method = 'file-input';
    } catch (_) {
      method = null;
    }
  }

  let signal = method ? await waitForAttachmentSignal(composer, fileName, before, 5000) : null;
  if (!signal) {
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      const event = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'clipboardData', { value: dt });
      composer.dispatchEvent(event);
      method = 'synthetic-paste';
      signal = await waitForAttachmentSignal(composer, fileName, before, 5000);
    } catch (_) {
      signal = null;
    }
  }

  if (!signal) throw new Error('CAH visual attachment was not confirmed by ChatGPT UI');
  return { method, ref, sha256: actualSha, file_name: fileName, signal_count: signal.count };
}

async function submitWake(message) {
  if (location.protocol !== 'https:' || location.hostname !== 'chatgpt.com') {
    throw definiteWakeError('Wake submission is only allowed on chatgpt.com');
  }
  const marker = String(message.marker || '');
  if (!/^GAH_WAKE v=1 id=[A-Za-z0-9._-]{8,128} project=\S+$/.test(marker)) {
    throw definiteWakeError('Invalid wake marker');
  }
  const text = String(message.text || marker);
  if (!text.startsWith(marker) || text.length > 4096) {
    throw definiteWakeError('Invalid wake text');
  }

  await dismissHistoryAccessRateLimitModalIfPresent();

  const beforeAssistantCount = assistantMessageCount();
  let composer = findComposer();
  if (textOf(composer).trim()) throw definiteWakeError('Composer is not empty; refusing to overwrite user text');
  setComposerText(composer, text);
  await new Promise(r => setTimeout(r, 150));

  let attachmentResult = null;
  try {
    if (message.attachment) {
      attachmentResult = await attachWakeImage(composer, message.attachment);
      await new Promise(r => setTimeout(r, 200));
    }

    const preSendRecovery = await dismissHistoryAccessRateLimitModalIfPresent();
    if (preSendRecovery.handled) {
      composer = findComposer();
      const current = textOf(composer).trim();
      if (!current) {
        setComposerText(composer, text);
        await new Promise(r => setTimeout(r, 150));
      } else if (current !== text.trim()) {
        throw new Error('Composer changed while dismissing history-access rate-limit modal');
      }
    }

    findSendButton().click();
  } catch (err) {
    try {
      const currentComposer = findComposer();
      if (textOf(currentComposer).trim() === text.trim()) setComposerText(currentComposer, '');
    } catch (_) {}
    if (err && typeof err === 'object') err.definitePreSend = true;
    throw err;
  }
  const responseStarted = await waitForAssistantStart(beforeAssistantCount, 15000);
  if (!responseStarted) {
    throw unknownWakeError('Wake submitted but assistant response did not start within timeout');
  }
  activeDispatchContext = parseDispatchContext(text) || activeDispatchContext;
  if (activeDispatchContext) lastEndedDispatchKey = null;
  // Treat a successfully admitted wake as an active semantic turn even when
  // ChatGPT renders a very fast response without a stable visible stop button.
  responseWasRunning = true;
  return {
    ok: true,
    response_started: true,
    attachment_method: attachmentResult ? attachmentResult.method : null,
    attachment_ref: attachmentResult ? attachmentResult.ref : null,
    attachment_sha256: attachmentResult ? attachmentResult.sha256 : null,
  };
}


const seenSyscalls = new Set();
const seenSyscallKeys = new Set();
const inFlightSyscallKeys = new Set();
const syscallFailures = new Map();
let syscallScanTimer = null;
let syscallScanRunning = false;

function syscallBlocks(text) {
  const out = [];
  const re = /GAH_SYSCALL_BEGIN\s*([\s\S]*?)\s*GAH_SYSCALL_END/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const raw = String(m[1] || '').trim();
    if (raw && raw.length <= 32768) out.push(raw);
  }
  return out;
}

function syscallIdentity(syscall) {
  return {
    task_id: syscall && syscall.task_id ? String(syscall.task_id) : null,
    dispatch_id: syscall && syscall.dispatch_id ? String(syscall.dispatch_id) : null,
    dispatch_generation: syscall ? Number(syscall.dispatch_generation || 0) : 0,
    action_id: syscall && syscall.action && syscall.action.action_id ? String(syscall.action.action_id) : null,
  };
}

function syscallKey(syscall) {
  const x = syscallIdentity(syscall);
  if (!x.task_id || !x.dispatch_id || !x.dispatch_generation || !x.action_id) return '';
  return [x.task_id, x.dispatch_id, x.dispatch_generation, x.action_id].join('|');
}

function permanentSyscallError(error) {
  return /ACTION_SUBMIT_(STALE_DISPATCH|WORKER_MISMATCH|DISPATCH_CHANGED|BACKEND_CL_INVALID|DISPATCH_MISSING|DISPATCH_STATE_(WAIT_RESULT|WAIT_RESOURCE|WAIT_DEP|HANDOFF|DONE|ERROR|BLOCKED|CANCELLED))/i.test(String(error || ''));
}

function syscallRetryDelay(attempt) {
  const n = Math.max(1, Number(attempt || 1));
  return Math.min(60000, 1000 * (2 ** Math.min(n - 1, 6)));
}

function recordSyscallFailure(key) {
  const count = Number(syscallFailures.get(key) || 0) + 1;
  syscallFailures.set(key, count);
  return count;
}

async function submitObservedSyscalls() {
  if (responseRunningSignal()) {
    scheduleSyscallScan(500);
    return { deferred: true };
  }
  if (syscallScanRunning) return { deferred: true, reason: 'scan_in_flight' };
  syscallScanRunning = true;
  try {
    const messages = [...document.querySelectorAll('[data-message-author-role="assistant"]')].slice(-6);
    for (const el of messages) {
      const text = String(el.innerText || el.textContent || '');
      for (const raw of syscallBlocks(text)) {
        if (seenSyscalls.has(raw)) continue;
        let syscall;
        try {
          syscall = JSON.parse(raw);
        } catch (err) {
          seenSyscalls.add(raw);
          await reportLocal('worker.syscall_parse_error', 'error', {
            message: String(err && err.message || err).slice(0, 500),
            raw_prefix: raw.slice(0, 500),
          });
          continue;
        }
        if (!syscall || syscall.v !== 1 || syscall.kind !== 'action_submit') {
          seenSyscalls.add(raw);
          await reportLocal('worker.syscall_unsupported', 'warn', syscallIdentity(syscall));
          continue;
        }

        const key = syscallKey(syscall);
        if (!key) {
          seenSyscalls.add(raw);
          await reportLocal('worker.syscall_identity_invalid', 'error', syscallIdentity(syscall));
          continue;
        }
        if (seenSyscallKeys.has(key) || inFlightSyscallKeys.has(key)) {
          seenSyscalls.add(raw);
          continue;
        }

        inFlightSyscallKeys.add(key);
        try {
          const response = await ext.runtime.sendMessage({
            type: 'gah-worker-syscall',
            syscall,
          });
          if (response && response.ok) {
            seenSyscalls.add(raw);
            seenSyscallKeys.add(key);
            syscallFailures.delete(key);
            continue;
          }

          const error = String(response && response.error || 'Worker syscall rejected');
          const permanent = permanentSyscallError(error);
          const attempt = permanent ? 1 : recordSyscallFailure(key);
          const abandoned = permanent;
          if (abandoned) {
            seenSyscalls.add(raw);
            seenSyscallKeys.add(key);
            syscallFailures.delete(key);
          }
          const retry_in_ms = abandoned ? null : syscallRetryDelay(attempt);
          await reportLocal('worker.syscall_submit_rejected', abandoned ? 'error' : 'warn', {
            ...syscallIdentity(syscall),
            attempt,
            error: error.slice(0, 800),
            abandoned,
            permanent,
            retry_in_ms,
          });
          if (!abandoned) scheduleSyscallScan(retry_in_ms);
        } catch (err) {
          const attempt = recordSyscallFailure(key);
          const retry_in_ms = syscallRetryDelay(attempt);
          await reportLocal('worker.syscall_submit_error', 'warn', {
            ...syscallIdentity(syscall),
            attempt,
            error: String(err && err.message || err).slice(0, 800),
            abandoned: false,
            retry_in_ms,
          });
          scheduleSyscallScan(retry_in_ms);
        } finally {
          inFlightSyscallKeys.delete(key);
        }
      }
    }
    return { deferred: false };
  } finally {
    syscallScanRunning = false;
  }
}

function scheduleSyscallScan(delayMs = 700) {
  if (syscallScanTimer) clearTimeout(syscallScanTimer);
  syscallScanTimer = setTimeout(() => {
    syscallScanTimer = null;
    submitObservedSyscalls().catch(err => {
      reportLocal('worker.syscall_scan_error', 'warn', {
        error: String(err && err.message || err).slice(0, 800),
      });
    });
  }, delayMs);
}

async function notifySemanticResponseEnded() {
  if (responseRunningSignal()) return;
  const ctx = latestDispatchContext();
  if (!ctx) return;
  const key = dispatchContextKey(ctx);
  if (!key || key === lastEndedDispatchKey) return;

  // Give the syscall path first right of refusal. A successful action_submit
  // transitions the canonical dispatch to WAIT_RESULT before liveness recovery.
  await submitObservedSyscalls();
  await new Promise(r => setTimeout(r, 250));

  try {
    const response = await ext.runtime.sendMessage({
      type: 'gah-worker-response-ended',
      dispatch_context: ctx,
      observed_at: new Date().toISOString(),
    });
    if (response && response.ok) {
      lastEndedDispatchKey = key;
      if (response.recovered && response.dispatch) {
        activeDispatchContext = {
          task_id: ctx.task_id,
          backend_cl: ctx.backend_cl,
          dispatch_id: String(response.dispatch.dispatch_id || ''),
          dispatch_generation: Number(response.dispatch.generation || 0),
          fence_token: String(response.dispatch.fence_token || ''),
        };
      }
    } else {
      await reportLocal('worker.response_end_reconcile_rejected', 'warn', {
        ...ctx,
        error: String(response && response.error || 'response-end reconciliation rejected').slice(0, 800),
      });
    }
  } catch (err) {
    await reportLocal('worker.response_end_reconcile_error', 'warn', {
      ...ctx,
      error: String(err && err.message || err).slice(0, 800),
    });
  }
}

const syscallObserver = new MutationObserver(() => scheduleSyscallScan());
if (document.documentElement) {
  syscallObserver.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
}
setTimeout(() => scheduleSyscallScan(0), 1200);
setInterval(() => {
  if (!responseRunningSignal()) submitObservedSyscalls().catch(() => null);
}, SYSCALL_RESCAN_MS);

let responseWasRunning = responseRunningSignal();
setInterval(() => {
  const running = responseRunningSignal();
  if (responseWasRunning && !running) {
    scheduleSyscallScan(0);
    setTimeout(() => notifySemanticResponseEnded().catch(() => null), RESPONSE_END_SETTLE_MS);
  }
  responseWasRunning = running;
}, RESPONSE_MONITOR_MS);

// Recovery after tab/extension reload: infer the latest dispatch from the DOM and
// reconcile an already-ended semantic turn even if the transition event was missed.
setTimeout(() => {
  if (!responseRunningSignal()) {
    scheduleSyscallScan(0);
    setTimeout(() => notifySemanticResponseEnded().catch(() => null), RESPONSE_END_SETTLE_MS);
  }
}, 2500);

ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message) return false;
  if (message.type === 'gah-content-ping') {
    sendResponse({
      ok: true,
      href: location.href,
      assistant_count: assistantMessageCount(),
      response_running: responseRunningSignal(),
      dispatch_context: latestDispatchContext(),
      wake_marker: latestWakeMarker(),
    });
    return false;
  }
  if (message.type === 'gah-probe-wake-marker') {
    try { sendResponse(probeWakeMarker(message.marker)); }
    catch (err) { sendResponse({ ok: false, error: String(err && err.message || err) }); }
    return false;
  }
  if (message.type === 'cah-ui-recovery-preflight') {
    dismissHistoryAccessRateLimitModalIfPresent()
      .then(result => {
        let composerReady = null;
        let composerError = null;
        if (message.require_composer) {
          try {
            findComposer();
            composerReady = true;
          } catch (err) {
            composerReady = false;
            composerError = String(err && err.message || err);
          }
        }
        sendResponse({
          ok: true,
          handled: Boolean(result && result.handled),
          attempts: Number(result && result.attempts || 0),
          composer_ready: composerReady,
          composer_error: composerError,
          context: String(message.context || '').slice(0, 120),
        });
      })
      .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  }
  if (message.type !== 'gah-submit-wake') return false;
  submitWake(message)
    .then(sendResponse)
    .catch(err => sendResponse({
      ok: false,
      error: String(err && err.message || err),
      definite_pre_send: Boolean(err && err.definitePreSend),
      outcome_unknown: Boolean(err && (err.outcomeUnknown || !err.definitePreSend)),
    }));
  return true;
});
