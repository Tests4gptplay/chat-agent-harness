# Event-Driven Semantic Scheduling: Tasks Should Not Pin Workers

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Core idea

A logical task should not permanently occupy the Worker that happened to execute its previous segment.

When work reaches an unresolved dependency or unavailable resource, it should checkpoint the minimum durable continuation state, enter a wait state, and release reasoning capacity.

```text
Task A on Worker 0
 -> dependency unavailable
 -> durable checkpoint
 -> WAIT_DEP / WAIT_RESOURCE
 -> Worker 0 becomes free
 ...
 dependency event arrives
 -> Task A becomes READY
 -> any compatible Worker claims it
 -> fresh lease / generation / fence
 -> resume from durable continuation
```

## Why this matters

LLM-backed Workers are expensive semantic capacity. Waiting for a tool, dependency or remote result should not pin that capacity when unrelated READY work exists.

This allows CAH to approach a work/span style execution model: blocked logical work stops consuming reasoning slots, while independent work continues.

## Migratable continuation

Correctness must not depend on the original Worker returning.

A continuation should externalize enough explicit state to resume:

- task/subtask identity;
- semantic resume boundary;
- satisfied and unresolved dependencies;
- required evidence/result references;
- relevant verified decisions and constraints;
- capability/resource requirements;
- current ownership metadata.

A warm original Worker may be preferred for locality, but affinity is an optimization rather than identity.

## Work stealing / help-join

Completed or idle Workers may claim eligible READY successors when that usefully shortens the critical path.

They must not split non-decomposable critical sections merely to stay busy. Every reassignment receives fresh ownership/fencing, and shared resources remain independently leased.

## Important limitation

An LLM conversation is not a serializable CPU register file. CAH can migrate explicit semantic state, evidence, decisions and checkpoints, not hidden model activations. Resume correctness therefore depends on durable explicit context plus verification.
