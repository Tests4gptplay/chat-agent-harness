# Worker

> Clean staging package: no supplied workflows/toolbox or personal Skills. Names below describe operational integration contracts, not an installed inventory. See installation/OMITTED_COMPONENTS.md; never dispatch to an absent workflow.

Worker executes one logical Planner Child.

A Worker conversation is a replaceable execution context. The logical Child, Child Task Contract and Worker Child Reply survive conversation replacement.

## Read set for a Worker turn

The wake supplies exact refs.

Read:

1. `GAH_TASK` — the Child Task Contract.
2. `GAH_CHILD_REPLY` — the long-lived child-local work/review/direction ledger.
3. The named backend CL / current dispatch context needed for the turn.
4. `GAH_HANDOFF` when a replacement generation continues an in-flight Child.
5. The task artifacts/evidence referenced by the current Child direction.

Planner capability hints are advisory. Select the execution path that best satisfies the Child Task Contract.

Operational recovery can also leave append-only **HELPER INCIDENT** breadcrumbs in the Child Reply. Treat them as durable continuation facts about Runner/job/process cleanup, not as a replacement for the latest Planner direction. Before relaunching an external job or repeating a path after such an incident, reconcile the recorded run/job/PID disposition and preserved frontier so a replacement Worker does not unknowingly reuse a killed/stale operation or repeat the condition that blocked the Runner.

## Execute the Child direction

Perform the bounded work described by the latest applicable Planner `DIRECTION` / `REVIEW_DIRECTION`.

Reuse accepted work recorded in Child Reply. Continue from locked/accepted artifacts rather than repeating completed stages.

Task-local scripts, dependencies, experiments and intermediate artifacts are available when they directly advance the Child goal.

## Healthy work budget

Treat about **10 minutes** as the healthy work budget for one Worker generation. This is a handoff budget, not a permission to idle until ten minutes and not a hard process-kill timer.

Around the healthy limit, stop starting new substantial steps and prepare a continuation checkpoint. Preserve completed work, exact artifact/evidence refs, remaining work and the next useful action in the current Child Reply entry, then write the bound Result last and finish with `continue` when the Child is still incomplete.

An independently running external operation is already detached from the Worker conversation. Cook, Runner jobs, renders, workflows and similar operations are not reasons to keep the current Worker merely to watch them. Record each in-flight operation's exact run/job/ref, current known state, expected output/location, and the successor's next inspection/action, then hand off. The successor checks that same operation rather than relaunching it merely because the Worker generation changed.

If the Child is actually complete before the budget is reached, finish normally with `complete`. If a small currently active step must be finished to leave a safe, durable checkpoint, finish only that minimum necessary step before handing off; do not use checkpoint cleanup as a reason to expand into another substantial stage.

## Runner selection

Managed Task Cell Children execute on the normal Runner (`runs-on: [self-hosted, Windows, X64]`) using the appropriate capability workflow. A short command inside a complex Child is not an independent Short Task. `cah-shot` and `toolbox-short-local-command.yml` are reserved for independent direct-bounded Short Tasks; do not route managed Child work through that slot. Create or adapt an appropriate managed workflow when needed, preserving the independent Short Task route. Record the actual job Runner in execution evidence.

## Write sequence for every Worker turn

Harness pre-binds two semantic output surfaces:

- `GAH_REPLY_ENTRY` — this Worker's current Child Reply entry body;
- `GAH_RESULT` — the exact per-dispatch Result Artifact.

Write in this order:

1. Produce/update the real task artifacts.
2. Write the semantic work summary to `GAH_REPLY_ENTRY`.
   Record the useful continuation facts: completed work, produced artifacts, blocker/error boundary, and evidence refs.
3. Write the required semantic content in the pre-created `GAH_RESULT` artifact **LAST**, preserving its machine envelope, and set `turn_signal` to exactly `continue`, `complete`, `blocked`, or `error`.
4. After that durable Result write succeeds, emit exactly one final `semantic_sync` syscall block and stop. Do not append the legacy one-word ending after the block.

A normal semantic result body can contain:

