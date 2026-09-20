'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const WORKER_CANONICAL_REPO = 'example-owner/cah-private';
  const WORKER_STATE_PATH = 'state/chatgpt.json';

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

  function isWorkerRoot(projectKey) {
    if (!projectKey || location.hostname !== 'chatgpt.com') return false;
    const prefix = `/g/${projectKey}`;
    if (!location.pathname.startsWith(prefix)) return false;
    const next = location.pathname.slice(prefix.length, prefix.length + 1);
    return (next === '/' || next === '-')
      && /\/project\/?$/.test(location.pathname)
      && !/\/c\/[A-Za-z0-9-]+/.test(location.pathname);
  }

  function candidates() {
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
        const rect = el.getBoundingClientRect();
        if (rect.width < 250 || rect.height < 30) continue;
        seen.add(el);
        found.push(el);
      }
    }
    return found;
  }

  function findWorkerComposer() {
    const all = candidates();
    const exact = all.filter(el => safeAttr(el, 'aria-label') === '此项目中的新聊天');
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) throw new Error(`Expected one exact Worker Project textbox, found ${exact.length}`);
    if (all.length === 1) return all[0];
    throw new Error(`Expected one Worker Project textbox, found ${all.length}`);
  }

  function editableText(el) {
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return el.value || '';
    return el.innerText || el.textContent || '';
  }

  function setEditableText(el, value) {
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, value);
      el.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: value ? 'insertText' : 'deleteContentBackward',
        data: value || null,
      }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
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
    if (value) {
      try { inserted = document.execCommand('insertText', false, value); } catch (_) {}
    }
    if (value && !inserted) {
      el.textContent = value;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    } else if (!value) {
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward', data: null }));
    }
  }


  async function waitForStableComposer(projectKey, timeoutMs = 10000, stableMs = 1200) {
    const started = Date.now();
    let stableSince = 0;
    let lastEl = null;
    let lastText = null;
    while (Date.now() - started < timeoutMs) {
      if (!isWorkerRoot(projectKey)) throw new Error('Worker Project root disappeared while waiting for composer stability');
      let composer;
      try {
        composer = findWorkerComposer();
      } catch (_) {
        stableSince = 0;
        lastEl = null;
        lastText = null;
        await new Promise(r => setTimeout(r, 150));
        continue;
      }
      const text = editableText(composer);
      if (composer === lastEl && text === lastText) {
        if (!stableSince) stableSince = Date.now();
        if (Date.now() - stableSince >= stableMs) return composer;
      } else {
        lastEl = composer;
        lastText = text;
        stableSince = Date.now();
      }
      await new Promise(r => setTimeout(r, 150));
    }
    throw new Error('Timed out waiting for Worker Project composer hydration to stabilize');
  }

  async function clearComposerStable(projectKey, timeoutMs = 8000) {
    const started = Date.now();
    let attempts = 0;
    while (Date.now() - started < timeoutMs && attempts < 8) {
      attempts += 1;
      const composer = await waitForStableComposer(projectKey, Math.min(3000, timeoutMs), 500);
      if (editableText(composer).trim()) setEditableText(composer, '');
      const holdStarted = Date.now();
      let stayedEmpty = true;
      while (Date.now() - holdStarted < 800) {
        await new Promise(r => setTimeout(r, 100));
        const current = findWorkerComposer();
        if (editableText(current).trim()) {
          stayedEmpty = false;
          break;
        }
      }
      if (stayedEmpty) return { composer: findWorkerComposer(), attempts };
    }
    throw new Error('Worker Project draft kept returning after clear; hydration did not settle');
  }

  async function writeBootstrapStable(projectKey, bootstrap, marker, timeoutMs = 10000) {
    const started = Date.now();
    let attempts = 0;
    while (Date.now() - started < timeoutMs && attempts < 6) {
      attempts += 1;
      const cleared = await clearComposerStable(projectKey, Math.min(5000, timeoutMs));
      let composer = cleared.composer;
      setEditableText(composer, bootstrap);

      let persisted = true;
      const holdStarted = Date.now();
      while (Date.now() - holdStarted < 1200) {
        await new Promise(r => setTimeout(r, 120));
        composer = findWorkerComposer();
        if (!editableText(composer).includes(marker)) {
          persisted = false;
          break;
        }
      }
      if (persisted) return { composer, attempts, clearAttempts: cleared.attempts };
    }
    throw new Error('Worker Project bootstrap was overwritten during hydration');
  }

  function rawSendButtonCandidates() {
    const exactSelectors = [
      'button[data-testid="send-button"]',
      'button[data-testid="fruitjuice-send-button"]',
    ];
    const exact = [];
    const seen = new Set();
    for (const selector of exactSelectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (!seen.has(el) && visible(el) && !el.disabled && safeAttr(el, 'aria-disabled') !== 'true') {
          seen.add(el);
          exact.push(el);
        }
      }
    }
    if (exact.length) return exact;

    return [...document.querySelectorAll('button')].filter(el => {
      if (!visible(el) || el.disabled || safeAttr(el, 'aria-disabled') === 'true') return false;
      const signal = [
        safeAttr(el, 'aria-label'),
        safeAttr(el, 'title'),
        safeAttr(el, 'data-testid'),
      ].filter(Boolean).join(' ').toLowerCase();
      return /(^|\b)(send|submit)(\b|$)|发送|提交/.test(signal);
    });
  }

  function center(rect) {
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }

  function inViewport(point) {
    return point.x >= 0 && point.y >= 0 && point.x < window.innerWidth && point.y < window.innerHeight;
  }

  function isTopmostButton(button) {
    const point = center(button.getBoundingClientRect());
    if (!inViewport(point)) return false;
    const hit = document.elementFromPoint(point.x, point.y);
    return Boolean(hit && (hit === button || button.contains(hit)));
  }

  function buttonScore(button, composer) {
    const br = button.getBoundingClientRect();
    const cr = composer.getBoundingClientRect();
    const bp = center(br);
    const target = { x: cr.right, y: cr.bottom };
    const dx = bp.x - target.x;
    const dy = bp.y - target.y;
    const distance = Math.hypot(dx, dy);

    let score = -distance;
    if (bp.x >= cr.left + cr.width * 0.55) score += 250;
    if (bp.y >= cr.top - 40 && bp.y <= cr.bottom + 80) score += 250;
    if (br.left >= cr.left && br.right <= cr.right + 80) score += 120;
    return score;
  }

  function chooseWorkerSendButton(composer) {
    const raw = rawSendButtonCandidates();
    const topmost = raw.filter(isTopmostButton);
    const eligible = topmost.length ? topmost : raw.filter(button => inViewport(center(button.getBoundingClientRect())));
    if (!eligible.length) return { button: null, rawCount: raw.length, eligibleCount: 0 };

    const ranked = eligible
      .map(button => ({ button, score: buttonScore(button, composer) }))
      .sort((a, b) => b.score - a.score);

    if (ranked.length > 1 && Math.abs(ranked[0].score - ranked[1].score) < 1) {
      throw new Error(`Worker send button remained ambiguous after hit testing; raw=${raw.length} eligible=${eligible.length}`);
    }
    return { button: ranked[0].button, rawCount: raw.length, eligibleCount: eligible.length };
  }

  async function findWorkerSendButton(composer, timeoutMs = 5000) {
    const started = Date.now();
    let lastRaw = 0;
    let lastEligible = 0;
    while (Date.now() - started < timeoutMs) {
      const picked = chooseWorkerSendButton(composer);
      lastRaw = picked.rawCount;
      lastEligible = picked.eligibleCount;
      if (picked.button) return picked;
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error(`No usable Worker send button; raw=${lastRaw} eligible=${lastEligible}`);
  }

  function submissionAccepted(marker, projectKey) {
    if (!isWorkerRoot(projectKey)) return true;
    const all = candidates();
    return !all.some(el => editableText(el).includes(marker));
  }

  async function waitForSubmissionAccepted(marker, projectKey, timeoutMs) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (submissionAccepted(marker, projectKey)) return true;
      await new Promise(r => setTimeout(r, 100));
    }
    return submissionAccepted(marker, projectKey);
  }

  async function submitWorkerHandoff(message) {
    const marker = String(message.marker || '');
    const projectKey = String(message.worker_project_key || '');
    const laneId = String(message.lane_id || '');
    const handoffPacketRef = message.handoff_packet_ref ? String(message.handoff_packet_ref) : '';
    if (handoffPacketRef && (!/^[A-Za-z0-9._\/-]{1,512}$/.test(handoffPacketRef) || handoffPacketRef.includes('..'))) {
      throw new Error('Invalid handoff packet ref');
    }
    if (!/^g-p-[A-Za-z0-9]+$/.test(projectKey)) throw new Error('Invalid Worker Project key');
    if (!/^lane-[0-9]{2,}$/.test(laneId)) throw new Error('Invalid lane id');
    if (!isWorkerRoot(projectKey)) throw new Error('Worker handoff submission requires the exact lane Project root');
    if (!/^GAH_WAKE v=1 id=pool-[A-Za-z0-9._:-]{8,128} project=\S+$/.test(marker)) {
      throw new Error('Invalid Worker handoff marker');
    }

    const bootstrap = [
      marker,
      `GAH_BOOTSTRAP repo=${WORKER_CANONICAL_REPO} state=${WORKER_STATE_PATH}`,
      'GAH_HOT_START profile=managed-worker-state-only-v1',
      `GAH_LANE lane_id=${laneId} project_key=${projectKey}`,
      handoffPacketRef ? `GAH_HANDOFF packet=${handoffPacketRef}` : null,
      'GAH_TAKEOVER mode=bootstrap-only: this Project-root handoff is NOT a semantic task dispatch. Read only state/lanes.json, state/chatgpt.json, and the named handoff packet when present. If a packet is present, validate its task/dispatch/generation/fence before takeover. Write this exact marker pool-* id into this lane last_pool_takeover_id (lane-00 may mirror state/chatgpt.json during migration), checkpoint the takeover, then STOP. Do not execute next_action, do not author task artifacts, and do not mutate a backend task CL in this response. The queued fenced wake is delivered only after runtime verifies takeover.',
      'Do not search repository names or repeat owner/permission confirmation unless direct access fails or resource scope changes.',
    ].filter(Boolean).join('\n');

    const initiallyStable = await waitForStableComposer(projectKey, 12000, 1200);
    const prior = editableText(initiallyStable).trim();
    const written = await writeBootstrapStable(projectKey, bootstrap, marker, 12000);
    let composer = written.composer;

    let picked = await findWorkerSendButton(composer, 7000);
    let sendButton = picked.button;
    let sendTestId = safeAttr(sendButton, 'data-testid');
    sendButton.click();

    let accepted = await waitForSubmissionAccepted(marker, projectKey, 2500);
    if (!accepted) {
      composer = findWorkerComposer();
      if (!editableText(composer).includes(marker)) {
        accepted = true;
      } else {
        picked = await findWorkerSendButton(composer, 3000);
        sendButton = picked.button;
        sendTestId = safeAttr(sendButton, 'data-testid');
        sendButton.click();
        accepted = await waitForSubmissionAccepted(marker, projectKey, 5000);
      }
    }
    if (!accepted) throw new Error('Worker send click was not accepted by ChatGPT');

    return {
      ok: true,
      method: 'worker-clear-draft-then-hit-tested-send-click',
      selector: 'fixed-worker-project-textbox',
      send_button_testid: sendTestId,
      send_button_raw_count: picked.rawCount,
      send_button_eligible_count: picked.eligibleCount,
      cleared_existing_draft: Boolean(prior),
      prior_length: prior.length,
      marker_length: marker.length,
      hydration_write_attempts: written.attempts,
      hydration_clear_attempts: written.clearAttempts,
    };
  }

  ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== 'gah-worker-submit-root-handoff') return false;
    submitWorkerHandoff(message)
      .then(sendResponse)
      .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true;
  });
})();
