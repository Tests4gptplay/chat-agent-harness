# Public export feature contract

A public edition of CAH must preserve **capabilities**, even when private data, user-specific state, local paths, private case artifacts, or personal Skill contents are removed.

The public export is therefore not a trimmed demo fork. It is a sanitized distribution of the same architecture.

## Required capability invariants

A public edition must preserve at least:

- Git-canonical continuity/state and replaceable Worker semantics;
- foreground/backend CL supervision and evidence-gated completion;
- scheduler dispatch identity, generation/fencing, WAIT/continuation behavior;
- backend lane topology and bounded Worker-generation lifecycle;
- lane-scoped conversational reset (`lane_clear`) as a preserved lifecycle-maintenance primitive;
- deterministic executor interface and capability routing;
- Stage 1 parallel DAG/barrier/reducer primitives once they are part of main;
- **Skill Registry as a first-class Agent capability**;
- Skill candidate/active/deprecated lifecycle;
- evidence-backed Skill promotion and PASS/non-PASS history;
- Planner Skill lookup/reuse rules;
- post-task Skill distillation guidance;
- external Skill import adapter contract, including Codex-style incremental reconciliation;
- self-maintenance/update contracts that are safe to publish;
- a reproducible Windows installation path;
- a one-click Windows launcher entry point equivalent to `Start_CAH.bat`;
- Chromium extension build/load instructions and lane Project onboarding;
- context-economy guidance that keeps hot-path contracts small and routes low-frequency knowledge through focused docs/Skills/Cases.

## Skill-specific export rule

Sanitization may remove or replace:

- the maintainer's private/personal Skills;
- imported Codex Skill source snapshots;
- local filesystem identities;
- private task evidence;
- proprietary cases or artifacts.

Sanitization must **not** remove:

- `harness/skill.schema.json`;
- `harness/skills.py`;
- `docs/SKILL_SYSTEM.md`;
- the `skills/` directory structure and at least one safe example Skill;
- Skill routing in `ai/repo-map.json`;
- Worker Skill guidance in `AGENTS.md`;
- Skill Registry tests;
- the external Skill import interface/docs where the adapter itself is publishable.

If private Skills are stripped, replace them with sanitized examples so public users receive a functional Skill subsystem rather than an empty architectural reference.

## Why Skill is core

CAH is intended to improve with repeated use. That requires more than durable task memory.

```text
Memory   -> what happened
Skill    -> how to do a recurring class of work
Executor -> deterministic implementation
Evidence -> whether the procedure actually worked
```

Removing Skill accumulation would reduce CAH from a learning/reuse-oriented Agent runtime to a stateless orchestration harness. That is a product capability regression and is not an acceptable public-export simplification.

## Export verification

`harness/public_export_required.json` is the machine-checkable minimum path/capability manifest. CI verifies that every required core path still exists.

The manifest is intentionally conservative: private examples may change, but the mechanisms that make Skill accumulation, scheduling, continuity, evidence, and execution work must survive public sanitization.


## Installation / packaging export rule

The private development environment has demonstrated that installation is a real product surface, not incidental operator knowledge.

A public edition must therefore preserve:

- `docs/INSTALL_WINDOWS.md` or an equivalent maintained installation guide;
- a Windows one-click launcher entry point;
- the Chromium extension build/package path;
- documented `chrome://extensions` Developer mode / **Load unpacked** setup for source installs;
- self-hosted runner setup;
- localhost bridge startup/health verification;
- lane Project registration and first-run topology checks.

The public launcher **must not** ship maintainer-specific drive letters, usernames, runner roots, ChatGPT Project keys, or Project URLs as authoritative defaults. Those values must be user-configurable, installer-generated, or replaced with clearly fake examples.

The public export should preserve the proven user experience:

```text
first-time setup
  -> install/configure runner
  -> build/load Chromium extension
  -> configure own ChatGPT lane Projects
  -> Start_CAH.bat
  -> bridge + runner + browser lanes become available
  -> health/topology evidence confirms readiness
```

Private machine paths and account identities are evidence to guide packaging, not material to copy into the public release.


## Lifecycle maintenance export rule

A public edition must preserve the ability to reset disposable conversational state for one explicitly registered backend lane without deleting the ChatGPT Project or canonical Git state.

The maintained contract is `docs/LIFECYCLE_MAINTENANCE.md`.

At minimum, the exported runtime must preserve:

- exact lane/project identity scoping;
- Project-preserving lane reset;
- canonical Git/evidence preservation;
- durable terminal completion with `deleted_count` and `remaining_count`;
- fail-closed behavior when the target Project or conversation surface is ambiguous.

A public cleanup implementation may change internally, but removing the capability or replacing it with an unsafe broad account-level deletion operation is not acceptable.


## Context-economy export rule

A public edition must preserve the design principle that model context is an architectural budget.

The maintained guidance is `docs/CONTEXT_ECONOMY.md`.

Public/export refactors must preserve:

- minimal high-frequency runtime contracts;
- focused low-frequency subsystem documents;
- machine-readable routing through `ai/repo-map.json`;
- selective Skill retrieval rather than loading the entire Skill library;
- separation between current State, reusable Skills, subsystem contracts, and historical Cases/evidence;
- one canonical explanation with short pointers instead of duplicated detailed prose across hot-path files.

Sanitization may simplify private examples, but it should not collapse all documentation back into `AGENTS.md`, README, or canonical State.


## Host capability export rule

A public edition must preserve the shared host-capability/configuration runtime.

The maintained contract is `docs/HOST_CAPABILITIES.md`.

Public export must retain:

- provider-based host tool discovery;
- machine-local persistence for verified absolute paths;
- sanitized Git/Worker-visible capability projections;
- version validation from authoritative tool metadata rather than directory names;
- recoverable `WAIT_RESOURCE / NEED_HOST_CONFIG` semantics;
- fresh generation/fence resume after a missing host capability is configured;
- tests proving that private absolute paths do not leak into sanitized projections.

Private machine paths, local capability cache contents, and maintainer-specific configuration values must not be exported.
