# Lane lifecycle maintenance

Status: current maintenance/control contract.

This document defines explicit maintenance operations for registered CAH backend lanes. These are **runtime lifecycle controls**, not ordinary task steps and not installation procedures.

The first first-class primitive is:

```text
lane_clear
```

Its purpose is to return one registered lane to a clean conversational execution surface while preserving durable CAH state.

## Why this exists

CAH treats ChatGPT Worker conversations as disposable reasoning capacity while Git remains canonical.

That creates two different cleanup problems:

1. **normal generation retirement** — bounded 5+1 rollover removes old managed Workers as part of ordinary lifecycle;
2. **explicit lane reset** — remove all disposable conversations associated with one registered CAH lane so the next workload starts from a clean Worker surface.

These operations must not be conflated.

Normal retirement is automatic pool housekeeping. `lane_clear` is an explicit maintenance operation.

## Semantic contract

```text
lane_clear(lane)
  destroy:
    disposable ChatGPT conversations inside the exact registered lane Project

  preserve:
    ChatGPT Project itself
    lane registration
    project_key identity
    canonical Git engineering/task history
    accepted artifacts and evidence
    topology registration

  terminal proof:
    status = DONE
    deleted_count = N
    remaining_count = 0
```

The operation is scoped by exact `lane_id + project_key`, never by display name alone.

## Preconditions

A normal `lane_clear` should be issued only when clearing the lane cannot destroy the sole live execution context for active work.

Preferred preconditions:

```text
lane registered
lane identity unambiguous
no unsafe active task ownership
no unverified Worker handoff
no destructive UI ambiguity
```

For showcase/benchmark cleanup, both target lanes should normally be `IDLE` before clearing.

If a lane is actively executing work, the caller should first bring that work to a safe scheduler boundary or cancel/terminate it through the appropriate task-control path. `lane_clear` is not an emergency-stop primitive.

## Control path

The current implementation uses the Git-backed control request plus localhost/browser control runtime:

```text
explicit maintenance request
        |
        v
state/chatgpt.json control_request
        |
        | kind = lane_clear
        | lane_id + project_key
        v
localhost bridge
        |
        v
Chromium extension lane_clear runtime
        |
        v
exact ChatGPT Project root
        |
        v
discover Project conversation cards
        |
        v
delete each conversation through visible UI
        |
        v
verify Project conversation set is empty
        |
        v
Git control result
  DONE
  deleted_count
  remaining_count
```

Current implementation references:

- `local_bridge/control.py` — Git-backed control transition/completion;
- `local_bridge/server.py` — bridge control endpoint;
- `extension/lane_clear_runtime.js` — Project-scoped browser deletion runtime.

The browser implementation deliberately uses visible Project UI rather than undocumented ChatGPT internal APIs.

## Safety boundaries

A lane reset is intentionally destructive to conversation state, so it fails closed.

Required boundaries:

- target must be an explicitly registered CAH lane;
- exact `project_key` must match the registered Project;
- the Project container itself must never be deleted;
- removing/clearing a lane must never modify unrelated ChatGPT Projects;
- canonical Git history/evidence must not be erased as part of conversation cleanup;
- ambiguous Project/conversation discovery must stop rather than broaden deletion scope;
- browser-session/account state is never treated as canonical task truth.

A successful UI action without durable Git completion is not sufficient proof.

## Relationship to Worker rollover

`lane_clear` does **not** replace the 5+1 Worker lifecycle.

Use normal rollover/retirement for:

- context-compaction handoff;
- routine generation replacement;
- bounded pool housekeeping;
- continuity-preserving Worker rotation.

Use `lane_clear` for:

- showcase or benchmark reset;
- clearing accumulated disposable Worker chats;
- recovery from stale/bad conversation surfaces after verified durable state is safe;
- restoring a reproducible clean lane baseline;
- deliberately restarting semantic reasoning while keeping durable project state.

## Relationship to topology

A lane reset does not unregister the lane.

After a successful clear:

```text
state/lanes.json
  lane remains registered
  lane remains enabled unless separately changed
  Project identity remains unchanged

browser Project
  conversation set = empty

next workload
  CAH bootstraps a fresh managed Worker as needed
```

Topology changes remain a separate control primitive.

## Live proof

The maintenance path is live-proven.

Historical Sandbox0 proof established Project-root card deletion and durable `DONE` completion.

A later pre-showcase dual-lane cleanup on 2026-09-19 exercised the same primitive against both registered lanes:

```text
lane-00 / CAH Sandbox0
  deleted_count   = 8
  remaining_count = 0

lane-01 / CAH Sandbox1
  deleted_count   = 1
  remaining_count = 0

canonical topology afterward
  registered_count = 2
  enabled_count    = 2
  both lanes       = IDLE
```

This demonstrates that CAH can reset disposable conversational execution state without deleting lane Projects or canonical Git state.

## Maintenance-control family

`lane_clear` belongs to the lifecycle/maintenance control plane rather than ordinary semantic scheduling.

Current/future controls in this family include:

```text
Worker rollover / retirement
lane_clear
topology reconcile
host update
extension reload
health / diagnostics
```

They share one design principle:

> maintenance may replace or repair disposable runtime state, but durable Git truth and explicit identity boundaries remain authoritative.

## Documentation layering

This contract intentionally lives outside `AGENTS.md`.

```text
AGENTS.md
  -> hot-path runtime rules only

docs/LIFECYCLE_MAINTENANCE.md
  -> low-frequency maintenance/control semantics

docs/CONVERSATION_POOL.md
  -> normal Worker pool/rollover lifecycle
```

This keeps one-time or low-frequency operational knowledge out of the high-frequency Worker context path.
