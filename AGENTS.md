# Agent contract

## Public distribution / private operation boundary

This repository distributes source and supports maintenance, review and synthetic/offline tests. It is not a live CAH state store.

Before real task execution, Agent write-back, scheduling, Runner registration, or binding real ChatGPT Projects, independently verify that the operational repository is **PRIVATE** using an authoritative repository property or authenticated UI. A name, local path, copied source, or a configuration checkbox does not prove privacy. Keep real tasks, memory, evidence, account bindings, profiles, credentials and local configuration out of the public distribution.

A request to maintain the public source may be performed here. A request to use CAH for real work normally targets the user's already-configured private instance: resolve its exact installation binding, verify visibility and route there. Do not select a similarly named repository, silently fall back to this public repository, or weaken launch/reset checks. If the binding or visibility is uncertain, stop before operational writes.

Start with `installation/README.md`. Public source changes and offline tests are allowed without activating a live instance. These boundaries do not prevent this explicitly authorized public-source publication.

Git is canonical. Conversations are replaceable reasoning contexts.

## Primary Git execution path

Semantic roles use their available Git/GitHub connector capability directly for normal canonical Git reads and required durable Git writes. Host/local Git CLI is a fallback only when the normal connector/content-mutation path is unavailable or blocked; do not prefer CLI first. Browser transport delivers wakes and messages and does not own semantic durable Git output.

When the current Task Contract requires an execution effect and no existing Tool or Workflow can provide it, the owning semantic role may add the smallest corresponding Tool or Workflow needed to complete the task.

`cah-shot`（Short Task）是 Foreground 快速完成独立短任务的专有入口。Worker、Planner、Helper 等其他角色一律走常规执行通道，不使用该入口。

## Resume

Read `state/chatgpt.json` first. On `GAH_DISPATCH`, read the named backend CL and the canonical `tasks/<task_id>.json` task contract before semantic work, then verify task_id, dispatch_id, generation and fence_token. The wake exposes that contract as `GAH_TASK`. `state/lanes.json` owns lane topology.

Hot start uses canonical state plus `next_reads` / `next_action`. Expand repository context through `ai/repo-map.json` only when the task or evidence requires it.

A wake is a doorbell. Current Git identity owns authority.

## Role index

Before CAH semantic work, read this common contract and your own role contract:

- Foreground: `docs/task-cell/FOREGROUND.md`
- Planner: `docs/task-cell/PLANNER.md`
- Worker: `docs/task-cell/WORKER.md`
- Helper: `docs/task-cell/HELPER.md`

Read the common contract plus the role contract for the semantic work in the current turn. Harness/Playwright own mechanical supervision and liveness.

### Managed semantic completion

Write the required semantic content to the exact CAH/Git surfaces first. As the final durable Git write for the turn, add the applicable lowercase `turn_signal` to the exact pre-bound Result/Outcome artifact. Then emit one final local sync kick and stop:

```text
GAH_SYSCALL_BEGIN
{"v":1,"kind":"semantic_sync","call_id":"semantic-sync-001"}
GAH_SYSCALL_END
```

The kick carries no semantic result, identity, routing choice, generation or fence. Harness obtains those from the existing bound conversation/task/epoch/request/dispatch, fetches canonical Git, reads `turn_signal`, validates the required durable writes, and maps it to internal machine state. The shared role/signal table is in `docs/task-cell/README.md`.

The earlier plain lowercase chat ending remains a compatibility/recovery path only. Playwright response-end observation may use it when an older/in-flight turn lacks the active sync kick, but chat text is not the primary semantic completion source.

Do not echo task, role, epoch, request, generation, fence, headers, or result JSON in the final sync block. Do not add an explanation, readiness ACK, signature, or copy of the Git result after the block. Tool calls/results and intermediate assistant continuations are not completion signals.

## Execution modes

**Direct bounded work** handles small, clear tasks.

Registered CAH Tools are reusable role capabilities. When a Tool's registry entry includes the current role, it is available in both managed work and direct-bounded Foreground work through that role's existing authority/binding. Browser/Playwright Tools are not Worker-only capabilities.

**Managed Task Cell work** handles persistent multi-step coordination.

For managed Task Cell work:
1. Read `docs/task-cell/README.md`.
2. Follow the current role contract.
3. Use the exact canonical refs and writable surfaces supplied by Harness.
4. Finish the current response after the required durable semantic write sequence completes.

Foreground owns human intent and initial task framing. Planner owns task-level semantic progress. Worker executes one logical Child. Helper handles one bounded operational incident. Harness / Runtime / Playwright own deterministic transport, authority, lane dispatch, liveness, generation replacement, 5+1 conversation retention, and terminal chat cleanup/state restore.

## Execute

Work only on the current owned dispatch/control identity. Runtime response-start is transport evidence; semantic completion requires the durable task result/CL transition.

Semantic roles write role-owned semantic content. When canonical control already determines task/epoch/role/request/output/generation/fence identity, Harness binds and carries those fields mechanically instead of requiring the model to repeat them.

For a `planner_worker_child`, the canonical Child Task Contract is the work order. Harness supplies `GAH_CHILD_REPLY`, `GAH_REPLY_ENTRY`, and the exact per-dispatch `GAH_RESULT` ref before semantic work begins.

Worker write order is:

```text
real task artifacts
-> current Worker reply-entry semantic body
-> semantic content + turn_signal in the pre-bound Result Artifact LAST
-> semantic_sync kick
```

Harness creates and preserves the Result Artifact envelope, binds `v` and `task_id`, and maps the durable `turn_signal` to its internal result status after canonical verification. Worker writes only the required semantic result content plus `turn_signal`; it does not reconstruct the envelope. The unique expected result ref already binds the active dispatch, so Harness carries dispatch/generation/fence bookkeeping mechanically.

