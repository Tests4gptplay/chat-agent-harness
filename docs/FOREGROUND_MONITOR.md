# Foreground Supervisor / CL contract

Status: approved target design for Stage 0 migration. The current extension foreground notices are already live-proven, but they are no longer the preferred normal-path control model.

## Goal

For a delegated long-running task, the human-facing ChatGPT conversation acts as an **active foreground Supervisor**. It creates and monitors a durable Git condition ledger (CL) while managed Workers/runners perform background work.

The foreground turn is allowed to remain occupied by supervision. If the user wants unrelated conversation while a long task is running, they can use another non-supervising chat.

Git remains authoritative. The foreground monitors Git directly; browser/plugin messages are not task truth.

```text
Human -> Foreground Supervisor
          |
          +-> create task + CL in Git
          +-> dispatch background work
          |
          +-> monitor CL while task runs
          |      [GREEN] report meaningful progress
          |      [ERROR/BLOCKED] report fault + decide/retry/escalate
          |      [WAIT/RUNNING] continue monitoring
          |
          +-> user may interrupt with a status question
          |      -> answer from current CL/evidence
          |      -> resume monitoring
          |
          +-> completion predicate satisfied
                 -> read durable result/evidence
                 -> report final result
```

## CL type and scope

The foreground CL uses the same CL/control-light semantics as backend scheduler CLs described in the distributed-agent memo. It is not a separate status system.

The difference is scope: a foreground supervision CL is owned by the Foreground Supervisor and is **not schedulable backend work**. It may observe/aggregate backend CLs, but it does not join backend partitions, READY queues, work stealing, lane ownership or resource arbitration.

## Foreground / Harness / backend boundary

The foreground Supervisor does not directly schedule or converse with backend lanes. Its normal responsibility is narrower:

```text
Foreground
  -> create/hold foreground_supervision CL
  -> read/write canonical Git state
  -> observe Harness-published task state/evidence
  -> report/accept/reject to the human

Harness
  <-> backend_execution CLs
  <-> Workers / runners / executors
```

Backend execution may maintain short-lived task-local memory/cache for coordination and performance. After verified useful outputs are promoted into canonical state/evidence and the task is accepted, that backend cache is disposable. The foreground working set is longer-lived and RAM-like, but Git remains the authoritative durable source.

## CL semantics

A CL is a small durable synchronization/control record in Git. The user-facing "lights" are only a projection of richer CL slot state.

For the current single-thread design, the six-light display should be interpreted as a **5+1 backend generation ring**, not six cumulative workflow stages.

The foreground Supervisor emits the initial task/supervision signal. Harness allocates backend slot 1 and wakes the current managed Worker. Each backend Worker generation owns one CL slot while it is current, publishes its status/evidence there, and requests/permits handoff when it reaches a durable boundary. Harness projects only the basic slot state back to the foreground CL.

Example:

```text
start:
slot1 RUNNING
[◐ ○ ○ ○ ○ ○]

worker 1 checkpoints / hands off:
slot1 DONE, slot2 RUNNING
[● ◐ ○ ○ ○ ○]

worker 2 checkpoints / hands off:
slot1 DONE, slot2 DONE, slot3 RUNNING
[● ● ◐ ○ ○ ○]

...

temporary 6th generation:
slot1 DONE, slot2 DONE, slot3 DONE, slot4 DONE, slot5 DONE, slot6 RUNNING
[● ● ● ● ● ◐]

slot6 proves durable takeover/completion:
slot6 DONE
oldest retained slot1 becomes RELEASED
[○ ● ● ● ● ●]
```

If the logical task still continues, generation 7 may then reuse physical slot 1 with a **new monotonic generation/fencing token**. A stale write from the old generation 1 must be rejected even though the physical slot number is the same.

The six slots are therefore capacity/lifecycle slots, not fixed semantic task stages. A logical user task may finish in slot 1, 2, or 3 and never fill the ring. Long tasks may wrap the ring many times.

### Slot record

