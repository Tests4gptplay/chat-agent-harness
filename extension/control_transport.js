'use strict';

// Runtime-neutral control-plane primitives, also exercised directly by Node tests.
((root) => {
  const READ_OPS = new Set([
    'health', 'dispatch_status', 'worker_takeover_status', 'extension_runtime_status',
    'foreground_task_status', 'topology_status', 'control_status', 'artifact_read',
    'task_model_policy',
  ]);
  const ADMISSION_PREFIX = 'gahAdmission:';
  const LANE_WAKE_PREFIX = 'gahLastSubmittedWake:';
  const serialActive = new Map();

  function identity(ctx) {
    if (!ctx || !ctx.task_id || !ctx.backend_cl || !ctx.dispatch_id ||
        !Number.isInteger(Number(ctx.dispatch_generation)) || Number(ctx.dispatch_generation) < 1 ||
        String(ctx.fence_token || '').length < 8) throw new Error('Invalid admission identity');
    return [ctx.task_id, ctx.backend_cl, ctx.dispatch_id, Number(ctx.dispatch_generation), ctx.fence_token].join('|');
  }

  function key(ctx) { return ADMISSION_PREFIX + encodeURIComponent(identity(ctx)); }
  function laneStorageKey(laneId) {
    const value = String(laneId || '').trim();
    if (!/^lane-[0-9]{2,}$/.test(value)) throw new Error('Invalid lane id');
    return LANE_WAKE_PREFIX + encodeURIComponent(value);
  }

  function isMutation(op) { return op !== 'event' && !READ_OPS.has(op); }
  function timeoutFor(op) { return op === 'event' ? 3000 : READ_OPS.has(op) ? 12000 : 45000; }

  function rpcError(code, op, message, unknown = false, cause = null) {
    const error = new Error(message || code);
    error.code = code;
    error.operation = op;
    error.outcomeUnknown = Boolean(unknown);
    if (cause) error.cause = cause;
    return error;
  }

  async function serial(k, fn) {
    const previous = serialActive.get(k) || Promise.resolve();
    const next = previous.catch(() => {}).then(fn);
    serialActive.set(k, next);
    try { return await next; }
    finally { if (serialActive.get(k) === next) serialActive.delete(k); }
  }

  async function call(endpoint, payload, options = {}) {
    const op = String(payload && payload.op || '');
    const mutation = isMutation(op);
    const timeoutMs = Number(options.timeoutMs || timeoutFor(op));
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1) throw new Error('Invalid RPC timeout');
    const controller = new AbortController();
    const fetchFn = options.fetch || root.fetch;
    if (typeof fetchFn !== 'function') throw new Error('fetch unavailable');
    let timer = null;
    let timedOut = false;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        timedOut = true;
        try { controller.abort(); } catch (_) {}
        reject(rpcError('LOCAL_RPC_TIMEOUT', op, `LOCAL_RPC_TIMEOUT:${op}`, mutation));
      }, timeoutMs);
    });

    const request = (async () => {
      let response;
      try {
        response = await fetchFn(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-GAH-Bridge': '1' },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
      } catch (error) {
        if (timedOut) throw rpcError('LOCAL_RPC_TIMEOUT', op, `LOCAL_RPC_TIMEOUT:${op}`, mutation, error);
        throw rpcError('LOCAL_RPC_NETWORK', op, `LOCAL_RPC_NETWORK:${op}: ${String(error && error.message || error)}`, mutation, error);
      }

      let text;
      try { text = await response.text(); }
      catch (error) {
        if (timedOut) throw rpcError('LOCAL_RPC_TIMEOUT', op, `LOCAL_RPC_TIMEOUT:${op}`, mutation, error);
        throw rpcError('LOCAL_RPC_BODY', op, `LOCAL_RPC_BODY:${op}: ${String(error && error.message || error)}`, mutation, error);
      }

      let parsed;
      try { parsed = JSON.parse(text); }
      catch (error) {
        throw rpcError('LOCAL_RPC_NON_JSON', op, `LOCAL_RPC_NON_JSON:${op}:HTTP_${response.status}`, mutation, error);
      }
      if (!response.ok || !parsed.ok) {
        const code = String(parsed.error || 'LOCAL_RPC_REJECTED');
        const error = rpcError(code, op, `${code}${parsed.detail ? ': ' + parsed.detail : ''}`, false);
        error.response = parsed;
        throw error;
      }
      return parsed;
    })();

    try { return await Promise.race([request, deadline]); }
    finally { if (timer) clearTimeout(timer); }
  }

  function retryDelayMs(attempt) {
    const n = Math.max(1, Number(attempt || 1));
    return Math.min(60000, 1000 * (2 ** Math.min(n - 1, 6)));
  }

  function journal(storage, clock = () => Date.now()) {
    async function read(ctx) {
      const k = key(ctx);
      const all = await storage.get(k);
      return all && all[k] ? all[k] : null;
    }
    async function change(ctx, fn) {
      const k = key(ctx);
      return serial(k, async () => {
        const old = await read(ctx);
        const value = await fn(old);
        if (value) await storage.set({ [k]: value });
        return value;
      });
    }
    return {
      read,
      async prepare(ctx, meta) {
        let fresh = false;
        const record = await change(ctx, old => {
          if (old) return old;
          fresh = true;
          return {
            v: 1, context: { ...ctx }, ...meta, phase: 'PREPARED', attempts: 0,
            prepared_at: new Date(clock()).toISOString(), updated_at: new Date(clock()).toISOString(),
            next_retry_at: null, last_error: null,
          };
        });
        return { fresh, record };
      },
      async started(ctx, meta = {}) {
        return change(ctx, old => {
          if (old && ['ADMITTED', 'REJECTED'].includes(old.phase)) return old;
          const at = new Date(clock()).toISOString();
          return {
            ...(old || { v: 1, context: { ...ctx }, attempts: 0, prepared_at: at }),
            ...meta, phase: 'STARTED', started_at: old && old.started_at || at,
            updated_at: at, next_retry_at: null,
          };
        });
      },
      async reject(ctx, reason) {
        return change(ctx, old => old ? {
          ...old, phase: 'REJECTED', last_error: String(reason || 'rejected').slice(0, 600),
          updated_at: new Date(clock()).toISOString(), next_retry_at: null,
        } : old);
      },
      async forgetUnsent(ctx) { return serial(key(ctx), () => storage.remove(key(ctx))); },
      async reconcile(ctx, admit) {
        return change(ctx, async old => {
          if (!old || old.phase !== 'STARTED') return old;
          const now = clock();
          const retryAt = old.next_retry_at ? Date.parse(old.next_retry_at) : 0;
          if (Number.isFinite(retryAt) && retryAt > now) return old;
          const attempts = Number(old.attempts || 0) + 1;
          const record = { ...old, attempts, updated_at: new Date(now).toISOString() };
          try {
            const result = await admit(record);
            if (!result || result.ok !== true) throw new Error('ADMISSION_PENDING');
            record.phase = 'ADMITTED';
            record.last_error = null;
            record.next_retry_at = null;
            record.admitted_at = new Date(clock()).toISOString();
            record.canonical_state = result.state || null;
          } catch (error) {
            record.last_error = String(error && error.message || error).slice(0, 600);
            if (/DISPATCH_(STALE|TASK_MISMATCH|WORKER_MISMATCH|LANE_MISMATCH)/.test(record.last_error)) {
              record.phase = 'REJECTED';
              record.next_retry_at = null;
            } else {
              record.next_retry_at = new Date(now + retryDelayMs(attempts)).toISOString();
            }
          }
          return record;
        });
      },
      async pending() {
        const all = await storage.get(null);
        return Object.entries(all || {}).filter(([k, v]) => k.startsWith(ADMISSION_PREFIX) && v &&
          ['PREPARED', 'STARTED'].includes(v.phase)).map(([, v]) => v);
      },
    };
  }

  function laneGate() {
    const active = new Map();
    return {
      run(laneId, fn) {
        const id = String(laneId || '');
        if (active.has(id)) return active.get(id);
        const promise = Promise.resolve().then(fn);
        active.set(id, promise);
        return promise.finally(() => { if (active.get(id) === promise) active.delete(id); });
      },
      active(laneId) { return active.has(String(laneId || '')); },
      size() { return active.size; },
    };
  }

  const api = Object.freeze({
    call, journal, identity, key, laneStorageKey, laneGate, retryDelayMs, timeoutFor, isMutation,
  });
  root.CAHControlTransport = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
