# Localhost wake bridge

## Purpose

The localhost bridge and Playwright browser host deliver compact machine-owned wakes to exact ChatGPT conversations.

```text
durable Git state/event
-> localhost bridge
-> Playwright browser host
-> exact conversation
-> semantic role reads canonical refs
-> semantic role writes the requested durable surface
```

Git is canonical. Bridge queue and browser-host state are disposable transport.

## Wake marker

```text
GAH_WAKE v=1 id=<opaque-id> project=<project-id>
```

Wakes stay compact. Task payloads and large evidence stay in Git or the owning artifact store.

## Worker dispatch

A managed Worker wake carries the current execution envelope:

```text
GAH_DISPATCH
GAH_TASK
GAH_OWNER
GAH_CHILD_REPLY      # Planner Child when applicable
GAH_REPLY_ENTRY      # current Worker semantic entry
GAH_RESULT           # exact per-dispatch result target
GAH_HANDOFF          # replacement generation when applicable
```

When no current Worker conversation exists, Playwright creates one and sends this **real wake as the first message**.

Assistant response-start is the mechanical admission boundary. Harness marks the exact dispatch RUNNING and binds the current Worker owner in that same machine transaction.

Worker performs real semantic work in that response.

The real wake also identifies the role, CAH repository and required role/Task/Reply reads in normal task language. Its machine envelope is input supplied by Harness, not a response template for AI to repeat.

## Planner routing

Planner turns target the current authoritative Planner conversation.

Every actual initial, later, or successor Planner wake must include the exact readable Foreground task location, with the repository/ref/path or accessible URL needed to resolve it. The prompt tells Planner to read the project requirements there before using Plan and Memory to decide the next work. A task id, a Memory link, or a prose summary alone does not provide the original project requirements.

If the original submission and formal Task Contract are different, include both existing locations and their relation; if they are the same surface, reference it once. Harness binds these refs from the Foreground submission/current task rather than asking Planner to guess, search for, or repeat them. Preserve the exact requirement location through Planner replacement and subsequent Foreground updates.

Harness pre-creates:

- Planner Memory entry surface;
- lane-bound Worker work surfaces for all registered lanes;
- turn outcome surface.

The wake/prompt names those surfaces and relevant new event refs. Planner writes the required semantic content and durable `turn_signal` to CAH/Git, then emits the fixed `semantic_sync` syscall kick. Harness fetches canonical Git and derives the internal outcome and dispatch bookkeeping.

Planner replacement starts with the real task/continuity prompt, including the Foreground requirement location, and reads the same project requirements as well as Task/Plan/Memory and pending work. Memory cannot replace the overall user goal. There is no transport-only bootstrap message or model-authored binding ACK.

## Helper routing

Harness creates a fresh conversation for every Helper incident and sends the real bounded diagnosis request as its first message. Runtime/watchdog can request this directly without Planner approval.

Planner Helper and Worker Helper are separate transport states. Planner-requested Helper work uses `task_cell_role_prompt` / `WAIT_HELPER` / `helper_result`. The Playwright Worker watchdog uses `task_cell_worker_helper_prompt` / `WAIT_WORKER_HELPER` / `worker_helper_result`, with `semantic_output_kind=WORKER_HELPER_RESULT` and `worker-helper-*` request ids. Both still bind the same single-use `helper` semantic role, but the source and return semantics remain visible in canonical state.

For managed Workers, successful Playwright wake + response-start/admission arms a fixed 30-minute Worker watchdog on the physical Worker record. Lease renewal does not reset it. When the deadline expires and the exact dispatch is still RUNNING, Playwright calls `worker_watchdog_expired` directly from the retained Worker binding before DOM turn parsing, so DOM-unknown cannot suppress Worker Helper escalation.

Helper result routing and Helper exit are separate phases. First, Helper writes a complete durable `diagnosis + repair_result` **without** `turn_signal`; Harness may route that result while Helper remains alive. A Helper requested by Planner normally returns as a canonical `helper_result` Planner event. A Worker-watchdog Helper is marked as `worker_helper_result`; it first attempts same-Worker operational repair, and if the abnormal physical Worker must be replaced it records a HELPER INCIDENT and restores the original same-generation wake/binding rather than fabricating G+1. Only when new semantic strategy is required does the Worker Helper return that incident to the authoritative Planner. After the correct next role has actually response-started, Helper updates the same bound result with `turn_signal=done` and kicks `semantic_sync` solely to end its own single-use lifecycle.

The next-role wake is verified through Harness-generated transport markers, not model-authored text: Planner receives `GAH_WAKE v=1 id=<planner-request-id> project=<task-cell-project-id>`; a managed Worker receives `GAH_WAKE v=1 id=<wake-id> project=<worker-project-id>` plus its matching `GAH_DISPATCH ...` envelope. Response-start confirms transport. The Helper's own `semantic_sync` only requests immediate canonical reconciliation; it is not itself proof that the next role was awakened.

Helper must restore and observe the correct next semantic role before writing `turn_signal=done`. That durable Git value is the Helper's exit request, not a chat ending. On accepted Helper `semantic_sync`, Harness validates the exact result/binding and immediately exact-deletes that single-use Helper conversation; there is no additional Helper response-end gate. The Helper role binding is cleared only after exact deletion succeeds. A later incident does not reuse the finished binding. Planner `done` retains the Planner conversation; the same durable signal does not imply the same lifecycle action.

## Foreground delivery

Planner uses the existing Foreground delivery channel for:

- final managed-task delivery;
- `NEED_USER` questions.

A user-query delivery asks Foreground to present the Planner question. The user's reply is committed into the existing Task Contract; Harness detects that Task Contract write and emits `foreground_intent` for Planner.

## Delivery semantics

Transport state and semantic completion are distinct.

Worker response-start admits a dispatch. Planner/Helper response-start confirms transport only. Semantic completion is driven by the role's durable `turn_signal` in the exact bound Git output. The `semantic_sync` syscall merely asks Harness to fetch/reconcile immediately; role identity comes from the existing conversation/task/epoch/request binding, not text echoed by AI.

The active kick may be observed before the browser has painted its final response-end affordance. Harness may therefore reduce canonical semantic state immediately, while Playwright still owns the transport rule that a busy composer/conversation is not reused unsafely. Legacy ended-response word parsing remains recovery/backward compatibility only.

A kick or legacy ending without its required durable output remains unresolved, not successful. A Planner timeout/missing output is a runtime incident to diagnose, not evidence of semantic compaction and not an automatic `handoff`. Missing Helper output likewise must not authorize deleting its conversation as successfully handled.

## Liveness

Playwright observes:

- explicit `semantic_sync` syscall transport;
- response running/ended for transport safety and fallback recovery;
- exact conversation route;
- Worker dispatch liveness.

Harness reads expected semantic output presence and `turn_signal` from canonical Git after a sync kick or recovery reconciliation.

Harness/runtime reconciles those facts with canonical Git state and is itself the mechanical watchdog. Helper diagnoses operational ambiguity and precise recovery; Planner owns semantic task strategy. There is no separate AI Watchdog patrol role.
