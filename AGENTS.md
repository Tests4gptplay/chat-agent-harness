# Agent contract

## Highest-priority public-distribution safety rule

**Do not run CAH as an operational Agent system in this public repository or in any other public repository.** Before enabling task execution, canonical runtime state, Agent write-back, Worker scheduling, local-runner binding, or user workloads, deploy/configure CAH in a repository whose visibility is verified as **PRIVATE**.

This is a public-distribution bootstrap guard, not a permanent runtime instruction. After the installed operational repository has been verified PRIVATE, this section may be deleted from that private copy of `AGENTS.md`; the remaining Agent contract continues to apply.

CAH is a thin Git-mediated control plane. Git is canonical; conversations are replaceable reasoning Workers.

## Start and act

Read `state/chatgpt.json` first. A named `GAH_DISPATCH` reads its own backend CL and task, validates the task/dispatch/generation/fence, then does useful semantic work in the same turn. The runtime owns response-start admission: do not add a standalone ACK turn. A bootstrap-only wake checkpoints its exact lane takeover and stops until the task wake arrives.

The installed repository is `example-owner/cah-private` after running `tools/configure_install.py` with the owner's repository. Canonical topology is `state/lanes.json`. Do not search other repositories or infer a lane from a display name. Handle explicit pending control/topology requests before ordinary work.

## Shortest sufficient route

Answer directly when execution and durable continuation are unnecessary. Otherwise reuse one compatible Worker unless independent useful work warrants parallel branches. Task Cell handles interpretation, planning, re-planning and exceptions; Helper is consulted when useful, not for mandatory routine approval. See `docs/NORMAL_TASK_PATH.md`.

Load only named inputs and the smallest relevant read set from `ai/repo-map.json`. Do not load showcase history, every Skill, or all subsystem docs at startup. Checkpoint meaningful progress or a changed plan; do not write back every observation.

## Execution and continuity

Publish durable outputs with the current dispatch identity. The deterministic finalizer validates them before closing the task. Preserve task/dispatch/fence boundaries, exact-target deletion and no blind duplicate execution. Waiting work records its checkpoint and wait ref instead of continuously polling with model turns. Positive context-compaction evidence requires a durable work checkpoint and memory capsule before rollover.

Task Cell is outside the Worker pool and is not disposable at a Worker rollover. Manage only explicitly registered Worker conversations, never underlying Projects or manual chats. Follow `docs/CONVERSATION_POOL.md`, `docs/LIFECYCLE_MAINTENANCE.md`, and `docs/SCHEDULER_MODEL.md` when those operations are needed.

## Skills and tools

Skill accumulation/reuse is a core Agent capability. Before non-trivial matching work, consult `skills/index.json` and load only relevant Skill records. After a successful task, distill a stable reusable procedure into a CANDIDATE with evidence; deterministic validation controls ACTIVE promotion. Record PASS and non-PASS reuse outcomes. Keep mechanical operations in executors, not bloated model instructions. See `docs/SKILL_SYSTEM.md` and `docs/CODEX_SKILL_IMPORT.md`.

Use the shared host capability provider rather than rediscovering tool paths per task. Missing configurable tools are recoverable resource waits. Absolute machine paths remain host-local.

## Maintenance

Keep source and tests clear. Fix demonstrated problems; do not invent a new global gate for a hypothetical edge case. Preserve failure evidence when recovery is part of a claim. Report only observed success, distinguishing code tests, host readback and actual workload outcomes. Include a usable artifact location when delivering work.

Before a public export, check `harness/public_export_required.json` and `docs/PUBLIC_EXPORT_CONTRACT.md`. Preserve core mechanisms and a safe example Skill; strip private state and content. Never publish credentials, raw private logs or another installation's bindings.
