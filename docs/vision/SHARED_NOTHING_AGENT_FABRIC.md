# Shared-Nothing Distributed Agent Fabric

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Relationship to the multi-lane runtime

This direction extends the same dynamic READY/claim/continuation model beyond one host.

The goal is not simply "more lanes on more computers." Tasks should remain migratable and recoverable even when Workers live in different processes, machines, networks, model providers or failure domains.

```text
                     durable task DAG + READY state
                               |
                     global scheduling decisions
                               |
             +-----------------+-----------------+
             |                 |                 |
         Node / Host A     Node / Host B     Node / Host C
          Worker A1         Worker B1         Worker C1
          hot cache         hot cache         hot cache
             |                 |                 |
             +-------- publish/checkpoint -------+
                               |
                               v
                durable semantic memory / evidence
                continuations / results / refs / decisions
```

Tasks still belong to the DAG, not to the machine that last executed them.

## Shared-nothing does not mean no shared logical state

Nodes should not require shared RAM, one local filesystem, one browser process or direct Worker-to-Worker conversation.

They cooperate through durable contracts and references:

- task identity and dependency state;
- READY/WAIT transitions;
- ownership leases and fencing epochs;
- continuation checkpoints;
- semantic memory/evidence refs;
- artifact addresses and content hashes.

The shared state is logical and durable; its physical storage may be Git-backed control state plus object/content-addressed artifact stores or other replaceable backends.

## Cross-host work stealing and migration

An idle compatible node may claim READY work that originated elsewhere.

```text
Node B finishes current node
 -> publishes result / reusable memory / evidence
 -> releases previous lease/resources
 -> scheduler sees another compatible READY node
 -> Node B claims it with fresh ownership/fence
 -> loads only required continuation/memory refs
 -> executes
```

Likewise, a task suspended on Node A may later resume on Node C.

## Memory hierarchy across hosts

```text
node-local Worker hot context
           |
node-local task cache / scratch
           |
durable semantic memory / evidence
           |
managed/content-addressed artifacts
           |
cold archive
```

Only durable layers participate in recovery semantics.

Before ownership migrates, useful state must escape the local cache. Large artifacts need not live in Git; the canonical state may carry stable refs, hashes and provenance.

## Data locality and resource gravity

Moving compute is often cheaper than moving large data or rebuilding authenticated/tool environments.

Placement should therefore consider:

- large local datasets or artifacts;
- GPU/toolchain locality;
- authenticated browser/tool state;
- licensed or scarce software;
- privacy/trust boundaries;
- network cost and latency.

A nominally idle node is not necessarily eligible for a task.

## Failure and partition semantics

Distributed execution introduces:

- node crashes;
- network partitions;
- delayed stale results;
- duplicated recovery attempts;
- split-brain ownership;
- unavailable artifacts;
- partially visible state.

Generation/fencing and explicit ownership epochs are essential. A late Worker may publish diagnostics, but must not mutate authoritative scheduler state after its lease/fence becomes stale.

## Node identity and capability advertisement

A future node should have a stable authenticated identity and a bounded capability manifest. Scheduler-visible claims should be verifiable where possible rather than trusting arbitrary self-description.

The node identity is a routing/trust primitive, not task identity.

## Trust domains and secrets

Not all nodes should receive the same task packet or memory.

Sensitive inputs, credentials and private semantic memory may constrain placement to a trusted domain. Cross-domain scheduling should expose only the minimum data required for the selected work.

## Relationship to capability scheduling

The distributed fabric supplies heterogeneous capacity. Capability-centric scheduling decides which node is eligible and desirable for each READY task.

## Open questions

- node identity and authentication;
- lease expiry under network partitions;
- artifact addressing and large-object transport;
- capability attestation;
- data locality and trust domains;
- split-brain recovery;
- scheduler centralization versus partitioning;
- which state belongs in durable control versus node-local caches.