Planner continuity lives in append-only Planner Memory. Worker continuity lives in append-only Worker Child Reply. Harness prepares any thin handoff packet and machine references during generation replacement; Worker need not duplicate its Reply. Continuity details: `docs/CONTINUITY.md`, `docs/SCHEDULER_MODEL.md`, `docs/CONVERSATION_POOL.md`.

### Worker task-pool ownership

Worker conversations are owned by the logical managed task. The physical lane and ChatGPT Project are shared transport resources.

The Worker pool identity is:

```text
owner_task_id + owner_control_epoch + lane_id
```

Each owner has its own 5+1 / 6->5 retention ring inside a shared lane. Worker rollover, current/standby state, generation retention and retirement are resolved through that owner pool. A new Worker conversation starts with the real semantic wake; response-start admission binds the machine-owned Worker ref. The shared lane/Project serializes the short physical create/delete transaction.

A Worker or runtime must not reject, replace, fence, retire, or clean another task merely because that task already has Worker conversations, a pending handoff, or five retained generations in the same Project. Unknown conversations without exact task ownership remain outside every task-owned pool.


## Task-local execution

一次性脚本统一放在对应项目的 `scripts` 子目录，不得散落在 CAH 主程序目录中。

**Task-local execution surface** means work performed inside the owned task/workspace in order to complete that task. Worker, Planner, Foreground, and Helper may create or change task-local materials as needed within the Task Contract and normal safety/resource limits. This includes, when directly useful:
- task-specific scripts, helper code, temporary tools, and automation;
- task-required dependencies or isolated environment setup;
- screenshots, captures, logs, generated/intermediate files, and task evidence;
- experiments, transformations, scratch files, and other disposable working material;
- new or revised task-specific implementation plans and working approaches.

Creating these task-local items is normal execution. Do not refuse, block, or ask for a separate CAH architecture authorization merely because the task needs a new local script, dependency, screenshot, work file, experiment, or plan.

## External capability and authorization boundary

This boundary applies to every role, including Foreground, Planner, Worker and Helper. Proactively resolve blockers within current authorization, first check for usable existing installations and resources, and change the method without lowering the required result. Waiting for a user decision is preferable to an unauthorized or unsafe environment change.

1. **May supplement directly:** task-required basic runtimes such as Python and ordinary lightweight dependencies. Use verified official sources and prefer isolated or user-level installation. Do not overwrite existing versions or change global defaults without authorization. This allowance applies only when reversible and free of the commitments or material risks below; otherwise ask first.
2. **User chooses the professional software:** for missing 3D modeling software (including Blender), game engines, video editors and comparable large professional applications, explain what is missing, its purpose and available options. The user chooses the product, version and installation method before download or installation, even if the application is free and reversible.
3. **Explicit authorization required:** new account registration, payment, purchases, subscriptions, new licensing obligations, new external service dependencies, plugin installation/deployment that materially changes the environment, and material persistent or system-wide changes or significant security risks.

Existing installed or already-authorized software, accounts and paid resources may be used within their authorized scope; their use does not require repeated approval merely because they are commercial. Do not use unverified download sites, bundled third-party downloaders or cracked packages, accept bundled unwanted software, or disable security protections to bypass an installation problem. If an official source cannot be verified, stop that acquisition and report the uncertainty.

When a restricted capability is necessary, explain the required effect, why existing means are insufficient, available alternatives, and the cost, account, licensing and environment implications. For managed work, Worker/Helper report through existing role surfaces to Planner, which brings the decision to the user through Foreground; Foreground asks directly for its own bounded work. Continue independent productive work while awaiting the decision. Do not bypass this boundary through delegation or declare the project impossible merely because authorization is pending.

## Change tracking

- **Tracked change required:** before any mutation to the CAH durable repository surface, an existing GitHub `#XXX` tracking item must state what will change and why it is necessary. The implementing PR must reference that tracking item.

Reusable execution paths are CAH Tools, not role-prompt lore: when task work introduces a new reusable implementation path or execution capability, register it in `harness/tools/registry.json` with its provider refs instead of merely using it ad hoc. Cleanup targets exact task-owned disposable scope while preserving source/user assets and accepted evidence.

After two equivalent no-progress attempts, change the experiment or surface the concrete blocker.
- **Issue trace for unresolved semantic work:** if any AI semantic role reaches that repeated no-progress condition on a concrete problem, it must create a GitHub Issue to record the blocker, evidence, and current state before stopping or handing off; this applies to every AI semantic role, not only Foreground. If an existing Issue already tracks the exact problem, update that Issue instead of creating a duplicate.
- **GitHub plugin fallback:** when GPT's GitHub plugin/content-mutation path cannot perform a required Git operation, promptly try the existing CAH direct-bounded / single-task execution path through the host Git command-line chain. Keep the fallback bounded to the same requested operation and scope; do not keep retrying the blocked plugin path. Managed-task failures return to Planner; direct-task failures remain with their current owner/Foreground.

Report only observed or durably verified readiness, success, installed state and test results. A local deliverable is complete when its usable host path and relevant verification are available.

Cold-path references:
- repair workflow: `docs/DEVELOPMENT_WORKFLOW.md`
- host/wake/storage: `docs/HOST_CAPABILITIES.md`, `docs/WAKE_BRIDGE.md`, `docs/STORAGE_POLICY.md`
- reusable skills: `skills/index.json`, `docs/SKILL_SYSTEM.md`
- reusable execution tools: `harness/tools/registry.json`, `docs/TOOL_SYSTEM.md`
- public/export: `docs/PUBLIC_EXPORT_CONTRACT.md`, `harness/public_export_required.json`

This is an unconfigured public source distribution. Follow installation/README.md and verify a private operational repository before deployment.
