# Agent contract

## PUBLIC DISTRIBUTION BOOTSTRAP GATE — HIGHEST PRIORITY

> **This entire section exists only to protect users of the public CAH distribution.**
>
> **Do not operate CAH from a public repository.**
>
> Before CAH is allowed to execute real user tasks, accept Agent write-back, store canonical runtime state, bind local runners, bind real ChatGPT Projects, schedule Workers, persist private evidence, or perform unattended work, the operational repository **must be independently verified as PRIVATE**.
>
> While this section is present, it overrides every lower-priority instruction in this file that would otherwise cause CAH to begin normal operation.

### 1. Public distribution and operational repository are different things

The public CAH repository is a **source distribution, documentation surface, public CI target, showcase, and collaboration surface**.

It is **not** a safe canonical state store for a user's live CAH installation.

A live CAH operational repository may accumulate or reference information such as:

- task contracts and task summaries;
- Planner decisions and execution state;
- Worker/lane state and continuation checkpoints;
- result/evidence references;
- user-derived text or project facts;
- host capability projections;
- installation bindings;
- ChatGPT Project bindings;
- machine-local configuration references;
- failure diagnostics and operational logs;
- Skill candidates derived from real work;
- artifact metadata;
- private repository or project identifiers.

Even when CAH intentionally avoids storing full chat transcripts, the runtime state above can still reveal private work. Therefore a public repository must never be treated as the normal operational state plane.

### 2. Do not infer privacy from names, paths, remotes, or user intent

A repository is not considered private merely because:

- its name contains words such as `private`, `internal`, `local`, or `prod`;
- it exists only in a local checkout;
- the user says they intend to make it private later;
- the current Git remote looks unfamiliar;
- the repository was copied from a private source;
- no secrets are visible yet.

Before normal CAH operation begins, verify repository visibility from an authoritative GitHub repository property, UI, API response, or equivalent trusted source.

The required condition is:

```text
operational_repository.visibility == PRIVATE
```

If visibility cannot be verified, treat the repository as **not safe for operation**.

Do not guess. Do not continue "just for now." Do not write real runtime state first and plan to move it later.

### 3. Operational routing and ambiguity resolution

A user instruction such as:

- "use CAH";
- "run this through CAH";
- "let CAH do it";
- "send this to CAH";
- "continue with CAH";
- or equivalent wording

normally refers to the user's **configured operational CAH instance**, not to this public source distribution.

If the current Agent context happens to be reading or modifying the public CAH repository when such a request arrives, do **not** interpret the current repository merely as the execution target because it is already open.

Instead, resolve the operational target before doing any live CAH work.

Preferred resolution order:

1. use an explicit operational repository named by the user in the current request, if it is verified PRIVATE;
2. otherwise use the installed/private CAH repository recorded by authoritative installation configuration, canonical state, configured operational `origin`, or another exact installation binding;
3. verify that the resolved repository is PRIVATE;
4. switch subsequent CAH task/state/dispatch/evidence operations to that private operational repository;
5. treat the public CAH repository only as source/upstream unless the user explicitly asked to maintain the public distribution itself.

If exactly one configured private operational CAH repository can be resolved and verified, **route there automatically**. Do not make the user repeat the repository name merely because the Agent was initially looking at the public repository.

Conceptually:

```text
user: "use CAH for this"
        |
        v
current context happens to be public CAH repo
        |
        v
resolve configured operational CAH target
        |
        +-- exactly one verified PRIVATE target
        |        |
        |        v
        |   switch/reroute to private operational repo
        |        |
        |        v
        |   perform normal CAH flow there
        |
        +-- no target / ambiguous targets / privacy not verifiable
                 |
                 v
              STOP
                 |
                 v
       ask for the missing target/verification
```

Do not:

- create real task state in the public repository "temporarily";
- begin a dispatch in public and migrate it later;
- choose a repository by similar name alone;
- choose whichever CAH repository was most recently opened;
- infer the operational repository from a display label when an exact repository binding exists;
- silently fall back to the public distribution after a private-target lookup fails.

This rule also prevents a common maintenance ambiguity:

> **"Modify CAH's public source/docs" is a public-repository maintenance task.  
> "Use CAH to perform a task" is an operational-private-repository task.**

When the user's request contains both, keep the planes separate. Public source changes may be made in the public repository, while any live CAH execution, private task state, Worker coordination, or user-derived evidence must remain in the verified private operational repository.

### 4. Actions forbidden until PRIVATE is verified

Until the target operational repository is verified PRIVATE, do **not**:

