# Localhost wake bridge

Status: current runtime contract.

## Goal

Close the external execution loop without requiring a second metered reasoning API.

```text
Worker writes action
-> GitHub/self-hosted runner executes
-> durable result/evidence reaches Git
-> local_bridge receives a compact wake
-> extension claims the wake
-> exact bound ChatGPT conversation receives GAH_WAKE
-> Worker reads canonical Git state and continues
```

The execution node and browser may be the same machine today; the protocol does not require task truth to live in the browser.

## Authority

- Git = canonical engineering/task state.
- Local bridge queue = disposable wake transport.
- Extension = browser interrupt/lifecycle client.
- `GAH_WAKE` = doorbell only.

Never place task payloads, secrets or large artifacts in a wake marker.

## Wake marker

```text
GAH_WAKE v=1 id=<opaque-id> project=<project-id>
```

On wake, a managed Worker uses the fixed bootstrap and reads canonical state first. With `hot_start.mode=state_only`, a concrete `next_action` may be executed without rereading docs/source. Context expands only when state/evidence requires it.

## Queue semantics

The local bridge owns a small lease-based queue:

```text
PENDING -> CLAIMED -> CONSUMED
              |
              +-> lease expiry / release -> PENDING
```

Duplicate wake delivery must be harmless. The extension remembers the last submitted wake id and canonical Git state remains the final idempotency boundary.

## Extension behavior

The extension:

1. polls the localhost bridge on the shared `gah-wake-poll` alarm;
2. claims one wake;
3. locates the exact configured foreground/Worker conversation as appropriate;
4. refuses to overwrite non-empty user text;
5. submits the compact marker;
6. consumes or releases the wake;
7. reports bounded diagnostic events to the local spool.

It does not scrape assistant output.

## Local telemetry

Disposable diagnostics live under the configured bridge runtime root, typically:

```text
C:\CAH\runtime\
  extension-events\
  errors\
```

Meaningful engineering state belongs in Git, not the telemetry spool.

## Failure policy

Fail closed when:

- the exact bound conversation is not available;
- the current tab navigated to a different conversation;
- the composer contains user text;
- the composer/send control is ambiguous or missing;
- the localhost bridge is unavailable.

A failed wake delays reasoning; it must not corrupt or lose canonical task state.

## Removed transport

The earlier Gmail/Apps Script mailbox fallback was removed from the current runtime because localhost wake is proven and is the active architecture. Git history retains the old implementation if a roaming transport is needed again later.


## Foreground vs backend wake distinction

The extension currently has two distinct roles and they must not be conflated:

1. **backend Worker interrupt/lifecycle** — normal Stage 0 path; wakes the verified current Sandbox0 managed Worker so AI reasoning can continue from canonical Git state;
2. **foreground notification/recovery** — optional on the active Foreground Supervisor path; CL monitoring replaces normal start/heartbeat/terminal business notifications.

A CL-based foreground Supervisor does not eliminate the need for backend Worker wake transport. Until a future Host/browser controller replaces it, a reasoning task that must involve Sandbox0 is incomplete if only the self-hosted runner/executor path runs and no managed Worker is awakened.

## Lane-aware backend routing

The localhost bridge remains one global transport endpoint, but backend wakes are lane-addressed.

Global defaults:

```text
project_id = git-agent-harness
endpoint   = http://127.0.0.1:8765/api
```

Per-wake routing must additionally identify the backend lane, preferably by both stable lane_id and exact worker_project_key. The extension validates both against its registered lane configuration before delivery.

Normal backend delivery:

```text
Harness/backend CL
-> bridge wake {wake_id, lane_id, worker_project_key}
-> extension
-> matching lane registry entry
-> verified current managed Worker for that lane
-> if absent: create/bootstrap Worker in that lane's Project root
-> Worker reads canonical Git state
```

Foreground conversation binding is not part of this normal backend wake route.

Removing exact foreground binding from the normal popup does not remove the extension from the backend path: backend Worker wake, generation switching, 5+1 rollover, and lane-local lifecycle remain core normal-path responsibilities.
