# Upstream patterns adopted selectively

This document records ideas learned from other agent harnesses and how they are adapted to CAH. It is a design-decision ledger, not a vendored dependency list.

No source code is copied from the projects below. CAH keeps its own architecture: ChatGPT subscription reasoning, Git as canonical durable state, replaceable managed Workers, GitHub/self-hosted execution, and localhost/browser wake transport.

## Sources reviewed

- SUNRNEHUI/agent-harness — portable continuity contracts, bounded resume capsules, evidence-based acceptance, Progress Circuit Breaker.
- sandbaseai/sandbase-harness — explicit sandbox/backend capability declarations, self-hosted worker boundary, AI-readable installation metadata.
- hnykda/talon — persistent-agent supervision, health separation, restart/backoff patterns.
- rjunee/neutron — subscription-backed model substrate with orchestration kept outside inference.
- deepseek-ai/deepseek-harness — optional inner-runtime reference only; see `docs/REFERENCES.md`.

## Adopt now

### 1. Progress Circuit Breaker

CAH Workers must treat "progress" as an observable change that advances a named acceptance boundary. Examples:

- new evidence;
- an artifact change;
- a test result that discriminates hypotheses;
- a binding decision that changes the next action.

Rephrasing the same theory, rescanning unchanged data, or repeating an equivalent command with the same result is not progress.

After **two consecutive no-progress cycles on the same diagnosis**, do not issue another equivalent action. The next step must be one of:

1. a falsifying experiment that could disprove the current diagnosis;
2. a materially different hypothesis with a reason for the change;
3. `BLOCKED` / `NEED_USER` if neither can be justified.

This rule is intentionally model-side. It does not create another durable database or force every tool call into a new state field.

### 2. Executor capability manifest

Do not learn stable host limitations by repeatedly failing tool calls.

CAH defines a small sanitized capability manifest in `harness/capabilities.schema.json` and a dependency-free probe in `executors/probe_capabilities.py`.

The manifest reports availability/version metadata only. It deliberately omits:

- usernames;
- hostnames;
- absolute executable paths;
- credentials;
- environment-variable values.

A consumer may checkpoint a sanitized manifest to Git when useful, or keep it local and return it as evidence. Canonical task state remains in Git; the capability manifest is advisory execution evidence, not a second source of truth.

If a current capability manifest says a required feature is unavailable, route around it or refresh the probe. Do not spend an Agent round proving the same known limitation by failure.

## Stage, but do not activate yet

### Bounded resume capsule

A deterministic bounded `capsule.md` can reduce Worker startup context once `state/chatgpt.json` becomes materially large.

Do not activate this prematurely. For CAH:

- Git state remains canonical;
- a capsule would be a regenerated cache only;
- freshness must be verifiable;
- a stale capsule must fail closed to canonical state.

Implement when measured Worker startup/context cost justifies another generated artifact.

### Per-subsystem exponential backoff

Useful if one subsystem repeatedly fails, especially browser UI creation or an unavailable transport.

Do not globally slow the one-minute maintenance loop. If added, backoff must be scoped to the failing subsystem and reset immediately after recovery.

Implement only after repeated live failures show that one-minute retry churn is materially wasteful.

### Local deterministic micro-loop

For Blender/UE tasks, a single coarse CAH action may eventually run several deterministic local steps before returning one compact result:

```text
Worker -> one coarse action
       -> local executor
          -> inspect
          -> modify
          -> validate
          -> render/test
       -> one compact result/evidence package
       -> Worker
```

This avoids paying a full Git -> Actions -> Runner -> Wake round trip for every tiny mechanical edit.

Do not build a second hidden LLM agent loop into the runner by default. Start with deterministic micro-steps and add an inner runtime only if a real task demonstrates the need.

## Explicitly not adopted

For the current single-primary-node architecture, do not add these merely because other harnesses have them:

- a second durable SQLite control database beside Git;
- a generic distributed work-item queue beside GitHub Actions;
- a persistent tmux/CLI agent fleet as the identity of the Agent;
- full event replay/audit machinery for ordinary tasks;
- ownership epochs beyond the existing exact handoff-id gates;
- mandatory heavy receipts for trivial operations.

CAH should remain a thin control plane. Add infrastructure only when it removes measured friction or closes a demonstrated correctness gap.
