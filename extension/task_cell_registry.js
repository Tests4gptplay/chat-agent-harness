'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const STORAGE_KEY = 'taskCellBinding';
  const DEFAULT_TASK_CELL = Object.freeze({
    v: 1,
    display_name: 'CAH Task Cell',
    project_key: 'g-p-exampletaskcell',
    project_root_url: 'https://chatgpt.com/g/g-p-exampletaskcell-cah-task-cell/project',
    enabled: '__CAH_CONFIGURED__' === 'yes',
    role: 'task_cell',
    lifecycle: 'task_bound',
    release_gate: 'foreground_result_delivered',
    worker_pool: false,
    retirement_policy: 'task_cell_release_gate_only',
    roles: ['planner', 'watchdog', 'helper'],
  });

  function parseProjectRoot(value) {
    if (globalThis.CAHLanes && typeof globalThis.CAHLanes.parseProjectRoot === 'function') {
      return globalThis.CAHLanes.parseProjectRoot(value);
    }
    let u;
    try { u = new URL(String(value || '').trim()); } catch (_) { throw new Error('Invalid ChatGPT Project root URL'); }
    if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') throw new Error('Project root must be on https://chatgpt.com');
    const m = u.pathname.match(/^\/g\/(g-p-[A-Za-z0-9]+)(?:-[^/]+)?\/project\/?$/);
    if (!m) throw new Error('Use the exact ChatGPT Project root URL ending in /project');
    u.hash = '';
    u.search = '';
    return { project_key: m[1], project_root_url: u.toString().replace(/\/$/, '') };
  }

  function normalize(raw) {
    const source = raw || DEFAULT_TASK_CELL;
    let parsed = parseProjectRoot(source.project_root_url || DEFAULT_TASK_CELL.project_root_url);
    // This Task Cell Project kept the same canonical project_key while ChatGPT
    // changed the human-readable root slug to -cah-task-cell. Persisted browser
    // storage from 1.0.0 may still carry the old /...-task-cell/project URL.
    // Canonicalize the known binding by project_key so storage self-heals.
    if (parsed.project_key === DEFAULT_TASK_CELL.project_key) {
      parsed = parseProjectRoot(DEFAULT_TASK_CELL.project_root_url);
    }
    return {
      v: 1,
      display_name: String(source.display_name === 'GAH Task Cell' ? DEFAULT_TASK_CELL.display_name : (source.display_name || DEFAULT_TASK_CELL.display_name)).trim().slice(0, 80) || DEFAULT_TASK_CELL.display_name,
      project_key: parsed.project_key,
      project_root_url: parsed.project_root_url,
      enabled: source.enabled !== false,
      role: 'task_cell',
      lifecycle: 'task_bound',
      release_gate: 'foreground_result_delivered',
      worker_pool: false,
      retirement_policy: 'task_cell_release_gate_only',
      roles: ['planner', 'watchdog', 'helper'],
    };
  }

  async function assertSeparateFromLanes(binding) {
    const lanesApi = globalThis.CAHLanes;
    if (!lanesApi || typeof lanesApi.getRegistry !== 'function') return binding;
    const registry = await lanesApi.getRegistry();
    const conflict = (registry.lanes || []).find(lane =>
      lane && String(lane.project_key || '') === String(binding.project_key || '')
    );
    if (conflict) {
      throw new Error(`Task Cell Project must not also be an execution lane: ${conflict.lane_id}`);
    }
    return binding;
  }

  async function getBinding() {
    const data = await ext.storage.local.get([STORAGE_KEY]);
    let binding;
    try {
      binding = normalize(data[STORAGE_KEY] || DEFAULT_TASK_CELL);
      await assertSeparateFromLanes(binding);
    } catch (_) {
      binding = normalize(DEFAULT_TASK_CELL);
      await assertSeparateFromLanes(binding);
    }
    if (!data[STORAGE_KEY] || JSON.stringify(data[STORAGE_KEY]) !== JSON.stringify(binding)) {
      await ext.storage.local.set({ [STORAGE_KEY]: binding });
    }
    return binding;
  }

  async function saveBinding(raw) {
    const binding = normalize(raw);
    await assertSeparateFromLanes(binding);
    await ext.storage.local.set({ [STORAGE_KEY]: binding });
    return binding;
  }

  function matchesUrl(binding, value) {
    let u;
    try { u = new URL(String(value || '')); } catch (_) { return false; }
    const key = String(binding && binding.project_key || '');
    if (!key || u.hostname !== 'chatgpt.com') return false;
    return u.pathname === `/g/${key}/project`
      || u.pathname.startsWith(`/g/${key}-`)
      || u.pathname.startsWith(`/g/${key}/c/`);
  }

  globalThis.CAHTaskCell = Object.freeze({
    STORAGE_KEY,
    DEFAULT_TASK_CELL,
    parseProjectRoot,
    normalize,
    assertSeparateFromLanes,
    getBinding,
    saveBinding,
    matchesUrl,
  });
})();