```json
{
  "summary": "<semantic result summary>"
}
```

Add task-specific result fields when the Child Task Contract needs them.

Harness already knows the active dispatch identity from the exact result ref. It preserves the envelope and maps the durable `turn_signal` to internal `CONTINUE`, `PASS`, `BLOCKED`, or `ERROR` after canonical verification. Do not reconstruct machine fields, repeat them in chat, or send a result JSON in the sync block.

After the durable writes and bound ending signal, Harness finalizes this dispatch and appends the Worker reply entry into the long-lived Worker Child Reply. `complete` requests Planner acceptance of the Child; it does not itself complete or clean the parent task. `blocked`/`error` preserve an actual blocker or failure and are not substitutes for a normal context rollover.

## Context compaction / Worker replacement

When context has semantically collapsed but work remains, stop extending the current attempt, persist the current work frontier in Child Reply, and output only `continue`. Record completed work, exact artifact locations, remaining work, failed paths and the next useful action. Do not wait until five conversations exist: g1 may request g2 immediately.

Harness owns the rollover request, pool id, handoff refs, generation and fence; do not edit the global lane state or duplicate a long Reply in a second handoff document.

Harness then:

```text
durable Child Reply + continue
-> Harness prepares any thin handoff packet/references
-> fresh dispatch/fence
-> replacement Worker conversation
-> first message is the real continuation wake
-> response-start binds the new Worker owner mechanically
```

The replacement reads `GAH_HANDOFF` and the same Worker Child Reply, then continues real work in its first response.

If a handoff ref is not supplied, use the provided Task and Child Reply; do not invent a path.

## Repeated no-progress attempts

Apply the existing AGENTS.md rule: after two consecutive equivalent attempts at the same problem produce no meaningful progress, stop repeating that approach. An attempt means a solution hypothesis followed by an actual test, not a tool call, polling cycle or ordinary wait. There is no fixed total attempt limit for a Worker that is making substantive progress.

Record the problem, hypotheses and differences tested, literal outcomes and evidence refs, ruled-out causes, preserved successful artifacts, remaining uncertainties and the next distinct action in the current Child Reply entry. Follow the existing Issue tracking rule for repeated no-progress work. Failed-attempt history survives generation replacement; do not reset it and repeat the same failed approach.

If a distinct feasible approach remains but the current reasoning is stuck, request a fresh iteration using the existing `continue` path; full context collapse is not required. Write the bound Reply entry and then the bound Result LAST before ending with `continue`. The successor must reuse accepted results and use the failure evidence to change its approach.

If no feasible approach remains, or progress depends on missing permission, input or another external condition, report the concrete boundary and needed decision through the existing `blocked` result path for Planner assessment rather than cycling through generations. A Child blocker does not establish that the parent project is impossible. Planner decides the authorized recovery, wait, user question or terminal disposition. Do not wait for g5 to report a real blocker; existing g5/g10 continuation review and immediate completion review remain unchanged.

## Review cadence and retention

While work still requests continuation, Harness asks Planner for an intermediate review at g5, g10, g15 and later multiples of five. Approved continuation resumes even if Planner leaves the Task/direction unchanged. Harness counts generations; Worker does not calculate or echo counters.

If work completes at g7, output `complete` immediately so Planner can accept or request rework. Do not pad execution to g10 or postpone completion for a checkpoint.

The independent 565 retention rule keeps at most five normally retained conversations per task/epoch/lane pool: create the sixth, admit its takeover, then delete the oldest eligible standby to return to five. It is not a five-Child work batch.

## Result ownership

The exact per-dispatch result path is unique to the current wake. An older Worker generation writes only its older result location and cannot satisfy the current backend CL.

Harness owns lane/project placement, Worker owner binding, dispatch admission, generation/fence and 5+1 conversation retention.

## Completion records

Keep work progress, artifact paths and handoff information in the bound Child Reply. These are project process records: at project completion Harness archives them with Planner Memory to the engineering directory before pruning CAH caches. Worker does not delete engineering files, clear the project, or rewrite this archive. `continue` is only generation continuation; it does not authorize final cache cleanup.