A backend slot should be richer than a lamp. Minimal useful fields are:

```text
slot_id              # physical 1..6
generation           # monotonic logical generation
fence_token          # rejects stale writers after reuse
worker_ref            # managed conversation / Worker generation
task_id
segment_id
state                 # EMPTY/READY/RUNNING/WAIT/HANDOFF/DONE/ERROR/BLOCKED/RELEASED
started_at
updated_at
input_ref
checkpoint_ref
result_ref
evidence_refs[]
handoff_to
fault
```

The exact serialized shape can remain compact; only add fields that the runtime actually needs.

### Ownership and handoff

- Foreground does not assign slot 2/3/4 directly. It starts/controls the logical task.
- Harness owns slot allocation, successor selection, timeout/liveness decisions, projection, and release.
- The active backend Worker writes only its own current slot state/evidence.
- A successor slot is not authoritative until it has read canonical state and produced the required durable takeover/checkpoint acknowledgement.
- An old slot is released only after the successor boundary is proven durable.
- At the 5+1 boundary, the sixth slot is temporary capacity; successful generation-6 takeover permits release/reuse of the oldest retained slot.
- Errors, blockers and timeouts stay on the affected backend slot and are projected to the foreground CL; silence is never interpreted as success.

### Foreground projection

The foreground CL does not join the backend ring. It contains/reads a small Harness-produced projection such as:

```text
supervision = HELD
current_generation = 6
slots = [RELEASED, DONE, DONE, DONE, DONE, DONE]
overall = RUNNING | VERIFY | SUCCESS | ERROR | BLOCKED
final_result_ref = ...
```

The human-facing six lights may render that projection, but task truth remains the backend CL slots + durable evidence in Git.

## Foreground supervision loop

The normal path is an active monitoring turn, not plugin-driven completion notification.

The Supervisor:

1. creates/records the task and CL before dispatch;
2. dispatches background work through the existing Worker/runner path;
3. repeatedly reads the smallest canonical CL/evidence set;
4. emits concise user-visible progress only on meaningful transitions or when the user asks;
5. allows user interruption at tool-call/turn boundaries, then resumes monitoring;
6. finishes only after the CL completion predicate is satisfied and final evidence is read.

Do not busy-poll at high frequency. Choose a reasonable bounded cadence for the workload and use event/result boundaries when available.

## Backend dispatch acknowledgement / CL handshake

A backend wake has two different acknowledgement layers and they must not be conflated:

1. **transport ACK**: the localhost bridge/extension claimed and delivered a wake;
2. **semantic ACK**: the target Worker read canonical Git state, recognized the dispatch, and durably accepted responsibility for that task generation.

Transport delivery alone is not enough to stop semantic retries.

Each schedulable backend task/segment should therefore expose a small durable dispatch record inside its backend CL, for example:

```json
{
  "dispatch": {
    "dispatch_id": "dispatch-...",
    "wake_id": "wake-...",
    "generation": 7,
    "fence_token": "...",
    "state": "PENDING|DELIVERED|ACKED|RUNNING|WAIT_RESULT|DONE|ERROR",
    "requested_at": "...",
    "delivered_at": null,
    "acked_at": null,
    "acked_by_worker_ref": null,
    "lease_expires_at": null
  }
}
```

The exact schema may stay smaller, but the semantics are mandatory:

- Harness creates the dispatch in canonical Git before emitting the wake.
- Bridge/extension delivery may mark or report `DELIVERED`, but this is only transport evidence.
- On wake, **before substantive reasoning**, the Worker compares `dispatch_id + generation + fence_token` with canonical state.
- If it is the current unacknowledged dispatch, the Worker immediately writes a durable semantic ACK (`ACKED` or `RUNNING`) with its Worker identity and timestamp.
- Once that ACK is visible in Git, Harness stops retries for that dispatch and must not emit another user-visible wake for the same logical dispatch.
- A late duplicate wake for an already-ACKed dispatch is consumed/dropped as a no-op; it must not interrupt substantive work.
- If a Worker is waiting for executor output, the backend CL moves to `WAIT_RESULT`; the Worker may release active reasoning capacity. The executor/result event creates the next explicit dispatch/continuation boundary.
- If the ACK lease expires without durable progress, Harness may retry or replace the Worker using a **new dispatch generation/fence token**, never by blindly spamming the same chat.

