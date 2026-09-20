# CAH Future Architecture — Discussion Drafts

**Status:** exploratory. These documents describe possible future directions, not implemented features, release promises, or roadmap commitments.

CAH is being developed around a simple separation: **tasks are durable; Workers are replaceable compute**. The current two-lane work is intentionally the smallest practical proof of a broader multi-lane scheduling model, not a fixed two-worker architecture.

The six ideas form one architectural progression rather than six unrelated feature wishes:

```text
Multi-Lane Semantic Runtime
        |
        +--> Event-Driven Scheduling
        |      WAIT releases Worker capacity
        |      READY work may resume elsewhere
        |
        +--> Durable Semantic Memory
        |      shared continuation / evidence / reusable reasoning
        |
        v
Shared-Nothing Distributed Agent Fabric
        |
        v
Capability-Centric Scheduling
        |      choose compatible model/hardware/region/trust domain
        |
        v
Permissionless Public Agent Network
               untrusted public capacity
               verification / challenge / optional settlement
```

Another way to state the common invariant:

> **State follows the task; compute follows available compatible Workers.**

The drafts are:

1. [Multi-Lane Semantic Runtime](MULTI_LANE_SEMANTIC_RUNTIME.md) — scale the two-lane proof into a dynamic task-DAG runtime with READY work, work stealing, migratable execution and shared durable semantic memory/evidence.
2. [Event-Driven Semantic Scheduling](EVENT_DRIVEN_SEMANTIC_SCHEDULING.md) — release Worker capacity during dependency/resource waits and re-enqueue continuations when canonical events make them READY.
3. [Shared-Nothing Distributed Agent Fabric](SHARED_NOTHING_AGENT_FABRIC.md) — extend the same scheduling/memory semantics across machines, networks and failure domains without requiring shared local state.
4. [Capability-Centric Scheduling](CAPABILITY_CENTRIC_SCHEDULING.md) — place READY work by hard capability/resource/trust constraints and soft locality/cost/latency preferences rather than fixed lane identity.
5. [Durable Semantic Memory as a Compute Asset](DURABLE_SEMANTIC_MEMORY.md) — treat verified conclusions, failed paths, continuation state and evidence as reusable semantic computation while keeping Worker hot context disposable.
6. [Toward a Permissionless Public Agent Network](PERMISSIONLESS_AGENT_NETWORK.md) — explore extending the trusted distributed model to untrusted public nodes with bounded disclosure, proof/verification, challenge/dispute and optional settlement.

## Shared design rules

Across all six ideas:

- tasks belong to the durable DAG, not the Worker that last executed them;
- Workers/lane processes are replaceable compute capacity;
- READY work may execute out of historical order when dependencies permit;
- WAIT states should release expensive Worker capacity;
- reusable semantic state is shared through durable structured memory/evidence, not raw chat-history splicing;
- capability, locality, trust and resource leases constrain placement;
- stale execution is fenced from authoritative writes;
- reducers/verifiers decide which outputs become canonical;
- large/private semantic state stays off-chain even if a future settlement layer exists.

## Design discipline

These ideas should evolve from measured workload needs, not from a desire to imitate operating systems, GPU runtimes, cluster schedulers or blockchains literally.

The public project should continue to distinguish:

- current implementation;
- live-proven behavior;
- planned engineering work;
- exploratory architecture.

A discussion may later become an RFC, design document, roadmap item or implementation issue only after its requirements become concrete.
