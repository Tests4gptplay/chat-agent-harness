# Task Cell

Task Cell is CAH's managed mode for long-lived or multi-step work.


## Core model

Git is the durable semantic source of truth.

```text
Foreground
  -> Task Contract + initial Plan

Planner
  -> Planner Memory
  -> Child Reply REVIEW / DIRECTION
  -> Plan updates
  -> turn_outcome

Worker
  -> task artifacts
  -> Worker turn reply entry
  -> exact Result Artifact

Helper
  -> bounded operational recovery owner
  -> restore autonomous lifecycle + advance past the fault boundary
  -> exact repair_result artifact

Harness / Playwright
  -> ids, paths, generation/fence, routing, browser transport,
     dispatch admission, generation review, retention, task-chat cleanup, state restore
```

Semantic roles write the meaning of the work. Harness materializes and routes the machine envelope.

Planner and single-use Helper are ordinary ChatGPT conversations in `CAH Task Cell`. Workers are ordinary conversations in the registered Sandbox Projects bound to lanes. A lane is a work channel/Project binding, not one permanent chat. Harness uses Playwright to submit a real task message telling the role to read CAH's common contract, its role MD and the named task/continuity surfaces; there is no bootstrap-only ACK turn.

## Role contracts

- Foreground: `docs/task-cell/FOREGROUND.md`
- Planner: `docs/task-cell/PLANNER.md`
- Worker: `docs/task-cell/WORKER.md`
- Helper: `docs/task-cell/HELPER.md`

Harness/Playwright own mechanical supervision and liveness. A semantic role normally reads this common contract plus its own role contract.

## Canonical semantic surfaces

### Task Contract

`tasks/<task_id>.json`

Contains the stable user goal, hard constraints, acceptance criteria, preserved inputs/assets, and task boundary.

### Plan

`tasks/<task_id>.plan.json`

Contains the current formal strategy from the present state toward completion.

### Planner Memory

`memory/planner/<task_id>/memory.md`

Append-only parent-task reasoning history. Planner generations continue the same ledger.

### Worker Child Reply

`memory/worker/<child_task_id>/reply.md`

Append-only child-local reconciliation ledger.

Worker entries record work/results/blockers. Planner entries record review/acceptance/direction.

### Exact per-turn result surfaces

Harness pre-binds each turn's writable surfaces before inference.

A semantic role writes the supplied semantic body in CAH/Git, writes the applicable lowercase `turn_signal` in that exact pre-bound output as its final durable Git mutation, then emits a fixed `semantic_sync` syscall kick. Harness fetches canonical Git, validates the exact role/turn binding and required durable writes, maps the durable signal to internal state, and advances mechanically. Machine identity, versions, hashes, routing and internal outcome enums are not model-authored bookkeeping.

## Managed turn rhythm

Harness prepares work positions for every registered lane and records the baseline after initialization. Position identifies the target; committed write comparison identifies new directions; machine consumption records prevent duplicate dispatch. The same rule applies on the first Planner turn and when Planner activates another lane mid-task. Memory/log changes alone do not activate workers.

```text
Planner turn
  -> reads Foreground task + Memory + relevant Child Reply
  -> writes semantic directions at the chosen lane work positions
  -> writes Memory/Plan/outcome content as required
  -> done (or rework after corrective instructions)

Harness
  -> consumes new directions and dispatches their exact lanes
  -> also resumes approved pending continuations even if directions are unchanged

Workers
  -> continue: save Reply, replace context; request Planner review at g5/g10/g15...
  -> complete at any generation: immediate Planner acceptance review

Planner acceptance
  -> rework: save corrections and continue Worker work
  -> complete: task-owned chat cleanup, then reset
```

Worker generation review and 5+1 conversation retention are separate rules, neither a fixed number of lanes nor a five-Child settlement barrier. A Worker completing at g7 does not wait for g10.

## Role-bound turn signals

These lowercase values are written as `turn_signal` in the exact bound Result/Outcome artifact after all other semantic writes. The model then emits the fixed `semantic_sync` syscall block; the syscall contains no role signal or routing metadata.