This handshake is task-level control state and is separate from the six-slot 5+1 Worker-generation ring. The ring tracks replaceable Worker generations; the dispatch record tracks whether a specific logical task continuation has been accepted.

### Minimal Stage 0 interaction loop

```text
Harness writes backend CL dispatch=PENDING
        |
        v
emit one wake
        |
        v
bridge/extension delivers -> transport DELIVERED
        |
        v
Worker reads Git first
        |
        +-- stale/already ACKed dispatch -> drop duplicate wake, no interruption
        |
        +-- current dispatch -> immediately commit semantic ACK/RUNNING
                              |
                              v
                    Harness observes ACK
                    stops all retries
                              |
                              v
                    Worker does substantive reasoning
                              |
                     needs executor result?
                        /           \
                      yes           no
                      |              |
                 WAIT_RESULT      DONE/ERROR
                      |
                 executor result
                      |
              new continuation dispatch
                      |
                   wake once
```

The design goal is **interrupt-on-state-change**, not repeated probing. Retries exist only to recover an unacknowledged delivery boundary, and a durable Worker ACK closes that boundary.

## Error and timeout ownership

Errors use the same CL path as success and progress.

- If a Worker/executor has explicit failure evidence, it writes the affected condition as `ERROR` or `BLOCKED` with a fault boundary/evidence ref.
- If a Worker/executor stops reporting and violates a declared lease/deadline/watchdog condition, the **Harness** may transition the affected condition to `ERROR` with a timeout/liveness reason.
- A timeout is evidence of a liveness/coordination failure at that boundary; it must not be misreported as proof that the underlying domain computation itself was wrong.
- The Supervisor reads the resulting CL state and decides whether to retry, re-route, ask the user, or terminate.

Silence is never success.

## Plugin / browser role

Separate **foreground notification** from **backend Worker wake/lifecycle**.

The foreground notification function is **not required on the normal active-Supervisor path** once CL monitoring is implemented. However, the current Stage 0 runtime still requires the localhost bridge + browser extension to wake and manage the fixed Sandbox0 ChatGPT Worker until a future Host/browser controller replaces that transport.

The foreground-notification side of the extension is therefore recovery/watchdog only:

```text
normal:
Foreground Supervisor <-> Git CL <-> Workers/runners

recovery:
Supervisor/session unexpectedly stops
        -> watchdog/extension detects unfinished supervised task
        -> wake/reopen foreground
        -> foreground rereads Git CL
        -> resume supervision
```

Therefore:

- plugin messages must never be a source of task status;
- normal foreground progress/success/error reporting comes from CL state/evidence;
- no separate plugin-only foreground error channel should exist;
- backend Sandbox0 wake/rollover/lifecycle remains an active normal-path responsibility of the extension in Stage 0;
- removing foreground notices must never disable backend Worker interrupts.

The already-proven `GAH_FOREGROUND started/heartbeat/terminal` mechanism remains valid as a fallback/recovery transport during migration, but it should not grow additional business semantics.

## Worker rollover interaction

Worker lifecycle is independent from foreground task/CL identity.

A long task may span multiple Worker conversations:

```text
CL task remains active
Worker A -> compaction -> checkpoint + rollover
Worker B -> takeover -> continue same CL
Worker C -> final evidence -> required CL conditions GREEN
Foreground Supervisor -> verify result -> report completion
```

Worker rollover must not reset the task or its CL.

## Operational rule

Use active foreground supervision for substantial delegated work where the user expects progress visibility and eventual completion in the same supervising conversation.

Do not create CL machinery for trivial synchronous chat turns.
