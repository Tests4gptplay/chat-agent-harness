# Change intake and promotion policy

This policy defines when a conversation, observation, GitHub/platform constraint, feature idea, or maintenance suggestion should become durable repository material.

The purpose is to keep CAH's canonical Git state useful without turning every chat thought into an Issue, task, document, or main-branch change.

## Default rule

A discussion is **not** automatically a repository change.

Use this promotion ladder:

```text
chat / observation
        ↓
discussion or proposal, only if durable tracking is useful
        ↓
accepted problem / feature / decision
        ↓
Issue or Task Contract when work is actually required
        ↓
branch + implementation / documentation
        ↓
tests / evidence / review
        ↓
main
```

Stages may be skipped only when the existing development workflow already permits a small local change, or when an already-accepted durable policy/decision needs a direct documentation update.

Do not create task/evidence/state artifacts merely to remember an exploratory conversation.

## What normally stays out of main

Keep the following in chat or a clearly non-canonical proposal/discussion until they become actionable or accepted:

- exploratory ideas, brainstorming and "could we..." questions;
- unverified hypotheses about GitHub, browsers, models, operating systems or third-party services;
- transient external service limits that have not materially affected CAH;
- feature suggestions without an accepted goal, scope or acceptance boundary;
- comparisons with other projects that do not change CAH requirements;
- duplicate observations already represented by an existing Issue, task or document;
- speculative future architecture with no current implementation commitment.

If the user explicitly says a topic is only discussion, do not mutate the repository for it.

## When to create a durable GitHub artifact

Choose the smallest artifact that matches the maturity of the information.

### GitHub Issue

Create or update an Issue when there is an actionable engineering unit, for example:

- a reproducible defect or operational failure;
- a confirmed external limitation that materially affects current runtime behavior;
- an accepted feature request that needs implementation work;
- a cross-component change that needs coordination or regression coverage;
- a blocker that should survive chat/session replacement.

Defect-specific thresholds and repair flow remain governed by [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md).

### Discussion / proposal

Use a Discussion, proposal note, or equivalent non-canonical design thread when durable debate is useful but the design is not yet accepted.

Examples:

- several plausible architecture choices remain;
- a feature is interesting but scope/priority is unresolved;
- an external platform behavior is being investigated;
- a suggestion needs evidence before becoming an Issue.

If GitHub Discussions is unavailable or not useful, keeping the topic in chat is preferable to manufacturing an Issue solely for storage.

### Canonical documentation

Promote information into canonical docs when it is an accepted and durable project rule, architecture decision, operator requirement, safety boundary, public contract, or verified platform dependency.

Low-frequency policy belongs in `docs/`, not in hot-path files such as `AGENTS.md`, unless an agent must read the rule on routine execution.

### Task / CL / evidence

Create task, condition-ledger and evidence artifacts only for actual controlled execution or verification. They are not a general notebook for ideas.

### Release notes / public-facing update

Record a feature as a product/update claim only after the relevant behavior is implemented and supported by acceptance evidence. Planned behavior must remain clearly labeled as planned.

## GitHub/platform constraints

GitHub is part of CAH's current control plane, so platform limits matter, but they are external and mutable.

Use these rules:

- Local Git object reads such as `git show`, `git diff`, `git rev-parse` and `git cat-file` should be preferred for repeated inspection after synchronization; ordinary local reads do not consume REST/GraphQL API quota.
- Remote synchronization and GitHub REST/GraphQL calls are separate concerns. API calls are rate-limited and may also be subject to secondary/abuse limits.
- Avoid hot polling. Prefer event-driven wakeups, bounded polling, exponential/backoff behavior, and cached/local state where practical.
- GitHub Actions steps/jobs have execution-time limits. Treat Actions as bounded transactions that dispatch work, collect evidence and exit—not as an indefinitely resident control shell.
- Do not encode today's GitHub quota numbers as timeless architecture invariants. When an exact external limit materially affects implementation, record the official source and the date checked, then build graceful `WAIT/RETRY/BLOCKED` behavior rather than assuming permanent capacity.
- A GitHub limit should become an Issue only when it creates a concrete current risk, failure, required guard, or design change. Merely learning that a limit exists is not itself a defect.

The architectural preference is:

```text
GitHub / remote Git = synchronization + canonical durability
local clone          = high-frequency read/inspection
CAH runtime          = bounded work + checkpoints + recovery
```

## Promotion triggers

Promote a discussion when at least one of these becomes true:

- the user explicitly accepts it as a project rule or implementation goal;
- evidence shows it is causing or is likely to cause a concrete current failure;
- implementation work must be scheduled;
- another Worker/session must be able to resume it without chat history;
- a public or operator-facing claim would otherwise become inaccurate;
- the decision changes a shared control, safety, state or compatibility contract.

Do not promote merely because an idea sounds useful.

## Assistant / agent behavior

When discussing CAH:

1. First determine whether the user is brainstorming, reporting a problem, accepting a decision, or requesting implementation.
2. Do not automatically create Issues, tasks, docs or source changes from brainstorming.
3. Reuse or update an existing durable artifact instead of creating a duplicate.
4. Put accepted low-frequency rules in focused docs rather than expanding `AGENTS.md`.
5. Keep volatile third-party facts sourced and dated when they become operationally relevant.
6. Treat Git as the source of truth for accepted engineering decisions and execution state, but not as a dump of every conversation.
7. When a discussion matures into implementation, choose the appropriate direct or managed execution path and use the relevant branch, evidence and review workflow.

## Short decision table

| Conversation state | Default repository action |
|---|---|
| Brainstorm / question / comparison | None |
| Unverified platform behavior | None; investigate first |
| Useful unresolved design debate | Discussion/proposal only if durable tracking adds value |
| Accepted feature needing work | Issue/Task Contract, then normal implementation flow |
| Reproducible tracked defect | Issue, dedicated fix flow |
| Accepted durable policy / architecture rule | Canonical `docs/` update |
| Implemented user-visible behavior | Docs/release note backed by evidence |
| Routine execution progress | Existing task/CL/evidence only; do not create unrelated project records |