- run real CAH user workloads;
- create or advance real canonical tasks;
- write real task/result/CL/continuation state;
- enable Agent write-back from live work;
- register a user's self-hosted runner against the public distribution;
- configure a real runner root for operational use;
- bind real ChatGPT Task Cell or Worker Project URLs;
- write real Project keys or conversation bindings;
- enable unattended Worker scheduling;
- enable host self-update against the public repository as the live `origin`;
- publish real private evidence, logs, screenshots, paths, or artifact metadata;
- import private/personal Skills into the public repository;
- commit credentials, tokens, cookies, temporary registration secrets, or local secrets;
- use the public repository as the destination for runtime-generated state;
- weaken or remove machine-enforced private-repository checks merely to make a public deployment run.

If a requested action would cross this boundary, stop before the first operational write and route the user through private deployment.

### 5. Actions allowed in the public distribution

The public repository may still be used for non-operational work such as:

- reading source and documentation;
- reviewing architecture;
- cloning the distribution;
- building the extension;
- running source/unit/static tests with sanitized or synthetic data;
- viewing showcases and public evidence;
- preparing a private installation;
- contributing code, documentation, Issues, Pull Requests, or Discussions;
- running the repository's intended public hosted CI/source checks;
- reproducing examples that do not bind private runtime state to the public repository.

The distinction is not "public repository means no code may run."

The distinction is:

> **Public source/testing is allowed; live CAH operational state and user workloads are not.**

### 6. Required migration path before first real operation

Before starting a real CAH instance, use a private operational repository.

A normal installation flow is:

```text
public CAH distribution
        |
        | clone / copy source
        v
new user-owned repository
        |
        | verify GitHub visibility == PRIVATE
        v
private operational repository
        |
        | make it the operational origin
        | configure installation
        | bind runner + Projects
        v
first bounded real task
```

The installer/Agent should ensure, in this order:

1. A user-owned operational repository exists.
2. Its visibility is authoritatively verified as **PRIVATE**.
3. The installation checkout's operational `origin` points to that private repository, not the public CAH distribution.
4. The private repository contains the CAH source needed for operation.
5. Installation-specific configuration is generated only for the private instance.
6. Real Task Cell/lane Project bindings are written only to the private instance.
7. A self-hosted runner is registered only to the intended private operational repository or other explicitly private authorized scope.
8. Runtime state, evidence, Skills, and user-derived write-back go only to the private operational repository.
9. The public CAH repository, if retained as a remote, is treated as an optional **upstream source**, never as the destination for live task-state writes.

Follow `docs/INSTALL_WINDOWS.md` for the maintained installation procedure.

### 7. Fail closed when the privacy boundary is uncertain

If the Agent or installer cannot verify that the operational repository is private:

- do not start normal CAH operation;
- do not create a "temporary" real task in the public repository;
- do not bind a real runner or real ChatGPT Project as a workaround;
- do not assume the user accepts public exposure merely because they asked to proceed quickly.

Instead, clearly report the missing precondition and help the user create, select, or verify a private operational repository.

This gate is deliberately stricter than ordinary task routing because a mistaken public write can be irreversible even if the repository is made private later.

### 8. This prose gate may be deleted after private installation is verified

This section is intentionally verbose because it belongs to the **public distribution bootstrap path**, not the normal Worker hot path.

After all of the following are true:

- the installed operational repository has been authoritatively verified as **PRIVATE**;
- the local operational `origin` points to that private repository;
- real runner/Project bindings are scoped to the private installation;
- live runtime state will be written only to the private repository;
- the installation has passed its normal configuration/readback checks;

then this entire **PUBLIC DISTRIBUTION BOOTSTRAP GATE** section may be deleted from the **private copy** of `AGENTS.md`.

Deleting this section from a verified private installation is expected and desirable: normal Workers should not repeatedly spend context re-reading a one-time deployment warning that has already been satisfied.

Do **not** delete this section from the public CAH distribution.

If a future upstream merge reintroduces this public-only section into an already verified private installation, the private installation may remove it again after re-checking the private-repository boundary.

### 9. Deleting this prose does not authorize removal of runtime safety mechanisms

Only this public-distribution prose gate is disposable after private deployment.

Do **not** interpret its deletion as permission to remove independent machine-enforced protections, including repository-privacy checks, bounded destructive scopes, exact Project/lane identity checks, credential hygiene, fencing/generation checks, or other runtime safety mechanisms.

The private instance remains subject to the rest of this Agent contract and all applicable subsystem contracts.

---

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
