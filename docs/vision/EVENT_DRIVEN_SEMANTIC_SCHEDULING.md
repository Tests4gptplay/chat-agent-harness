# Event-Driven Semantic Scheduling: Tasks Should Not Pin Workers

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Relationship to the multi-lane runtime

The multi-lane model treats Workers as temporary compute capacity. Event-driven scheduling is the rule that prevents a logical task from pinning that capacity while it is unable to make progress.

The scheduler should advance work from canonical state transitions and events, not by keeping chats alive in polling loops.

```text
RUNNING
  |
  +--> WAIT_DEP ------+
  |                   |
  +--> WAIT_RESOURCE -+--> release Worker
  |                   |
  +--> WAIT_EVENT ----+
                      ...
                  event satisfied
                      |
                    READY
                      |
               compatible Worker
                      |
                    RUNNING
```

## Core idea

A logical task should not permanently occupy the Worker that happened to execute its previous segment.

When work reaches an unresolved dependency, unavailable resource or explicit external wait, it should checkpoint the minimum durable continuation state, transition to a wait state and release reasoning capacity.

```text
Task A on Worker 0
 -> dependency unavailable
 -> durable semantic checkpoint
 -> A = WAIT_DEP
 -> Worker 0 becomes schedulable capacity
 ...
 dependency event arrives
 -> A = READY
 -> Worker 8 is compatible and idle
 -> Worker 8 claims A with fresh lease/fence
 -> restore required continuation state
 -> resume
```

## READY is a logical property, not a lane assignment

A task becomes READY because its declared predicates are satisfied, for example:

```text
READY =
  dependencies satisfied
  AND required resources available
  AND capability constraints satisfiable
  AND parent/task not cancelled
  AND current ownership epoch permits dispatch
```

READY does not mean "return to the Worker that previously ran this task."

## Migratable semantic continuation

A continuation should externalize enough explicit state for another compatible Worker to resume:

- task/subtask identity;
- semantic resume boundary;
- unresolved/satisfied dependency refs;
- required memory/evidence refs;
- verified decisions and active constraints;
- capability/resource requirements;
- current ownership/fencing metadata;
- acceptance or verification gate for the next step.

This is semantic migration, not hidden-state migration. An LLM conversation is not a serializable CPU register file.

## Shared memory interaction

Checkpointing and reusable memory are related but distinct:

```text
continuation checkpoint
  = what is needed to resume this logical task

shared semantic memory
  = verified reusable knowledge that may help this or future tasks

evidence/artifacts
  = durable facts and outputs that can be inspected or verified
```

A Worker should retrieve only what the resumed node needs.

## Work stealing and help-join

Completed or idle Workers may claim eligible READY work when this shortens the critical path.

A helper Worker must receive fresh ownership and must respect capability, locality, trust and exclusive-resource constraints. It may not split a non-decomposable critical section merely to keep itself busy.

## Cooperative yield, not arbitrary preemption

CAH should yield at explicit durable semantic boundaries:

- after a result is published;
- before an external wait;
- at a declared checkpoint;
- after a plan revision;
- before ownership transfer.

It should not pretend that an LLM can be interrupted at an arbitrary token and resumed losslessly elsewhere.

## Event-driven wakeup

Prefer state events over repeated semantic probes.

Examples include:

- dependency result accepted;
- resource lease becomes available;
- executor result arrives;
- verification completes;
- cancellation or invalidation occurs;
- capability configuration becomes available.

The event should re-evaluate readiness; it should not itself become a second source of task truth.

## Fairness and starvation

Work stealing and out-of-order execution improve utilization but can starve low-priority or repeatedly preempted work.

A future scheduler may therefore need bounded priority/fairness policy, aging or explicit service classes. These are policy concerns layered above the core ownership/fencing mechanism.

## Performance objective

The objective is not maximum Worker occupancy for its own sake. It is lower end-to-end span:

- release capacity during real waits;
- overlap independent work;
- avoid repeated reasoning;
- reuse durable verified state;
- preserve true dependency ordering.

## Open questions

- What events are strong enough to transition WAIT -> READY?
- How should fairness interact with priority and critical-path scheduling?
- When should warm affinity be preferred over the first compatible idle Worker?
- How much continuation state is sufficient for reliable migration?
- When should a WAIT condition time out into BLOCKED, FAILURE or human escalation?

## Publication and provenance

This document is published as a **time-stamped public record of CAH's exploratory design direction** and to invite technical discussion. It is intended to preserve the project's design chronology and provenance.

Publication does **not** claim exclusive rights over the underlying ideas, methods, algorithms or system concepts, and it does not imply that every element described here is implemented or committed to the roadmap.

Unless otherwise noted, original CAH text, diagrams and other project-authored material in this repository and its project-authored Issues/Discussions are made available under **AGPL-3.0-only** as stated in [NOTICE](../../NOTICE). That license governs copyrightable CAH expression; independent implementations of underlying ideas may exist.

Implementation-specific know-how, security-sensitive details, credentials, private runtime data and unpublished research may remain outside the public materials.

