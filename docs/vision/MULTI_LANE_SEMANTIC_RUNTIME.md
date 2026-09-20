# Multi-Lane Semantic Runtime: From Two-Lane Proof to Dynamic Worker Scheduling

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Motivation

The current two-lane design is the minimum useful proof that CAH can execute more than one semantic branch at once. It should be treated as a construction guide for a broader multi-lane runtime, not as a fixed two-worker architecture.

The long-term model is **not** "one lane permanently owns one branch." Tasks belong to the durable DAG; lanes/Workers are temporary execution capacity that may be released, reused, migrated or reassigned as dependencies and resources change.

```text
                         Planner / runtime coordinator
                                   |
                         durable task DAG + events
                                   |
                        READY set / dependency state
                                   |
                    dynamic claim / lease / fencing
                       /          |          \
                      v           v           v
                 Worker A     Worker B     Worker N
                 hot cache    hot cache    hot cache
                      \           |           /
                       +---- publish / checkpoint ----+
                                      |
                                      v
                    durable semantic memory / evidence pool
                    results / decisions / failed paths / refs
                                      |
                     +----------------+----------------+
                     |                                 |
              resume / restore                  reducer / verifier
                     |                                 |
             any compatible Worker             canonical promotion
```

Workers can finish, block, discover new work or lose their execution environment without remaining the durable owner of the task.

## Task ownership and execution capacity

The central invariant is:

> **Tasks belong to the DAG. Workers are temporary compatible compute capacity.**

A logical task therefore has a durable identity independent of the Worker that last executed it. A Worker claim is temporary and must be protected by explicit lease/generation/fencing state so that a stale or revived Worker cannot overwrite current ownership.

Physical lane count may grow or shrink without changing task identity.

## Shared durable memory and lane-local cache

Each Worker may keep a short-lived hot working set for efficient reasoning, but correctness must not depend on that cache surviving.

Useful state should be promoted into shared durable memory/evidence when it becomes reusable or required for continuation:

- verified intermediate conclusions;
- failed hypotheses that should not be repeated;
- accepted decisions and constraints;
- compact continuation checkpoints;
- result/evidence references;
- discovered capability or resource requirements;
- reusable task-local summaries.

This pool is **not raw shared chat history**. It is structured, evidence-referenced semantic state that another Worker can retrieve selectively.

```text
lane-local hot context
        |
task checkpoint / handoff
        |
shared durable semantic memory / evidence
        |
project-level reusable knowledge / procedures
        |
cold archive / large artifacts
```

## Dynamic scheduling and work stealing

A Worker that finishes its current node should not become idle merely because its original branch ended.

```text
Worker B finishes node B
 -> publish result / verified memory / evidence refs
 -> release B ownership and resources
 -> scheduler observes another eligible READY node
 -> Worker B claims it with a fresh lease/fence
 -> restore only required context
 -> continue
```

This is closer to **help-join / work stealing** than fixed one-lane-per-task execution.

Independent READY nodes may therefore execute out of historical order whenever dependency, capability and resource constraints permit it.

## Suspended tasks release Worker capacity

A logical task that cannot make progress should checkpoint and release its Worker.

```text
Task A on Worker 0
 -> dependency/resource unavailable
 -> checkpoint continuation + memory/evidence refs
 -> A = WAIT_DEP / WAIT_RESOURCE
 -> Worker 0 becomes free
 -> Worker 0 may execute unrelated READY work
 ...
 dependency event arrives
 -> A = READY
 -> any compatible idle Worker may claim A
 -> fresh lease / generation / fence
 -> restore durable continuation
 -> resume
```

The original Worker may be preferred for warm-context locality, but affinity is an optimization, never identity.

## Barriers and reducers are local dependency mechanisms

Not every lane must finish and then flow into one universal reduction stage.

```text
A ----+
      +--> join/reducer --> D
B ----+

C -----------------------> E
```

Barriers, joins and reducers exist only where the task graph requires synchronization, verification or authoritative merge.

## Dynamic DAG expansion

Workers may discover new subproblems while executing a node. The Planner/runtime coordinator may then expand or revise the DAG rather than forcing a one-shot plan to completion.

Newly discovered work should enter the same dependency/READY machinery and should not be coupled to the Worker that discovered it.

## Scaling invariants

An N-lane implementation should preserve:

- lane = execution capacity, not permanent semantic role;
- conversation/process = Worker incarnation, not durable identity;
- task identity and state live outside the Worker;
- ownership uses leases/generation/fencing;
- stale Workers cannot mutate current scheduler state;
- blocked work releases Worker capacity;
- completed Workers may help other eligible READY nodes;
- reusable semantic state is promoted to shared durable memory/evidence;
- inter-lane transfer uses compact contracts and refs, not raw conversation splicing;
- non-mergeable resources require explicit ownership or serialization;
- out-of-order execution is allowed when dependencies permit it;
- reducers exist only at real synchronization points;
- parallelism is justified by reduced critical-path time, not Worker count.

## Performance model

The useful mental model is work/span rather than a fixed-thread pipeline.

```text
T_P >= max(W / P, S)
```

where `W` is total useful work, `P` is compatible Worker capacity and `S` is the dependency span / critical path.

The scheduler should reduce idle capacity, hide dependency/tool wait time, avoid duplicated reasoning and reuse verified semantic memory where it shortens future work.

## Relationship to the other future-architecture ideas

This document defines the core execution model.

- **Event-Driven Semantic Scheduling** defines how WAIT/READY transitions release and reacquire Worker capacity.
- **Shared-Nothing Distributed Agent Fabric** extends the same model across machines, networks and failure domains.
- **Capability-Centric Scheduling** decides which compatible Worker/node should claim a READY node.
- **Durable Semantic Memory** defines the shared semantic substrate used for continuation, work stealing and reuse.
- **Permissionless Public Agent Network** explores extending the same model from trusted nodes to mutually untrusted public capacity.

## Open questions

- How should READY work be partitioned as Worker count grows?
- When should warm affinity outweigh immediate free-capacity placement?
- How much state must a continuation externalize before migration is reliable?
- Which intermediate conclusions are eligible for promotion into shared memory?
- How should reusable memory be invalidated when upstream facts change?
- Which reducer/verifier operations should remain deterministic?
- What resource-locking model is sufficient without turning CAH into a general cluster scheduler?
