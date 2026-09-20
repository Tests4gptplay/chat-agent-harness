# Normal-task path

Optimize for useful work and delivery, not the number of roles, checks or windows involved. This policy follows the user's post-CAH-migration priority.

## Choose the shortest sufficient route

| Work | Default route |
|---|---|
| An answer/edit that needs no local execution or durable continuation | Foreground answers directly. No artificial Worker task. |
| One bounded execution or artifact task | Compact contract -> one compatible Worker/executor -> one result -> delivery. Reuse the current Worker. |
| Clearly independent substantial work | Planner chooses useful parallel branches and a join. Do not require a numerical cost model when a simple judgment suffices. |
| Ambiguous result, failed assumption, resource conflict | Return the concrete evidence to Task Cell for diagnosis/replanning; do not build a new universal gate around every possible ambiguity. |

Foreground confirms intent once. Planner does not reinterpret an already confirmed contract. Planner coordinates; Watchdog reports actual symptoms; Helper is consulted only when useful. These are responsibilities, not a requirement to run three model conversations for each task.

A permitted fallback model should continue the role's work; do not stall merely to obtain the preferred model. Unknown binding is reported honestly. This policy does not claim the unfinished model-selection branch is deployed.

## Keep the usual path cheap

Read the dispatch/task and needed inputs, not the full project history. Checkpoint meaningful progress, not every observation. Keep historical narrative behind immutable references; do not append it endlessly to hot state. Deliver terminal output without mandatory extra commentary or a second AI approval unless the task explicitly needs it.

Use existing receipt identities to skip completed request payloads. Batch immutable Git object reads; do not start a Git process for every small file. Normal wake polling must not wait for unrelated admission recovery. Reuse a completed admission preflight, while the actual state-changing server operation still validates the current dispatch/owner.

The extension's cheap mailbox alarm defaults to 30 seconds; heavier lifecycle/control maintenance retains its one-minute default. Startup preserves existing alarm deadlines and polls immediately. Chrome can delay alarms, so this is not a 30-second end-to-end guarantee. The backend still has Actions/Git/network and model latency.

Keep the few deterministic boundaries that prevent a wrong task, duplicate side effect or deletion of the wrong resource. LLM supervision handles interpretation; it is not asked to undo corrupted task identity or deleted files.

## Measure separately

Track input/context bytes, request-discovery Git process count, dispatch delivery, useful response start and result delivery. Report source tests, local timings and real end-to-end observations separately.

`tools/benchmark_normal_path.py` compares one pending request behind completed history in a temporary Git repository. It deliberately excludes remote fetch, Actions scheduling, model inference and execution. A large speedup there must not be advertised as the same speedup for an entire task.

This is a default operating policy, not a new lifecycle subsystem. #50's unmerged scheduling/model changes remain separate; #69 addresses the first measured normal-path costs.
