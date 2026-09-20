# CAH Future Architecture — Discussion Drafts

**Status:** exploratory. These documents describe possible future directions, not implemented features, release promises, or roadmap commitments.

CAH is being developed around a simple separation: **tasks are durable; Workers are replaceable compute**. The current two-lane work is intentionally the smallest practical proof of a broader multi-lane scheduling model, not a fixed two-worker architecture.

The drafts below record the main directions that may extend that model:

1. [Multi-Lane Semantic Runtime](MULTI_LANE_SEMANTIC_RUNTIME.md) — scale the two-lane proof into dynamic N-lane scheduling while preserving ownership, fencing, isolation, evidence and reduction semantics.
2. [Event-Driven Semantic Scheduling](EVENT_DRIVEN_SEMANTIC_SCHEDULING.md) — suspended logical work should release Worker capacity and resume later on any compatible Worker.
3. [Shared-Nothing Distributed Agent Fabric](SHARED_NOTHING_AGENT_FABRIC.md) — allow Workers and execution nodes to live on different machines, networks and failure domains without making any one host the durable identity of the task.
4. [Capability-Centric Scheduling](CAPABILITY_CENTRIC_SCHEDULING.md) — place work by required model, software, hardware, locality, trust, cost and latency rather than by fixed lane names.
5. [Durable Semantic Memory as a Compute Asset](DURABLE_SEMANTIC_MEMORY.md) — treat verified conclusions, failed paths, decisions and evidence as reusable paid-for reasoning rather than disposable chat history.
6. [Toward a Permissionless Public Agent Network](PERMISSIONLESS_AGENT_NETWORK.md) — explore how the same task/lease/evidence model might extend to mutually untrusted public nodes with verification, challenge/dispute and optional settlement.

## Design discipline

These ideas should evolve from measured workload needs, not from a desire to imitate operating systems, GPU runtimes, cluster schedulers or blockchains literally.

The public project should continue to distinguish:

- current implementation;
- live-proven behavior;
- planned engineering work;
- exploratory architecture.

A discussion may later become an RFC, design document, roadmap item or implementation issue only after its requirements become concrete.
