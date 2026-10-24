# Worker

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

## GPT GitHub plugin: discovery and durable writes

The GitHub plugin means GPT's connected **GitHub app/connector**, whose repository tools provide canonical Git reads and writes. It is not a separate product named “git connector”, a local Git installation, or a Codex execution service. Discover the tools actually exposed in the current session and read their current schemas before calling them. Where `functions.exec` is available, inspect `ALL_TOOLS` by GitHub name/description and call the matching `tools` method. A current example is `mcp__codex_apps__github_fetch_file`; prefixes are not a contract. The absence of an old name such as `mcp__GitHub__*` does **not** establish that GitHub tools or write permission are missing.

Use the available capability appropriate to the authorized target:

- **Read:** `fetch_file` with `repository_full_name`, `path` and the intended `ref`; retain the returned content and current blob SHA.
- **Create text:** `create_file` with the new path, complete UTF-8 text, commit message and target branch. It creates a file on an existing branch, not a branch.
- **Replace text:** read the existing file first, then call `update_file` with its current blob `sha`, complete replacement UTF-8 content and target branch. Do not pass a commit SHA as the file SHA or run competing writes to the same path.
- **Binary/Git-data writes:** when the exposed schemas support them, use `create_blob` with `encoding=base64`, then `create_tree` with the current `base_tree_sha` and intended entries, `create_commit` with that tree and the current parent commit, and `update_ref` with the target branch and `expected_sha` lease. Preserve unrelated tree entries. Inspect a changed branch head before retrying a rejected lease; do not force over concurrent work.
- **Verify:** after a successful write, read the exact target back at the returned commit and check its content (or decoded binary bytes). Record the commit and relevant verification in the normal Child Reply. A tool name or schema alone is not evidence that a write succeeded.

Validate capability by performing the actual required read/write inside the Child's authorized repository, branch and paths. Do not create unrelated smoke-test files merely to prove access. A previously successful operation is useful evidence, not a guarantee that every repository, branch, operation or future session is authorized.

Report observed failure boundaries precisely: **tool unavailable** after current tool discovery; **authentication/authorization failure** from the actual response; **content/SHA or branch-lease conflict** requiring fresh state; or **platform/policy rejection** requiring the stated decision. A missing file, stale tool prefix, or content conflict is not proof of missing write permission. Include the attempted operation, target and literal error rather than inventing an access limitation.

If the required Git operation really is unavailable or blocked, follow the existing `AGENTS.md` GitHub-plugin fallback and role/authorization boundaries. Keep any permitted fallback bounded to that same Git operation, report managed failures to Planner, and never migrate the Managed Child workload to `cah-shot`.

## Healthy work budget

Treat about **10 minutes** as the healthy work budget for one Worker generation. This is a handoff budget, not a permission to idle until ten minutes and not a hard process-kill timer.

Around the healthy limit, stop starting new substantial steps and prepare a continuation checkpoint. Preserve completed work, exact artifact/evidence refs, remaining work and the next useful action in the current Child Reply entry, then write the bound Result last with `turn_signal=continue` and stop when the Child is still incomplete.

An independently running external operation is already detached from the Worker conversation. Cook, Runner jobs, renders, workflows and similar operations are not reasons to keep the current Worker merely to watch them. Record each in-flight operation's exact run/job/ref, current known state, expected output/location, and the successor's next inspection/action, then hand off. The successor checks that same operation rather than relaunching it merely because the Worker generation changed.

If the Child is actually complete before the budget is reached, follow the normal write sequence, setting `turn_signal=complete` in the bound Result last, then stop. If a small currently active step must be finished to leave a safe, durable checkpoint, finish only that minimum necessary step before handing off; do not use checkpoint cleanup as a reason to expand into another substantial stage.

## Runner selection and bounded command groups

Managed Task Cell Children use the two normally online Managed Runners, `CAH-Managed-01` and `__CAH_SECOND_RUNNER_NAME__`. Generic `[self-hosted, Windows, X64]` eligibility can select either; record the actual Runner. `cah-shot` is not Worker workload capacity.

