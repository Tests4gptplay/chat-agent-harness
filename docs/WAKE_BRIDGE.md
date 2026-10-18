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

## Bridge process supervision

`host/bridge_supervisor.ps1` keeps the same Bridge process configuration and disk-backed wake queue across restarts. Process exit retains the existing two-second restart delay. While the owned child is alive, the supervisor checks `GET /health/live` at most once per ten seconds, with a two-second HTTP timeout. Three consecutive failures cause only that child process to be stopped; a successful response resets the count. A failed stop never starts a second Bridge beside the live child.

`/health/live` returns a constant response without queue/Git locks, directory scans, expired-claim recovery or access-log writes. The existing `/health` status API is unchanged and is not used for periodic supervision. This detects loss of HTTP service responsiveness, not slow jobs, queue backlog, a slow Git operation or every possible stuck handler. Business tasks are never gated on a probe. Inbox/claimed/consumed files, Host/browser state and Runner jobs are not cleared or restarted. An interrupted in-flight RPC still relies on its existing transaction reconciliation; process restart does not certify that transaction as complete.

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

The wake/prompt names those surfaces and relevant new event refs. Planner writes the required semantic content and durable `turn_signal` to CAH/Git, then stops. Harness detects that exact Git write in its normal cycle. Harness fetches canonical Git and derives the internal outcome and dispatch bookkeeping.

Planner replacement starts with the real task/continuity prompt, including the Foreground requirement location, and reads the same project requirements as well as Task/Plan/Memory and pending work. Memory cannot replace the overall user goal. There is no transport-only bootstrap message or model-authored binding ACK.

## Helper routing

Harness creates a fresh conversation for every Helper incident and sends the real bounded diagnosis request as its first message. Runtime/watchdog can request this directly without Planner approval.

Planner-requested Helper and Worker-watchdog Helper are separate transport states. Planner-requested Helper work uses `task_cell_role_prompt` / `WAIT_HELPER` / `helper_result`. The Playwright Worker watchdog uses `task_cell_worker_helper_prompt` / `WAIT_WORKER_HELPER` / `worker_helper_result`, with `semantic_output_kind=WORKER_HELPER_RESULT` and `worker-helper-*` request ids. Planner physical watchdog recovery does not create a Helper transport state: Harness retains the exact Planner wake locally, validates the canonical turn at expiry, replays it in the same physical conversation when possible, and only replaces that physical conversation if replay fails.

Managed Planner turns have no semantic-output timeout Helper path. Each Planner response-start instead arms a fixed 30-minute physical watchdog. A Planner watchdog replacement replays the original retained Planner turn at the same Planner generation/fence and changes only the physical conversation binding; it is not Planner handoff/rotation. If one mechanical recovery attempt fails, Harness records the failure and starts another 30-minute watchdog cycle for the same unresolved turn rather than disabling liveness protection.

For managed Workers, successful Playwright wake + response-start/admission arms a fixed 30-minute Worker watchdog on the physical Worker record. Lease renewal does not reset it. When the deadline expires and the exact dispatch is still RUNNING, Playwright calls `worker_watchdog_expired` directly from the retained Worker binding before DOM turn parsing, so DOM-unknown cannot suppress Worker Helper escalation.

Helper result routing and Helper exit are separate phases. First, Helper writes a complete durable `diagnosis + repair_result` **without** `turn_signal`; Harness may route that result while Helper remains alive. A Helper requested by Planner normally returns as a canonical `helper_result` Planner event. A Worker-watchdog Helper is marked as `worker_helper_result` on its distinct transport/result path; it first attempts same-Worker operational repair, and if the abnormal physical Worker must be replaced it records a WORKER_HELPER INCIDENT in the Worker Child Reply and restores the original same-generation wake/binding rather than fabricating G+1. If that Worker Helper must return to Planner, Harness reuses the existing canonical `helper_result` Planner event and preserves `helper_source=worker_watchdog` in its source identity rather than creating a separate Planner event kind. After the correct next role has actually response-started, Helper updates the same bound result with `turn_signal=done` and stops; Harness detects this final Git write to finish the single-use lifecycle.

