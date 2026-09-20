# Multi-Lane Semantic Runtime: From Two-Lane Proof to Dynamic Worker Scheduling

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Motivation

The current two-lane design is the minimum useful proof that CAH can execute more than one semantic branch at once. It should be treated as a construction guide for a broader multi-lane runtime, not as a fixed two-worker architecture.

The long-term model is **not** "one lane permanently owns one branch." Tasks belong to the durable DAG; lanes/Workers are temporary execution capacity that may be released, reused, migrated or reassigned as dependencies and resources change.

A closer target abstraction is:

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
                       \          |          /
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

Workers can finish, block, or discover new work without remaining bound to their original branch.

## Shared durable memory and lane-local cache

Each Worker may keep a short-lived hot working set for efficient reasoning, but correctness must not depend on that cache surviving.

Useful state should be promoted into shared durable memory/evidence when it becomes reusable or required for continuation, for example:

- verified intermediate conclusions;
- failed hypotheses that should not be repeated;
- accepted decisions and constraints;
- compact continuation checkpoints;
- result/evidence references;
- discovered capability or resource requirements;
- reusable task-local summaries.

This pool is not raw shared chat history. It is structured, durable semantic state that other Workers can retrieve selectively.

The intended hierarchy is:

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

Workers should load the minimum context required for the node they claim rather than inheriting another Worker's full conversation.

## Dynamic scheduling and work stealing

A lane that completes its assigned work should not become idle merely because its original branch ended.

After publishing its useful result and releasing old ownership/resources, it may claim another eligible READY node:

```text
Worker B finishes node B
 -> publish result / verified memory / evidence refs
 -> release B ownership and resources
 -> scheduler observes another READY decomposable node
 -> Worker B claims it with a fresh lease/fence
 -> restore only required context from durable memory
 -> continue in parallel
```

This is closer to **help-join / work stealing** than fixed one-lane-per-task execution.

The scheduler may therefore execute independent DAG nodes out of historical order whenever dependencies allow it.

## Suspended tasks release Worker capacity

A Worker should also be released when its logical task cannot make semantic progress.

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

## Barriers and reducers are dependency mechanisms, not a global funnel

Not every lane must finish and then flow into one universal reduction stage.

Barriers, joins and reducers exist only where the task graph requires synchronization or authoritative merge:

```text
A ----+
      +--> join/reducer --> D
B ----+

C -----------------------> E
```

Independent work may continue while other branches wait or reduce.

## Scaling invariants

An N-lane implementation should preserve the properties already required by the small two-lane proof:

- a lane is logical execution capacity, not a permanent business role;
- a conversation/process is a Worker incarnation, not durable identity;
- tasks belong to the DAG rather than to the lane that last executed them;
- ownership is explicit and protected by lease/generation/fencing;
- stale Workers cannot mutate current scheduler state;
- blocked work releases Worker capacity instead of pinning it;
- completed Workers may help other eligible READY nodes;
- reusable semantic state is promoted to shared durable memory/evidence;
- inter-lane transfer uses compact contracts and refs, not raw conversation splicing;
- non-mergeable resources require explicit ownership or serialization;
- out-of-order execution is allowed when dependencies permit it;
- barrier/reducer semantics are introduced only at real synchronization points;
- parallelism is justified by reduced critical-path time, not by Worker count.

## Performance model

The useful mental model is work/span rather than a fixed-thread pipeline.

```text
T_P >= max(W / P, S)
```

where:

- `W` is total useful work;
- `P` is compatible Worker capacity;
- `S` is the dependency span / critical path.

The scheduler should reduce idle capacity, hide dependency/tool wait time, avoid duplicated reasoning and reuse verified semantic memory where it shortens future work.

## Open questions

- How should READY work be partitioned as Worker count grows?
- When should warm affinity outweigh immediate free-capacity placement?
- How much state must a continuation externalize before migration is reliable?
- Which intermediate conclusions are eligible for promotion into shared memory?
- How should reusable memory be invalidated when upstream facts change?
- Which reducer/verifier operations should remain deterministic?
- What resource-locking model is sufficient without turning CAH into a general cluster scheduler?
