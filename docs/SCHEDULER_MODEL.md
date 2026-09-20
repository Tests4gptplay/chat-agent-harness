# CAH scheduler model

Status: active architecture contract for backend task scheduling.

CAH should be reasoned about as a semantic scheduler/runtime, not as a collection of chats. CPU/GPU scheduler concepts are useful when they preserve durable task identity, bounded interruption, explicit wait states, and resumable execution.

## Core mapping

```text
logical task / continuation     ~ schedulable task/thread context
backend CL                      ~ TCB/PCB + scheduler-visible state
Worker conversation             ~ replaceable execution context
lane                            ~ execution queue / compute lane
dispatch_id + generation        ~ dispatch sequence / epoch
fence_token                     ~ stale-writer rejection token
wake                            ~ interrupt / doorbell
semantic ACK                    ~ accepted dispatch / context installed
WAIT_RESULT                     ~ blocked on I/O / event
executor result                 ~ completion event / interrupt
handoff packet                  ~ context-switch save image
Worker takeover                 ~ context restore + scheduler ACK
5+1 ring                        ~ bounded generation-retention ring
Git                             ~ durable scheduler/task memory
```

The analogy is semantic and control-plane level only. CAH must not imitate instruction-level timing, SMT, SIMT/warps, cache coherence, or hardware lockstep.

## Scheduler states

A backend task continuation should use scheduler states rather than conversational heuristics:

```text
READY
  -> DISPATCHED
  -> ACKED
  -> RUNNING
       |\
       | -> WAIT_RESULT / WAIT_RESOURCE / WAIT_DEP
       |        |
       |        -> event/completion -> READY(next continuation)
       |
       -> HANDOFF
       |    -> replacement takeover
       |    -> READY(fresh generation/fence)
       |    -> RUNNING
       |
       -> DONE / ERROR / BLOCKED / CANCELLED
```

`DELIVERED` may exist as transport evidence, but it is not a semantic scheduling state.

## Dispatch protocol

Every semantic dispatch is identified by:

```text
task_id
dispatch_id
dispatch_generation
fence_token
backend_cl_ref
lane_id
```

Rules:

1. Harness writes the canonical backend CL dispatch before emitting a wake.
2. One wake is emitted for that dispatch.
3. Bridge/extension delivery is only transport ACK.
4. Worker verifies canonical task/dispatch/generation/fence before doing semantic work.
5. The browser runtime waits until the target assistant response has actually started, then mechanically advances that exact dispatch to `RUNNING` with `ack_source=extension_response_start`. This is the durable execution-admission ACK.
6. The wake is consumed at that boundary, closing the retry window. The Worker does **not** burn a tool call mutating its own TCB and continues the same semantic response.
7. A duplicate/late wake for an already RUNNING/WAIT/HANDOFF/terminal dispatch is consumed at the control layer and never injected into Worker chat.
8. A retry is allowed only before response-start admission and only while the same dispatch remains unsuperseded.
9. Recovery after an expired/failed dispatch uses a new dispatch generation/fence token rather than repeated semantic spam.

This is analogous to dequeue/dispatch followed by an execution-context acceptance acknowledgement.

## Interrupt coalescing

CAH should prefer event coalescing over repeated wake messages.

If multiple completion events arrive while a Worker is busy or blocked, Harness should persist all durable result refs and schedule one continuation dispatch that names the relevant completion set. Do not inject one chat message per low-level event.

A wake is a doorbell for a state transition, not a stream of progress packets.

## Blocking and completion events

A Worker that is waiting on deterministic execution must publish:

```text
state = WAIT_RESULT
wait_ref = expected action/result/event identity
checkpoint_ref = current semantic checkpoint
```

Then the Worker may stop consuming reasoning capacity.

The runner/executor completion path:

1. commits the result/evidence;
2. verifies action/result identity;
3. atomically advances the backend CL from WAIT_RESULT to READY/PENDING for the next continuation;
4. allocates a new dispatch generation/fence;
5. emits one wake.

This is closer to I/O completion / event-driven rescheduling than polling a blocked thread.

## Recoverable host-resource waits

A missing host capability that can be supplied or configured by the user is not a terminal computation failure.

Use:

```text
RUNNING / WAIT_RESULT
 -> WAIT_RESOURCE
    wait_ref = host-capability:<capability_id>
    resource_wait.kind = NEED_HOST_CONFIG
    resource_wait.prompt = human-readable request
 -> capability AVAILABLE
 -> fresh generation/fence
 -> READY
```

Rules:

1. host capability resolution is deterministic and belongs to the shared host-capability runtime;
2. absolute host paths stay in the host-local cache, not canonical Git;
3. `NEED_HOST_CONFIG` keeps the logical task/checkpoint alive and releases the semantic Worker;
4. resource resolution resumes with a new dispatch generation/fence rather than reusing an accepted dispatch;
5. foreground may ask the user for the missing configuration using `resource_wait.prompt`;
6. terminal `ERROR` is reserved for resolver/executor faults, invalid durable evidence, or other non-config failures.

