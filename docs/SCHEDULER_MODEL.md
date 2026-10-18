# CAH scheduler model

Status: active backend scheduling contract.

CAH schedules semantic continuations. Git is durable state; ChatGPT conversations are replaceable execution contexts.

## Core mapping

```text
logical task / Child            ~ schedulable semantic work
backend CL                      ~ machine execution state
Worker conversation             ~ replaceable execution context
lane                            ~ physical execution lane
dispatch_id + generation        ~ current dispatch sequence
fence_token                     ~ stale-generation authority boundary
wake                            ~ doorbell
response-start admission        ~ mechanical execution admission
Result Artifact                 ~ semantic dispatch result
Worker Child Reply              ~ child-local semantic continuity
handoff packet                  ~ generation-switch save point
Git                             ~ durable canonical state/history
```

## Dispatch flow

Harness creates the dispatch before inference:

```text
READY dispatch
-> exact wake
-> Worker response starts
-> Harness sets RUNNING
-> Harness binds current Worker owner
-> Worker performs semantic work
-> Worker reply entry
-> semantic content in exact Result Artifact
-> final bound Git turn_signal
-> deterministic finalizer
-> terminal / wait / next READY dispatch
```

The semantic Worker starts real work in the same response that establishes admission.

The exact result ref is known before inference. For Planner Children it is unique per wake/generation, so an old generation's result path cannot satisfy the current dispatch.

## Machine authority

Harness owns:

- task / control epoch;
- lane / project placement;
- dispatch id / generation / fence;
- exact expected result ref;
- Worker owner binding;
- response-start admission;
- lease/liveness;
- Worker watchdog state and Helper escalation;
- physical conversation replacement.

Semantic roles consume those refs and write semantic payloads.

## Waits and continuations

A task that needs deterministic external work may enter `WAIT_RESULT`, `WAIT_RESOURCE`, or `WAIT_DEP`.

The completion path writes durable evidence and produces the next fresh dispatch/fence when semantic continuation is required.

## Worker context replacement

Model-visible context compaction is a generation-switch boundary.

```text
current Worker
-> saves current frontier in Worker Child Reply
-> continue
-> Harness binds worker_rollover_request and any thin handoff refs
-> Harness creates fresh dispatch/fence
-> replacement conversation receives the real continuation wake
-> response-start switches canonical Worker owner
-> replacement continues from Child Reply + handoff packet
```

The replacement performs useful semantic work in its first response.

A Worker can request g1 -> g2 as soon as context replacement is needed. It does not wait for a full retention lineage or author global lane state, a lineage id, a generation/fence, or a second copy of its Reply.

## Liveness

Playwright owns managed Worker and Planner physical watchdog timing.

When a managed Worker wake reaches real response-start/admission, the physical Worker record arms one fixed 30-minute `WORKER_HELPER` deadline bound to the exact conversation + wake + dispatch/generation/fence.

When a managed Planner turn reaches real response-start, the physical Planner role binding arms one fixed 30-minute `PLANNER_PHYSICAL_WATCHDOG` deadline bound to the exact conversation + Planner request/output/generation/fence. Managed Planner turns do not use semantic-output timeout for liveness. Expiry is handled mechanically by Harness through canonical validation, same-conversation Stop + exact wake replay, and only if that fails exact physical-conversation replacement within the same Planner generation. A failed recovery transaction re-arms another 30-minute cycle while that exact Planner turn remains unresolved; recovery failure is not a terminal watchdog state.

Managed `planner_worker_child` dispatches do **not** use the old 10-minute semantic-lease renewal loop. `reconcile_dispatch_liveness()` may observe the exact managed Worker and finalize an already-durable terminal Result, but it does not renew a managed Worker lease, create a successor generation, or decide Helper escalation. The independent 30-minute Playwright watchdog is the only managed-Worker liveness owner.

The 10-minute semantic lease remains only for non-managed/legacy semantic dispatch classes that still require lost-observation protection. That compatibility lease is outside the managed Task Cell Worker watchdog.

At the 30-minute deadline, if the exact managed backend dispatch is still RUNNING, Playwright calls `worker_watchdog_expired` directly from the retained Worker binding before DOM turn parsing. A missing or unknown `dispatch_turn_state()` therefore cannot suppress Worker Helper escalation.

Worker-watchdog Helper state is deliberately distinct from Planner Helper state: `WAIT_WORKER_HELPER`, `worker_helper_result`, `task_cell_worker_helper_prompt`, `WORKER_HELPER_RESULT`, and `worker-helper-*` request ids identify this path. Planner-requested Helper work continues to use `WAIT_HELPER` / `helper_result`.

After a Worker watchdog incident, Helper first inspects and repairs a concrete operational fault when possible. If the exact Worker remains abnormally unresolved and no concrete repairable fault is found, Helper exact-deletes that physical Worker conversation, appends a **WORKER_HELPER INCIDENT** breadcrumb to the Worker Child Reply, and restores/re-delivers the original same-generation wake/binding through Harness.

The physical retry is not a semantic handoff. Harness records it as a same-generation physical retry, keeps the original generation/dispatch/fence/wake/worker owner, and switches only the local current conversation pointer after the replacement response-start. A new Worker generation is created only by the normal semantic continuation/handoff lifecycle.

For Planner-watchdog recovery, physical replacement preserves the exact control epoch, Planner generation/fence, original request/challenge/output/turn identity and semantic direction; only the Planner conversation binding changes. Planner `handoff` remains the only semantic Planner generation transition.

The 30-minute thresholds are escalation boundaries for operational inspection, not by themselves proof of semantic failure or permission to change semantic generation.

## Generation/fencing

Physical conversations can be replaced. Dispatch generations are monotonic.

Each new recovery/continuation receives a fresh dispatch identity and fence. Exact current refs determine which result can advance the backend CL.

## Lane dispatch and generation review

Every Planner turn exposes the registered lanes' work positions. Harness takes the baseline after preparing headers and binds each position to its exact lane/Project. After the bound Planner ending, it compares committed directions and consumes each new write once. First dispatch and mid-task lane activation follow the same rule. Neither AI-written versions/hashes nor an AI-maintained active-lane list is needed.

```text
Worker continue at a non-review generation -> fresh Worker generation
Worker continue at g5, g10, g15... -> Planner intermediate review
Planner done/rework -> resume pending continuation with approved/corrected direction
Worker complete at any generation -> immediate Planner acceptance review
```

An approved pending continuation resumes even if the Task/direction is unchanged. A Worker completing at g7 does not wait for g10. There is no five-Child settlement barrier; the independent 565 rule limits retained conversations per task/epoch/lane lineage, not the work graph.

The role-bound Git-signal contract is in `docs/task-cell/README.md`. Planner `done` keeps Planner; Helper `done` retires only the finished Helper after result receipt. Planner `handoff` replaces the sole current Planner and inherits Task/Memory. Separately, Harness enforces the deterministic Planner prompt ceiling (default 10 response-starts per generation) after the current turn is fully reduced; watchdog replays continue that same count. Elapsed wall time by itself does not cause semantic Planner rotation.

## Terminal lifecycle

Planner `complete` accepts the parent task, closes semantic work, and automatically starts exact task-owned conversation cleanup through Playwright. Delete its remaining Worker generations and Task Cell Planner/Helper chats, then reset their bindings. Failed deletion must not be replaced by merely forgetting the chat id.

Project containers, Foreground, other/unknown chats, durable task history and accepted artifacts remain intact.

Destructive branch/worktree or task-sandbox reclamation still requires explicit user confirmation; it is not the automatic chat cleanup stage.
