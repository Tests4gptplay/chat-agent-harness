'use strict';

(() => {
  const ext = globalThis.browser || globalThis.chrome;
  const STORAGE_KEY = 'laneRegistry';
  const LEGACY_POOL_KEY = 'workerPoolState';
  const LEGACY_PENDING_KEY = 'workerPendingTabId';
  const DEFAULT_PROJECT_ID = 'git-agent-harness';
  const DEFAULT_ENDPOINT = 'http://127.0.0.1:8765/api';
  const SOFT_LIMIT = 5;
  const RUNTIME_EPOCH = 3;
  const LEGACY_LANE = Object.freeze({
    lane_id: 'lane-00',
    display_name: 'CAH Sandbox0',
    project_key: 'g-p-examplelane00',
    project_root_url: 'https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project',
    enabled: '__CAH_CONFIGURED__' === 'yes',
  });
  const SANDBOX1_LANE = Object.freeze({
    lane_id: 'lane-01',
    display_name: 'CAH Sandbox1',
    project_key: 'g-p-examplelane01',
    project_root_url: 'https://chatgpt.com/g/g-p-examplelane01-cah-sandbox1/project',
    enabled: '__CAH_CONFIGURED__' === 'yes',
  });
  const BOOTSTRAP_LANES = Object.freeze([LEGACY_LANE, SANDBOX1_LANE]);

  function now() { return new Date().toISOString(); }

  function parseProjectRoot(value) {
    let u;
    try { u = new URL(String(value || '').trim()); } catch (_) { throw new Error('Invalid ChatGPT Project root URL'); }
    if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com') throw new Error('Project root must be on https://chatgpt.com');
    const m = u.pathname.match(/^\/g\/(g-p-[A-Za-z0-9]+)(?:-[^/]+)?\/project\/?$/);
    if (!m) throw new Error('Use the exact ChatGPT Project root URL ending in /project');
    u.hash = '';
    u.search = '';
    return { project_key: m[1], project_root_url: u.toString().replace(/\/$/, '') };
  }

  function emptyPool(lane) {
    return {
      v: 1,
      project_key: lane.project_key,
      project_root: lane.project_root_url,
      soft_limit: SOFT_LIMIT,
      current: null,
      managed: [],
      handoff: null,
      updated_at: now(),
    };
  }

  function normalizeLane(raw, fallbackId) {
    let parsed = parseProjectRoot(raw.project_root_url || raw.project_root || '');
    const branded = BOOTSTRAP_LANES.find(item => item.project_key === parsed.project_key);
    if (branded) parsed = parseProjectRoot(branded.project_root_url);
    const laneId = String(raw.lane_id || fallbackId || '').trim();
    if (!/^lane-[0-9]{2,}$/.test(laneId)) throw new Error('lane_id must look like lane-00');
    const display = String(raw.display_name || laneId).trim().slice(0, 80) || laneId;
    const lane = {
      lane_id: laneId,
      display_name: branded && /^GAH Sandbox[01]$/.test(display) ? branded.display_name : display,
      project_key: parsed.project_key,
      project_root_url: parsed.project_root_url,
      enabled: raw.enabled !== false,
      pending_remove: Boolean(raw.pending_remove),
      pool_state: raw.pool_state || null,
      pending_tab_id: Number.isInteger(raw.pending_tab_id) ? raw.pending_tab_id : null,
    };
    if (!lane.pool_state || lane.pool_state.project_key !== lane.project_key) lane.pool_state = emptyPool(lane);
    lane.pool_state = {
      ...lane.pool_state,
      project_key: lane.project_key,
      project_root: lane.project_root_url,
      soft_limit: Number(lane.pool_state.soft_limit || SOFT_LIMIT),
    };
    return lane;
  }

  function nextLaneId(lanes) {
    let max = -1;
    for (const lane of lanes || []) {
      const m = String(lane && lane.lane_id || '').match(/^lane-(\d+)$/);
      if (m) max = Math.max(max, Number(m[1]));
    }
    return `lane-${String(max + 1).padStart(2, '0')}`;
  }

  function baseRegistry(lanes) {
    return {
      v: 1,
      runtime_epoch: RUNTIME_EPOCH,
      desired_version: 1,
      lanes,
      updated_at: now(),
    };
  }

  function resetRuntimeLane(raw, fallbackId) {
    const lane = normalizeLane(raw, fallbackId);
    return {
      ...lane,
      pool_state: emptyPool(lane),
      pending_tab_id: null,
    };
  }

  async function getRegistry() {
    const data = await ext.storage.local.get([STORAGE_KEY, LEGACY_POOL_KEY, LEGACY_PENDING_KEY]);
    if (data[STORAGE_KEY] && Array.isArray(data[STORAGE_KEY].lanes)) {
      const prior = data[STORAGE_KEY];
      const priorEpoch = Number(prior.runtime_epoch || 0);
      let lanes = prior.lanes.map((raw, i) =>
        priorEpoch < 2
          ? resetRuntimeLane(raw, `lane-${String(i).padStart(2, '0')}`)
          : normalizeLane(raw, `lane-${String(i).padStart(2, '0')}`)
      );

      if (priorEpoch < RUNTIME_EPOCH) {
        const existingIds = new Set(lanes.map(lane => lane.lane_id));
        const existingKeys = new Set(lanes.map(lane => lane.project_key));
        let topologyChanged = false;
        for (const bootstrap of BOOTSTRAP_LANES) {
          if (existingIds.has(bootstrap.lane_id) || existingKeys.has(bootstrap.project_key)) continue;
          lanes.push(resetRuntimeLane(bootstrap, bootstrap.lane_id));
          existingIds.add(bootstrap.lane_id);
          existingKeys.add(bootstrap.project_key);
          topologyChanged = true;
        }

        const migrated = {
          ...prior,
          v: 1,
          runtime_epoch: RUNTIME_EPOCH,
          desired_version: Number(prior.desired_version || 1) + (topologyChanged ? 1 : 0),
          lanes,
          bootstrap_parallel: {
            v: 1,
            status: 'PENDING',
            target_lane_ids: BOOTSTRAP_LANES.map(lane => lane.lane_id),
            requested_at: now(),
            completed_at: null,
            last_error: null,
          },
          migrated_runtime_at: now(),
          migration_reason: priorEpoch < 2
            ? 'discard_pre_lane_runtime_cache_and_enable_parallel_bootstrap'
            : 'enable_reserved_parallel_lane',
          updated_at: now(),
        };
        await ext.storage.local.set({
          [STORAGE_KEY]: migrated,
          [LEGACY_POOL_KEY]: null,
          [LEGACY_PENDING_KEY]: null,
        });
        return migrated;
      }

      lanes = lanes.map((raw, i) =>
        normalizeLane(raw, `lane-${String(i).padStart(2, '0')}`)
      );
      const normalized = { ...prior, v: 1, runtime_epoch: RUNTIME_EPOCH, lanes };
      if (prior.lanes.some((old, i) => old.display_name !== lanes[i].display_name || old.project_root_url !== lanes[i].project_root_url)) {
        await ext.storage.local.set({ [STORAGE_KEY]: normalized });
      }
      return normalized;
    }

    const lanes = BOOTSTRAP_LANES.map((raw, i) =>
      resetRuntimeLane(raw, `lane-${String(i).padStart(2, '0')}`)
    );
    const registry = {
      ...baseRegistry(lanes),
      bootstrap_parallel: {
        v: 1,
        status: 'PENDING',
        target_lane_ids: BOOTSTRAP_LANES.map(lane => lane.lane_id),
        requested_at: now(),
        completed_at: null,
        last_error: null,
      },
    };
    await ext.storage.local.set({
      [STORAGE_KEY]: registry,
      [LEGACY_POOL_KEY]: null,
      [LEGACY_PENDING_KEY]: null,
    });
    return registry;
  }

  async function saveRegistry(registry) {
    const lanes = (registry.lanes || []).map((lane, i) => normalizeLane(lane, `lane-${String(i).padStart(2, '0')}`));
    const out = { ...registry, v: 1, runtime_epoch: RUNTIME_EPOCH, lanes, updated_at: now() };
    await ext.storage.local.set({ [STORAGE_KEY]: out });
    return out;
  }

  async function applyDesired(desired) {
    const current = await getRegistry();
    const existing = new Map(current.lanes.map(l => [l.lane_id, l]));
    const usedKeys = new Set();
    const next = [];
    for (let i = 0; i < desired.length; i += 1) {
      const raw = { ...desired[i] };
      if (!raw.lane_id) raw.lane_id = nextLaneId([...current.lanes, ...next]);
      const old = existing.get(raw.lane_id);
      const lane = normalizeLane({
        ...raw,
        pool_state: old && old.project_key === parseProjectRoot(raw.project_root_url).project_key ? old.pool_state : null,
        pending_tab_id: old && old.project_key === parseProjectRoot(raw.project_root_url).project_key ? old.pending_tab_id : null,
        pending_remove: false,
      }, raw.lane_id);
      if (usedKeys.has(lane.project_key)) throw new Error(`Project already registered by another lane: ${lane.project_key}`);
      usedKeys.add(lane.project_key);
      next.push(lane);
    }

    // Keep removed lanes temporarily as disabled routing stubs so the last live
    // control Worker can finish topology reconciliation safely.
    const desiredIds = new Set(next.map(l => l.lane_id));
    for (const old of current.lanes) {
      if (!desiredIds.has(old.lane_id)) next.push({ ...old, enabled: false, pending_remove: true });
    }

    const out = {
      v: 1,
      desired_version: Number(current.desired_version || 0) + 1,
      lanes: next,
      updated_at: now(),
    };
    return saveRegistry(out);
  }

  function publicDesired(registry) {
    return (registry.lanes || [])
      .filter(lane => !lane.pending_remove)
      .map(lane => ({
        lane_id: lane.lane_id,
        display_name: lane.display_name,
        project_key: lane.project_key,
        project_root_url: lane.project_root_url,
        enabled: Boolean(lane.enabled),
      }));
  }

  function findLane(registry, laneId, projectKey) {
    return (registry.lanes || []).find(lane =>
      lane && lane.lane_id === laneId && (!projectKey || lane.project_key === projectKey)) || null;
  }

  function controlLane(registry, desired) {
    const desiredIds = new Set((desired || []).filter(x => x.enabled !== false).map(x => x.lane_id));
    const verified = (registry.lanes || []).find(lane => {
      const current = lane && lane.pool_state && lane.pool_state.current;
      return current && current.handoff_verified === true && current.status === 'current'
        && (desiredIds.has(lane.lane_id) || lane.pending_remove);
    });
    if (verified) return verified;
    return (registry.lanes || []).find(lane => desiredIds.has(lane.lane_id))
      || (registry.lanes || []).find(lane => lane.pending_remove)
      || null;
  }

  globalThis.CAHLanes = Object.freeze({
    STORAGE_KEY,
    DEFAULT_PROJECT_ID,
    DEFAULT_ENDPOINT,
    SOFT_LIMIT,
    RUNTIME_EPOCH,
    LEGACY_LANE,
    SANDBOX1_LANE,
    BOOTSTRAP_LANES,
    parseProjectRoot,
    normalizeLane,
    emptyPool,
    nextLaneId,
    getRegistry,
    saveRegistry,
    applyDesired,
    publicDesired,
    findLane,
    controlLane,
  });
})();