Detailed contract: `docs/HOST_CAPABILITIES.md`.

## Context switch / compaction rollover

Positive model-visible `context_compacted` is a preemption/context-switch boundary.

The outgoing Worker must serialize a durable handoff packet containing:

- work checkpoint / current frontier;
- semantic memory capsule;
- current action/round/segment;
- in-flight executor/result/wake identities;
- verified conclusions and decisions;
- failed paths that must not be repeated;
- artifact/evidence refs;
- exact next action and acceptance gate.

Then:

```text
RUNNING
 -> HANDOFF
 -> persist handoff packet
 -> publish handoff_packet_ref
 -> worker_rollover_request(context_compacted)
 -> stop substantive work
 -> replacement loads packet
 -> validates task/generation/fence
 -> takeover ACK
 -> old semantic dispatch is fenced
 -> READY(fresh generation/fence)
 -> one successor wake
 -> RUNNING
```

The replacement does not reconstruct continuation from the predecessor chat.

## Semantic liveness and orphan recovery

`RUNNING` means an admitted semantic response owns the dispatch; it is not an indefinite status bit.

On response-start admission the runtime sets a bounded semantic lease. The browser content/runtime also observes the semantic response boundary. After a response ends, it first gives any visible `action_submit` syscall a chance to transition the dispatch to `WAIT_RESULT`. If the exact dispatch is still `RUNNING`, the Harness treats it as orphaned semantic ownership.

Recovery never reuses the accepted dispatch:

```text
RUNNING(gN, fenceN)
 -> response ended with no WAIT/HANDOFF/terminal transition
    or semantic lease expires while no response is running
 -> preserve recovered_from lineage
 -> READY(gN+1, fenceN+1)
 -> emit one continuation wake
```

A visible/running assistant response suppresses lease recovery even if a clock deadline is reached. Existing dispatches that predate lease population derive a compatibility deadline from `acked_at + semantic lease`.

A Worker handoff is the same ownership rule at a different boundary: takeover verification closes the predecessor rollover transaction, clears the stale rollover request, fences the predecessor dispatch, and creates a fresh successor dispatch. Conversation takeover alone is not semantic task ownership.

Before liveness creates a recovery generation for a `parallel_semantic_branch`, it must give the deterministic parallel-branch finalizer a chance to accept a matching terminal `branch_result.json`. A canonically accepted `DONE/BLOCKED/ERROR` branch is terminal and must not be redriven as semantic stall.

## Generation/fencing

Physical Worker slots are reusable; logical generations are not.

Any task/slot reuse must advance a generation and fence token. A stale Worker from an older generation may write logs/evidence, but its scheduler-state mutation must be rejected when its fence no longer matches current canonical state.

This is the main protection against late writes after rollover or replacement.

## 5+1 ring

The six-slot ring is a bounded Worker-generation retention structure, not a six-stage workflow.

It answers:

- which Worker generations are current/standby/releasable;
- when an old generation may be retired;
- which generation/fence is authoritative.

It does not replace the backend task scheduler state machine.

## CPU/GPU concepts worth borrowing

Useful:

- ready queues and blocked queues;
- dispatch/accept acknowledgement;
- epochs/fences;
- leases/watchdogs;
- interrupt/event-driven wakeup;
- completion queues;
- context save/restore;
- preemption at explicit safe points;
- coarse-grained work stealing;
- streams/events for independent work;
- barriers/reduction for dependent fan-in;
- occupancy thinking: do not pin a Worker while work is blocked.

Do not copy literally:

- instruction-level scheduling;
- hardware cache coherence;
- SMT register sharing;
- SIMT warp lockstep;
- cycle-level timing;
- micro-op reorder semantics.

## Stage 0 invariant

For the single-lane proof, correctness requires:

```text
one canonical READY continuation
-> one semantic dispatch
-> one Worker ACK
-> zero further semantic wakes while RUNNING
-> explicit WAIT_* when blocked
-> one completion-driven continuation dispatch
-> durable DONE/ERROR
```

If the system needs repeated probe messages to learn whether a Worker is alive or accepted work, the scheduler contract is incomplete.

## Deterministic preludes

Known stable deterministic work should not consume an AI planning turn merely to rediscover the tool invocation.

Example: a known Zhihu capture task may stage the Camoufox action directly. The executor runs first, persists/validates evidence, and then seeds semantic dispatch generation 1 for the Worker to interpret the result. This keeps the AI on the semantic critical path while deterministic I/O stays deterministic.

```text
user task
 -> deterministic action
 -> executor + durable evidence
 -> semantic dispatch generation 1
 -> Worker ACK-and-run
 -> final analysis / terminal
```

## Semantic terminal artifact

For a terminal semantic reduction, the Worker should write one task-declared durable analysis artifact. It should not separately mutate backend CL, foreground CL, and global state.

A deterministic finalizer validates the artifact against the current dispatch identity/fence and durable source evidence, then closes all scheduler/control projections in one commit. This removes multi-file bookkeeping from the semantic critical path.
