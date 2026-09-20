# Stage 1 bounded parallel scheduler

Status: deterministic scheduler core is proven; live two-Project deployment is implemented in extension 0.7.0 and requires durable deployment evidence before the transport is considered live.

## Purpose

Stage 1 adds the smallest scheduler semantics needed for a real two-lane CAH proof without replacing the proven single-lane path.

The first target is coarse semantic MIMD-style fan-out:

```text
                +-> branch A ->+
task -> READY --|              |-> barrier -> reducer -> DONE
                +-> branch B ->+
```

Each branch owns an independent lane assignment plus dispatch generation/fence. Git remains the durable state boundary; Workers remain replaceable compute.

## Implemented smoke semantics

`harness/parallel.py` currently proves these deterministic rules:

- independent READY nodes may occupy distinct enabled lanes concurrently;
- one lane owns at most one RUNNING node;
- `max_parallel` bounds active semantic work;
- every node dispatch gets its own generation and fencing token;
- a completion with a stale/mismatched fence is rejected;
- dependency nodes remain `WAIT_DEP` until every required predecessor is `DONE`;
- a reducer becomes `READY` only after the barrier is satisfied;
- the reducer is dispatched exactly once in the smoke path;
- the same DAG remains valid with one enabled lane, preserving the single-thread fallback.

The static graph contract is `harness/parallel_task.schema.json`.

## Deliberate non-goals for this milestone

This smoke milestone does **not** yet:

- register/activate Sandbox1 in canonical `state/lanes.json`;
- wake two live ChatGPT Projects;
- claim that two browser Workers execute simultaneously;
- integrate work stealing, resource locks, retries, leases, or cancellation propagation;
- replace `harness/single_thread.py`;
- run a Blender/public showcase workload.

Those belong to the next live-proof phase after the deterministic scheduler smoke is green.

## Smoke command

```sh
python harness/parallel.py smoke
python -m unittest tests/test_parallel_scheduler.py
```

The smoke requires this sequence:

1. `branch-a` and `branch-b` dispatch together onto `lane-00` and `lane-01`.
2. Completing only one branch does not release `reduce`.
3. Completing both branches promotes `reduce` from `WAIT_DEP` to `READY`.
4. One reducer dispatch runs with a fresh fence.
5. Reducer completion moves the graph to `DONE`.
6. A stale fence test fails closed.
7. A one-lane topology still executes the same graph sequentially.