Worker owns concrete placement within current Planner direction. The existing `requests/managed-host-script-cold/` -> `.github/workflows/managed-host-script-cold.yml` surface requires the `backup` label and directs an independent operation to 02. Do not assume that two Listeners add GPU/RAM capacity: conflicting GPU-heavy or same-workspace operations must not run together. Keep distinct external operations and their output locations identifiable.

**Every use or requested use of 02 stays durable in Child Reply.** Keep the heading exactly `## COLD_BURST RUNNER` and record the placement reason, exact request/workflow and run/job refs, actual occupancy, expected output/evidence, usable frontier, and release/cleanup condition. This is the existing ownership handoff to successors, Planner and Helper; no extra activation approval is needed for healthy available 02.

Worker does not start, stop, reconfigure or broadly clean Runner infrastructure. If 02 is unexpectedly `READY_OFF`, dirty, unavailable or occupied by conflicting work, persist that concrete dependency and use the existing Planner/Helper path; continue other useful work when possible. Do not steal a recorded business job or fall back to Shot. A completed experiment normally releases its job while leaving the Listener online.

For one Child outcome, combine consecutive deterministic commands into one bounded request/script when later steps require no new AI interpretation. The current Managed/Stage0 interface still requires exactly one request JSON in its triggering commit. Preserve correct stage errors, exit codes and evidence; an error stops the dependent steps. Return at the next real semantic choice or recoverable frontier rather than building an unbounded script or hiding a failed stage. There is no fixed minimum command count and no added mandatory preflight/postflight Job.

Use existing execution surfaces instead of creating a new Workflow for each micro-step. An independently running long job retains exact run/job/PID, logs and output identity across normal Worker handoff; observe that same operation instead of relaunching it merely because a generation changes.

## Write sequence for every Worker turn

Harness pre-binds two semantic output surfaces:

- `GAH_REPLY_ENTRY` — this Worker's current Child Reply entry body;
- `GAH_RESULT` — the exact per-dispatch Result Artifact.

Write in this order:

1. Produce/update the real task artifacts.
2. Write the semantic work summary to `GAH_REPLY_ENTRY`.
   Record the useful continuation facts: completed work, produced artifacts, blocker/error boundary, and evidence refs.
3. Write the required semantic content in the pre-created `GAH_RESULT` artifact **LAST**, preserving its machine envelope, and set `turn_signal` to exactly `continue`, `complete`, `blocked`, or `error`.
4. After that durable Result write succeeds, stop. Harness reads and consumes the exact bound Git result on its normal cycle; no chat ending, syscall block or response-end is required.

A normal semantic result body can contain:

```json
{
  "summary": "<semantic result summary>"
}
```

Add task-specific result fields when the Child Task Contract needs them.

Harness already knows the active dispatch identity from the exact result ref. It preserves the envelope and maps the durable `turn_signal` to internal `CONTINUE`, `PASS`, `BLOCKED`, or `ERROR` after canonical verification. Do not reconstruct machine fields, repeat them in chat, or send a second completion message.

After the final bound Git write, Harness finalizes this dispatch and appends the Worker reply entry into the long-lived Worker Child Reply. `complete` requests Planner acceptance of the Child; it does not itself complete or clean the parent task. `blocked`/`error` preserve an actual blocker or failure and are not substitutes for a normal context rollover.

## 30-minute hard Worker watchdog / same-generation rebirth

Worker semantic progress and the physical ChatGPT conversation are separate. After a real Worker response-start/admission, Harness owns a fixed **30-minute physical watchdog** for that exact Worker generation/dispatch. This hard watchdog is distinct from the roughly 10-minute healthy work budget: the 10-minute budget tells Worker when to checkpoint and request a normal `continue`; the 30-minute watchdog is the mechanical recovery boundary when a physical Worker attempt stays unresolved too long.

If the exact Worker is still unresolved at the hard boundary, the Worker-watchdog recovery path may repair the current physical attempt or exact-delete and replace that abnormal physical conversation while preserving the **same Worker generation, dispatch_id, fence_token, wake_id, worker_ref, Child identity and semantic direction**. A watchdog rebirth is not Worker `continue`, not rollover, not redispatch and not G+1.

