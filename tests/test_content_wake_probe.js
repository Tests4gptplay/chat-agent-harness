'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CONTENT_SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'extension', 'content.js'),
  'utf8'
);

function message(role, text) {
  return {
    innerText: text,
    textContent: text,
    getAttribute(name) {
      return name === 'data-message-author-role' ? role : null;
    },
  };
}

function loadContent(messages, running = false) {
  let listener = null;
  const stopButton = {
    getBoundingClientRect() {
      return { width: 10, height: 10 };
    },
  };
  const document = {
    documentElement: {},
    querySelectorAll(selector) {
      if (selector === '[data-message-author-role]') return messages;
      if (selector === '[data-message-author-role="user"]') {
        return messages.filter(item => item.getAttribute('data-message-author-role') === 'user');
      }
      if (selector === '[data-message-author-role="assistant"]') {
        return messages.filter(item => item.getAttribute('data-message-author-role') === 'assistant');
      }
      if (
        selector === 'button[data-testid="stop-button"]'
        || selector === 'button[aria-label*="Stop"]'
        || selector === 'button[aria-label*="停止"]'
      ) {
        return running ? [stopButton] : [];
      }
      return [];
    },
  };
  const runtime = {
    onMessage: {
      addListener(fn) {
        listener = fn;
      },
    },
    async sendMessage() {
      return { ok: true };
    },
  };
  const sandbox = {
    browser: { runtime },
    document,
    location: { protocol: 'https:', hostname: 'chatgpt.com', href: 'https://chatgpt.com/' },
    MutationObserver: class {
      observe() {}
    },
    HTMLTextAreaElement: class {},
    HTMLInputElement: class {},
    InputEvent: class {},
    getComputedStyle() {
      return { visibility: 'visible', display: 'block' };
    },
    window: {
      getSelection() {
        return {
          removeAllRanges() {},
          addRange() {},
        };
      },
    },
    setTimeout() { return 0; },
    clearTimeout() {},
    setInterval() { return 0; },
    clearInterval() {},
    console,
    URL,
    Date,
    Promise,
    JSON,
    String,
    Number,
    Boolean,
    Array,
    Set,
    Map,
    Error,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(CONTENT_SOURCE, sandbox, { filename: 'extension/content.js' });
  assert.equal(typeof listener, 'function');
  return listener;
}

function probe(listener, marker) {
  let response = null;
  const handled = listener(
    { type: 'gah-probe-wake-marker', marker },
    {},
    value => { response = value; }
  );
  assert.equal(handled, false);
  assert.ok(response);
  return response;
}

const MARKER = 'GAH_WAKE v=1 id=wake-review-probe-001 project=git-agent-harness';
const DISPATCH = 'GAH_DISPATCH task_id=task-review-001 backend_cl=cl/task-review-001.backend.json dispatch_id=dispatch-review-001 generation=1 fence_token=fence-review-probe-001';

test('old wake marker is not credited with a later user turn global running signal', () => {
  const listener = loadContent([
    message('user', MARKER + '\n' + DISPATCH),
    message('user', 'unrelated later user turn'),
  ], true);

  const result = probe(listener, MARKER);
  assert.equal(result.ok, true);
  assert.equal(result.marker_visible, true);
  assert.equal(result.response_started, false);
  assert.equal(result.dispatch_context.dispatch_id, 'dispatch-review-001');
});

test('assistant response before the next user boundary proves the wake started', () => {
  const listener = loadContent([
    message('user', MARKER + '\n' + DISPATCH),
    message('assistant', 'response to exact wake'),
    message('user', 'later unrelated user turn'),
  ], true);

  const result = probe(listener, MARKER);
  assert.equal(result.ok, true);
  assert.equal(result.marker_visible, true);
  assert.equal(result.response_started, true);
  assert.equal(result.dispatch_context.fence_token, 'fence-review-probe-001');
});

test('latest wake may use the current running signal when no later user supersedes it', () => {
  const listener = loadContent([
    message('user', MARKER + '\n' + DISPATCH),
  ], true);

  const result = probe(listener, MARKER);
  assert.equal(result.ok, true);
  assert.equal(result.marker_visible, true);
  assert.equal(result.response_started, true);
});
