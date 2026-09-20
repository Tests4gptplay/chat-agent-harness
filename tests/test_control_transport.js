'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const transport = require('../extension/control_transport.js');

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

class MemoryStorage {
  constructor(seed = {}) {
    this.data = { ...seed };
  }
  async get(keys) {
    if (keys === null || keys === undefined) return { ...this.data };
    if (typeof keys === 'string') return Object.prototype.hasOwnProperty.call(this.data, keys)
      ? { [keys]: this.data[keys] }
      : {};
    if (Array.isArray(keys)) {
      const out = {};
      for (const key of keys) if (Object.prototype.hasOwnProperty.call(this.data, key)) out[key] = this.data[key];
      return out;
    }
    return {};
  }
  async set(value) {
    Object.assign(this.data, value);
  }
  async remove(keys) {
    for (const key of Array.isArray(keys) ? keys : [keys]) delete this.data[key];
  }
}

function context() {
  return {
    task_id: 'transport-test',
    backend_cl: 'cl/transport-test.backend.json',
    dispatch_id: 'dispatch-transport-test',
    dispatch_generation: 1,
    fence_token: 'fence-transport-test-0001',
  };
}

test('read RPC times out even when fetch ignores AbortSignal', async () => {
  const started = Date.now();
  await assert.rejects(
    transport.call('http://127.0.0.1:8765/api', { op: 'health' }, {
      timeoutMs: 25,
      fetch: async () => new Promise(() => {}),
    }),
    err => {
      assert.equal(err.code, 'LOCAL_RPC_TIMEOUT');
      assert.equal(err.operation, 'health');
      assert.equal(err.outcomeUnknown, false);
      return true;
    }
  );
  assert.ok(Date.now() - started < 500);
});

test('mutation RPC timeout covers a hung response body and is UNKNOWN', async () => {
  await assert.rejects(
    transport.call('http://127.0.0.1:8765/api', { op: 'dispatch_accept' }, {
      timeoutMs: 25,
      fetch: async () => ({
        ok: true,
        status: 200,
        text: async () => new Promise(() => {}),
      }),
    }),
    err => {
      assert.equal(err.code, 'LOCAL_RPC_TIMEOUT');
      assert.equal(err.operation, 'dispatch_accept');
      assert.equal(err.outcomeUnknown, true);
      return true;
    }
  );
});

test('mutation network failure is UNKNOWN but application rejection is definite', async () => {
  await assert.rejects(
    transport.call('x', { op: 'dispatch_accept' }, {
      timeoutMs: 100,
      fetch: async () => { throw new Error('socket reset'); },
    }),
    err => {
      assert.equal(err.code, 'LOCAL_RPC_NETWORK');
      assert.equal(err.outcomeUnknown, true);
      return true;
    }
  );

  await assert.rejects(
    transport.call('x', { op: 'dispatch_accept' }, {
      timeoutMs: 100,
      fetch: async () => ({
        ok: false,
        status: 409,
        text: async () => JSON.stringify({ ok: false, error: 'DISPATCH_STALE' }),
      }),
    }),
    err => {
      assert.equal(err.code, 'DISPATCH_STALE');
      assert.equal(err.outcomeUnknown, false);
      return true;
    }
  );
});

test('admission journal survives restart and reconciles with bounded backoff', async () => {
  let now = Date.parse('2026-09-19T19:00:00Z');
  const clock = () => now;
  const storage = new MemoryStorage();
  const ctx = context();

  const first = transport.journal(storage, clock);
  const prepared = await first.prepare(ctx, {
    lane_id: 'lane-00',
    worker_project_key: 'g-p-test',
    worker_ref: 'pool-worker-0001',
    wake_marker: 'GAH_WAKE v=1 id=wake-transport-test project=git-agent-harness',
  });
  assert.equal(prepared.fresh, true);
  assert.equal(prepared.record.phase, 'PREPARED');

  const duplicate = await first.prepare(ctx, { lane_id: 'lane-00' });
  assert.equal(duplicate.fresh, false);
  await first.started(ctx, { observation: 'assistant-visible' });

  let calls = 0;
  const failed = await first.reconcile(ctx, async () => {
    calls += 1;
    throw new Error('LOCAL_RPC_TIMEOUT:dispatch_accept');
  });
  assert.equal(failed.phase, 'STARTED');
  assert.equal(failed.attempts, 1);
  assert.ok(failed.next_retry_at);
  assert.equal(calls, 1);

  const restarted = transport.journal(storage, clock);
  const beforeBackoff = await restarted.reconcile(ctx, async () => {
    calls += 1;
    return { ok: true, state: 'RUNNING' };
  });
  assert.equal(beforeBackoff.phase, 'STARTED');
  assert.equal(calls, 1);

  now += 1500;
  const admitted = await restarted.reconcile(ctx, async () => {
    calls += 1;
    return { ok: true, state: 'RUNNING' };
  });
  assert.equal(admitted.phase, 'ADMITTED');
  assert.equal(admitted.canonical_state, 'RUNNING');
  assert.equal(calls, 2);

  const duplicateObservation = await restarted.started(ctx, { observation: 'duplicate-assistant-visible' });
  assert.equal(duplicateObservation.phase, 'ADMITTED');
  assert.equal((await restarted.pending()).length, 0);
});

test('admission journal permanently rejects stale identity errors', async () => {
  let now = Date.parse('2026-09-19T19:00:00Z');
  const storage = new MemoryStorage();
  const journal = transport.journal(storage, () => now);
  const ctx = context();
  await journal.prepare(ctx, { lane_id: 'lane-00' });
  await journal.started(ctx);
  const rejected = await journal.reconcile(ctx, async () => {
    throw new Error('DISPATCH_WORKER_MISMATCH');
  });
  assert.equal(rejected.phase, 'REJECTED');
  assert.equal(rejected.next_retry_at, null);
});

test('per-lane storage keys are independent', () => {
  assert.notEqual(transport.laneStorageKey('lane-00'), transport.laneStorageKey('lane-01'));
  assert.throws(() => transport.laneStorageKey('not-a-lane'), /Invalid lane id/);
});

test('lane gate serializes one lane without blocking another', async () => {
  const gate = transport.laneGate();
  let lane0Runs = 0;
  let release0;
  const blocked = new Promise(resolve => { release0 = resolve; });

  const first0 = gate.run('lane-00', async () => {
    lane0Runs += 1;
    await blocked;
    return 'lane0';
  });
  const duplicate0 = gate.run('lane-00', async () => {
    lane0Runs += 1;
    return 'should-not-run';
  });
  const lane1 = gate.run('lane-01', async () => {
    await sleep(5);
    return 'lane1';
  });

  assert.equal(gate.size(), 2);
  assert.equal(await lane1, 'lane1');
  assert.equal(lane0Runs, 1);
  release0();
  assert.equal(await first0, 'lane0');
  assert.equal(await duplicate0, 'lane0');
  await sleep(0);
  assert.equal(gate.size(), 0);
});