The next-role wake is verified through Harness-generated transport markers, not model-authored text: Planner receives `GAH_WAKE v=1 id=<planner-request-id> project=<task-cell-project-id>`; a managed Worker receives `GAH_WAKE v=1 id=<wake-id> project=<worker-project-id>` plus its matching `GAH_DISPATCH ...` envelope. Response-start confirms transport. The Helper's final Git value is an exit request, not a second instruction to wake the next role.

Helper must restore and observe the correct next semantic role before writing `turn_signal=done`. That durable Git value is the Helper's exit request, not a chat ending. On accepted Helper Git output, Harness validates the exact result/binding and immediately exact-deletes that single-use Helper conversation; there is no additional Helper response-end gate. After physical deletion succeeds, Harness removes the exact canonical `roles.helper` binding and then clears the local Playwright Helper binding. If the canonical removal transiently fails, the local binding is retained as a cleanup receipt and retried on later ticks. A later incident does not reuse the finished binding. Planner `done` retains the Planner conversation; the same durable signal does not imply the same lifecycle action.

## Foreground delivery

Planner uses the existing Foreground delivery channel for:

- final managed-task delivery;
- `NEED_USER` questions.

A user-query delivery asks Foreground to present the Planner question. The user's reply is committed into the existing Task Contract; Harness detects that Task Contract write and emits `foreground_intent` for Planner.

## Delivery semantics

Transport state and semantic completion are distinct.

Worker response-start admits a dispatch. Planner/Helper response-start confirms transport only. Semantic completion is driven by the final `turn_signal` in the exact bound Git output. The existing `planner_runtime_tick` reads current canonical bindings and any local pending physical acknowledgements, shares a fresh Git snapshot across idle outputs, invokes the existing exact-identity reducers for ready outputs, and runs the normal next-role scheduler. It does not scan historical result directories or fetch Git separately for each idle role.

The Host applies the accepted local projections before delivering the next control/wake. Stop is physical cleanup of an already-consumed response, not evidence of semantic success. Stop failures enter the existing error Helper path and cannot roll back committed semantic work. Task-owned Helper phase-1 routing and final done acceptance are unchanged; the system-error Helper is read from its existing exact local binding and Git result without a synthetic Task Cell.

Chat syscall blocks, one-word endings, and response-end no longer trigger semantic completion. Explicit `semantic_turn_sync` recovery calls remain available and read Git; a caller's supplied chat word cannot manufacture a result. Browser observations still serve physical liveness, response-start/admission, navigation and actual Tool commands.

## Liveness

Playwright observes the exact conversation and physical response state. Normal Git reconciliation runs before physical watchdogs, so completed semantic work is consumed instead of replayed. With no final Git signal, the existing Worker Helper and Planner physical-watchdog paths remain responsible for stalled execution. There is no new timer, admission gate or AI Watchdog role.

## Immediate mechanical-error Helper

Error-level runtime events and failed Bridge operations go to Helper immediately. Host reports its own errors locally before telemetry delivery, so failure of the Bridge being diagnosed does not prevent the browser Helper from starting. Errors from other authorized Bridge callers use the small local `errors/pending.json` mailbox (`harness_errors`); acknowledgements only remove messages already saved in Host state. No canonical business control slot or new WAIT state is consumed.

The Host reuses its existing Helper creation, local role binding, durable Git result and exact conversation deletion. `harness_error_result` reads the direct-bounded error Helper result; it does not mutate Task Cells or waive their normal authority checks. Host observation/control/maintenance stages are isolated so a failed page observation cannot skip the existing watchdogs or error Helper. The normal loop also retries failed browser delivery/reconnect; no additional watchdog or delay is introduced. Explicit hot reset clears this disposable error inbox and Host incident state together with the existing local bindings, while retaining Git evidence and telemetry history.

## Worker recovery receipt ordering

Same-generation retry commits its existing `APPLYING` control before publishing the runnable wake. The Host re-enters an unfinished `APPLYING` control to repair publication or acknowledge an already-saved replacement, without creating another conversation. A late physical-start receipt is checked against that control's original identity, not the business dispatch's current RUNNING status; business completion or a subsequent generation does not invalidate the receipt or authorize rewriting its backend. Once a Worker is admitted, later retention cleanup errors preserve its current binding and are handled separately.
