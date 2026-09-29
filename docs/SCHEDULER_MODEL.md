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
-> bound short chat signal
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

A Worker can request g1 -> g2 as soon as context replacement is needed. It does not wait for a full retention pool or author global lane state, a pool id, a generation/fence, or a second copy of its Reply.

## Liveness

Playwright directly observes whether the current assistant response is running or ended.

Harness reconciles that browser state with backend CL state and the exact expected output.

A running response is positive liveness evidence, not a lease failure. The semantic lease is a lost-observation watchdog, not a maximum Worker execution duration. The default Worker semantic lease is 10 minutes. A currently observed running response remains positive liveness and must not be recovered merely because the original lease boundary is near or crossed; the bounded renewal path preserves the same generation/fence when needed. While the exact current response is still running, Harness must not recover or replace that Worker merely because wall-clock time crossed the original lease. When the remaining lease enters the bounded renewal margin, the maintenance sweep renews the same generation/fence and leaves the Worker owner unchanged. Renewal is deliberately coarse-grained so normal 60-second observation does not create a Git write on every sweep.

A running response is not a completed turn. Tool-result continuation remains inside the bound semantic turn. Do not accept historical/quoted signals or submit another wake into a busy composer as though it started a new response.

The semantic lease and the Worker watchdog are separate mechanisms. The 10-minute semantic lease protects dispatch observation and may be renewed while the exact response is visibly running. It does **not** decide when to invoke Helper.

When Playwright successfully creates/wakes a managed Worker and response-start/admission succeeds, the physical Worker record arms a fixed 30-minute `WORKER_HELPER` watchdog for that exact conversation + wake + dispatch/generation/fence. Lease renewal never moves this deadline. At the deadline, if that exact backend dispatch is still RUNNING, Playwright calls the dedicated `worker_watchdog_expired` bridge path and Harness stages a single-use Worker Helper. This check runs from the retained Worker binding before DOM turn parsing, so a missing/unknown `dispatch_turn_state()` cannot silently suppress the watchdog.

Worker-watchdog Helper state is deliberately distinct from Planner Helper state: `WAIT_WORKER_HELPER`, `worker_helper_result`, `task_cell_worker_helper_prompt`, `WORKER_HELPER_RESULT`, and `worker-helper-*` request ids identify this path. Planner-requested Helper work continues to use its existing `WAIT_HELPER` / `helper_result` path.

`reconcile_dispatch_liveness()` never creates a successor generation, dispatch, fence or wake and does not itself start the 30-minute Worker Helper. It may renew the exact current lease, finalize an already-durable terminal Result, or report an unresolved liveness observation while the independent Playwright watchdog continues toward its deadline.

After a Worker watchdog incident, Helper first inspects and repairs a concrete operational fault when possible. If the exact Worker remains abnormally unresolved and no concrete repairable fault is found, Helper exact-deletes that physical Worker conversation, appends a **HELPER INCIDENT** breadcrumb to the Worker Child Reply, and restores/re-delivers the original same-generation wake/binding through Harness. Watchdog recovery must not synthesize G+1, a new dispatch, a new fence, or a new wake identity. A new Worker generation is created only by the normal semantic continuation/handoff lifecycle.

Harness/runtime is the mechanical watchdog. The 30-minute threshold is an escalation boundary for operational inspection, not by itself proof of semantic failure or a Planner replacement signal.

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

An approved pending continuation resumes even if the Task/direction is unchanged. A Worker completing at g7 does not wait for g10. There is no five-Child settlement barrier; the independent 565 rule limits retained conversations per task/epoch/lane pool, not the work graph.

The role-bound short-signal contract is in `docs/task-cell/README.md`. Planner `done` keeps Planner; Helper `done` retires only the finished Helper after result receipt. Planner `handoff` replaces the sole current Planner and inherits Task/Memory; normal turn count and elapsed time do not cause rotation.

## Terminal lifecycle

Planner `complete` accepts the parent task, closes semantic work, and automatically starts exact task-owned conversation cleanup through Playwright. Delete its remaining Worker generations and Task Cell Planner/Helper chats, then reset their bindings. Failed deletion must not be replaced by merely forgetting the chat id.

Project containers, Foreground, other/unknown chats, durable task history and accepted artifacts remain intact.

Destructive branch/worktree or task-sandbox reclamation still requires explicit user confirmation; it is not the automatic chat cleanup stage.