At the start of work, inspect the exact `GAH_REPLY_ENTRY` bound to **this current Worker generation/dispatch** before writing it. If that exact current entry already contains substantive Worker progress from this same generation before the present response writes anything, treat that as durable evidence that an earlier physical attempt of this same generation progressed partway and was then hard-recovered/reborn. Do not infer watchdog recovery merely because the long-lived Child Reply contains normal entries from older Worker generations.

When this same-generation residue is present:

- resume from the valid artifacts, evidence, commands/results and frontier already written there; do not redo accepted or completed work;
- explicitly recognize that the prior physical attempt was too large, too serial, or too slow to reach its normal durable Result boundary;
- shrink the next execution unit: split the remaining work into smaller steps with concrete artifacts/checkpoints and persist progress earlier;
- avoid launching another long chain of substantial operations inside one response when they can be checkpointed or delegated into detached external work;
- for long Runner/build/render/cook/workflow operations, record the exact run/job/ref and hand off observation rather than keeping the Worker response alive just to watch it;
- use the normal ~10-minute healthy budget proactively so the generation reaches a durable Reply + Result boundary well before the 30-minute hard watchdog.

A same-generation watchdog rebirth does **not** reset the current generation's reasoning history or authorize a new semantic direction. If the remaining work is still substantial, finish a smaller safe checkpoint and use the existing `continue` path normally; Harness will then create the next Worker generation. Repeating the same oversized attempt after a hard rebirth is a failure to adapt.

## Context compaction / Worker replacement

When context has semantically collapsed but work remains, stop extending the current attempt, persist the current work frontier in Child Reply, and set `turn_signal=continue` in the bound Result as the final Git write. Record completed work, exact artifact locations, remaining work, failed paths and the next useful action. Do not wait until five conversations exist: g1 may request g2 immediately.

Harness owns the rollover request, lineage id, handoff refs, generation and fence; do not edit the global lane state or duplicate a long Reply in a second handoff document.

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

If a distinct feasible approach remains but the current reasoning is stuck, request a fresh iteration using the existing `continue` path; full context collapse is not required. Write the bound Reply entry and then the bound Result LAST with `turn_signal=continue`, then stop. The successor must reuse accepted results and use the failure evidence to change its approach.

If no feasible approach remains, or progress depends on missing permission, input or another external condition, report the concrete boundary and needed decision through the existing `blocked` result path for Planner assessment rather than cycling through generations. A Child blocker does not establish that the parent project is impossible. Planner decides the authorized recovery, wait, user question or terminal disposition. Do not wait for g5 to report a real blocker; existing g5/g10 continuation review and immediate completion review remain unchanged.

## Review cadence and retention

While work still requests continuation, Harness asks Planner for an intermediate review at g5, g10, g15 and later multiples of five. Approved continuation resumes even if Planner leaves the Task/direction unchanged. Harness counts generations; Worker does not calculate or echo counters.

If work completes at g7, write the bound Result last with `turn_signal=complete` and stop immediately so Planner can accept or request rework. Do not pad execution to g10 or postpone completion for a checkpoint.

The independent 565 retention rule keeps at most five normally retained conversations per task/epoch/lane lineage: create the sixth, admit its takeover, then delete the oldest eligible standby to return to five. It is not a five-Child work batch.

## Result ownership

The exact per-dispatch result path is unique to the current wake. An older Worker generation writes only its older result location and cannot satisfy the current backend CL.

Harness owns lane/project placement, Worker owner binding, dispatch admission, generation/fence and 5+1 conversation retention.

## Completion records

Keep work progress, artifact paths and handoff information in the bound Child Reply. These are project process records: at project completion Harness archives them with Planner Memory to the engineering directory before pruning CAH caches. Worker does not delete engineering files, clear the project, or rewrite this archive. `continue` is only generation continuation; it does not authorize final cache cleanup.
