# Shared-Nothing Distributed Agent Fabric

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Direction

A future CAH deployment should not require every Worker or executor to share one process, machine, local network, filesystem, browser runtime, model provider or failure domain.

The durable task system should be sufficient to coordinate replaceable nodes:

```text
                 durable control state
                        |
        +---------------+---------------+
        |               |               |
   reasoning node   execution node   verification node
        |               |               |
   independent      independent      independent
   failure domain   failure domain   failure domain
```

Nodes should cooperate through explicit contracts, ownership, events, result references and evidence rather than direct shared conversational memory.

## Desired properties

- loss of one host does not erase task identity;
- a replacement node can resume from durable state;
- delayed stale results are rejected by generation/fencing checks;
- data and artifacts can remain outside the control store while being addressed by stable references and hashes;
- node-local caches improve performance but are never required for correctness;
- direct Worker-to-Worker connectivity is optional rather than foundational.

## Cross-machine execution

Examples of future placement may include:

- a graphics workload near a compatible GPU/toolchain;
- a build workload near its source tree and compiler;
- a browser task on a node with the required authenticated environment;
- a research or reasoning branch on a compatible model endpoint;
- verification on an independent node.

The scheduler should prefer moving compute toward data/resources when moving large data would dominate the task.

## Open questions

- node identity and authentication;
- lease expiry and network partitions;
- artifact addressing and large-object transport;
- data locality and trust domains;
- capability discovery;
- recovery from split-brain conditions;
- which state belongs in the durable control plane versus node-local runtime.
