# Multi-Lane Semantic Runtime: From Two-Lane Proof to Dynamic Worker Scheduling

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Motivation

The current two-lane design is the minimum useful proof that CAH can schedule independent semantic work across more than one replaceable Worker. It should be treated as a construction guide for a broader multi-lane runtime, not as a fixed two-worker product architecture.

The target abstraction is:

```text
durable task graph
      |
  READY work
      |
   scheduler
  /   |   |   \
lane lane lane lane ...
  \   |   |   /
 evidence / barriers / reduction
```

## Scaling invariants

An N-lane implementation should preserve properties already required by the small proof:

- a lane is logical execution capacity, not a permanent business role;
- a conversation or process is an incarnation of a Worker, not durable identity;
- authoritative ownership uses task identity plus a current generation/fence;
- stale Workers cannot mutate current scheduler state;
- each lane has an independent lifecycle and recovery boundary;
- Workers exchange compact durable results/evidence rather than entire chat histories;
- non-mergeable resources require explicit ownership or serialization;
- fan-out is justified only when it shortens the useful critical path;
- parent completion is evidence-gated and may require barrier/reducer semantics.

## Target direction

Over time, fixed lane assignment may become dynamic:

```text
READY subtask
 + compatible free capacity
 + satisfied dependencies
 + required resource lease
 + current ownership epoch
        |
        v
ASSIGNED -> RUNNING -> terminal evidence
```

The number of physical Worker hosts should be an implementation choice. Task semantics should remain stable as capacity grows or shrinks.

## Open questions

- How should READY queues be partitioned as lane count grows?
- When should warm Worker affinity outweigh free-capacity scheduling?
- Which reducer/verifier operations should be deterministic?
- When does lane-local state become too expensive to reconstruct?
- What resource-locking model is sufficient without turning CAH into a general cluster scheduler?