| Bound role | `turn_signal` | Meaning and Harness action |
| --- | --- | --- |
| Planner | `done` | Planning/review turn finished; consume its outputs and keep the Planner conversation. |
| Planner | `rework` | Corrective directions and reasons saved; resume the relevant work and keep Planner. |
| Planner | `complete` | Parent-task acceptance passed; automatically clean this task's chats, then reset. |
| Planner | `handoff` | Context collapsed; inherit Task/Memory in a replacement Planner and retire the predecessor. |
| Planner | `wait` / `need_user` / `error` | Read the corresponding durable wait, question or terminal error content. |
| Worker | `continue` | Work remains; saved Reply allows a new generation, after Planner review when due. |
| Worker | `complete` | Child work ready for Planner acceptance, not final parent-task acceptance. |
| Worker | `blocked` / `error` | Recorded blocker/failure; route the real condition, not false success. |
| Helper | `done` | The Helper result has already been routed and the next legal semantic role has started; this durable signal requests deletion of only that single-use Helper conversation. |

`done` is not a global deletion command. Harness identifies the role from its existing task/epoch/conversation/request binding before interpreting the durable signal; sharing the Task Cell Project does not make Planner and Helper interchangeable. The sync syscall is only a doorbell and is not a second identity or semantic source.

For compatibility and recovery, Playwright may still read the legacy one-word ending from an ended bound response. That path is fallback only. A syscall or legacy ending without the required committed semantic content does not establish successful completion.

## Lifecycle

Playwright observes response start/end and browser state.

Harness owns:

- task/epoch authority;
- generation/fence;
- dispatch/wake/result identity;
- physical Worker placement;
- Planner/Worker conversation replacement;
- all-lane direction consumption, periodic continuation review and 5+1 retention;
- deterministic liveness;
- terminal machine-state restore.

Planner `done` keeps the current Planner. Helper uses a two-phase result: `diagnosis + repair_result` is routed first while Helper remains alive; only after the correct next role has actually started does Helper add durable `turn_signal=done`. Accepted Helper `done` immediately exact-deletes that single-use Helper without a response-end gate; the next incident gets a fresh conversation.

Planner Helper and Worker Helper are visibly different canonical paths. Planner-requested recovery uses `WAIT_HELPER` / `helper_result` / `task_cell_role_prompt`. A managed Worker response-start arms a fixed 30-minute Playwright watchdog; expiry of an exact still-RUNNING Worker uses `WORKER_HELPER` / `WAIT_WORKER_HELPER` / `worker_helper_result` / `task_cell_worker_helper_prompt` and a `worker-helper-*` request id. Lease renewal does not reset that Worker deadline, and DOM turn-parsing failure cannot suppress it. Worker-watchdog recovery preserves the same generation unless normal semantic continuation/handoff later authorizes a successor generation.

If internal state does not expose a clear stall, Helper may inspect the exact affected browser/host state through an already-authorized direct execution surface, but recovery remains driven by direct operational action plus canonical Git/runtime repair. Helper must restore the Task Cell to a self-running lifecycle and advance the task to a next runnable/wakeable semantic role before successful completion. See `docs/task-cell/HELPER.md` for the operational boundary.

Planner `complete` ends semantic work and automatically invokes clean through Playwright for every remaining conversation owned by this task/epoch: all Worker generations in its lanes and Planner/Helper in Task Cell. Only after confirmed cleanup are the corresponding machine bindings reset. Do not equate forgetting bindings with deleting chats or require another user clean command for this task-owned conversation cleanup.

Preserve actual engineering sources/assets/outputs, Project containers, other tasks' or unknown conversations, the user's Foreground chat, Git history and accepted evidence. Harness first exports Planner Memory, Child Reply and related process records to the bound project_directory as records.md (no overwrites), verifies the backup, then prunes only the exact archived CAH continuity caches. Archive failure retains original records and prevents reset; an unbound legacy task retains records. See docs/CLEANER_RULES.md. A cleanup failure remains visible rather than being reported as success.

Chat cleanup does not authorize destructive branch/worktree or task-sandbox reclamation. That remains a separate explicit user decision; no source directory is deleted merely because Planner accepted a task.

## General browser tools

Worker, Planner and Foreground may use the conversational Playwright MCP Tool transport described in `docs/TOOL_SYSTEM.md`. Helper intentionally does not use that chat Tool-call control path. When browser evidence matters to an incident, Helper uses an already-authorized direct browser/host execution surface only as supporting evidence, then performs recovery through direct operational action, canonical Git/runtime correction and `semantic_sync`.

Harness still owns role identity, wakes, monitoring, rollover and cleanup.
