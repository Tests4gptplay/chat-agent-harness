# Durable Semantic Memory as a Compute Asset

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Core idea

Useful reasoning is work that has already been paid for.

Verified conclusions, failed hypotheses, accepted decisions, discriminating tests, capability discoveries and evidence should not be discarded with a Worker conversation when they can safely reduce future reasoning.

This memory is also the semantic substrate that makes task migration and work stealing possible.

```text
Worker hot context
      |
task continuation checkpoint
      |
shared durable semantic memory / evidence pool
      |
project-level reusable knowledge / procedures
      |
artifact store / cold archive
```

## Memory is not one undifferentiated store

CAH should separate several kinds of state.

### Worker-local hot context

Fast, ephemeral, task-local reasoning cache.

Useful for performance, but never required for correctness.

### Continuation state

The minimum explicit state needed to resume a suspended logical task:

- resume boundary;
- active constraints;
- unresolved dependencies;
- required memory/evidence refs;
- current task ownership/fencing context.

Continuation state follows the task.

### Shared semantic memory

Evidence-backed knowledge that may be reused by another Worker or later task:

- verified conclusions;
- known failed paths;
- accepted design decisions and rationale;
- validated procedures;
- capability/resource discoveries;
- compact reusable summaries;
- proven task-graph patterns.

### Evidence and artifacts

Durable facts and outputs used to verify claims:

- structured results;
- hashes;
- logs;
- test reports;
- artifact manifests;
- external storage refs.

Semantic memory may point to evidence; it should not replace it.

## Promotion into shared memory

Not every model thought should become durable memory.

Promotion should require an explicit gate such as:

```text
candidate insight
   |
scope + provenance
   |
evidence / verification
   |
accepted semantic memory
```

A reducer/verifier or other declared authority may decide which branch results become canonical shared memory.

## Failed paths are useful compute

A disproven hypothesis can be valuable if its scope and evidence are preserved.

Recording "this path failed under these conditions, with this evidence" can prevent later Workers from paying the same reasoning/tool cost again.

It should not become an absolute global prohibition if its validity is context-dependent.

## Retrieval should be selective

A Worker claiming a READY node should not reload the whole project history.

Retrieval should prefer the minimum sufficient context based on:

- current task/subtask;
- dependency refs;
- active constraints;
- capability/tool requirements;
- relevant prior conclusions;
- required evidence.

This keeps lane-local context small while allowing the shared pool to grow.

## Invalidation and versioning

Reusable memory can become stale when:

- upstream facts change;
- tool/model versions change;
- an assumption is invalidated;
- stronger evidence contradicts an earlier conclusion;
- scope was narrower than originally recorded.

Memory therefore needs provenance, scope and revision/invalidation semantics rather than a permanent undifferentiated "truth" flag.

## Scheduling implications

Memory locality can influence placement without becoming identity.

A Worker with warm relevant context may be preferred, but any compatible Worker should be able to reconstruct the task from durable state.

In a distributed fabric, shared memory/evidence refs allow work to migrate across hosts without copying full conversation histories.

## Relationship to permissionless execution

Public or untrusted nodes should not receive the entire shared memory pool.

The scheduler should construct bounded task packets containing only the memory/evidence needed for that work and allowed by the relevant trust/privacy domain.

If a future settlement layer exists, complete semantic memory should remain off-chain. Only compact commitments or hashes may need to be anchored for verification/dispute purposes.

## Goal

The long-term objective is not merely "long-term memory." It is to reduce repeated semantic computation while keeping reusable claims scoped, evidence-backed, migratable and auditable.

## Open questions

- What qualifies a result for promotion into shared memory?
- Who/what is authorized to invalidate or supersede memory?
- How should conflicting memories be represented?
- How should retrieval balance relevance, freshness and context cost?
- Which memory should remain task-local versus reusable across projects?

## Publication and provenance

This document is published as a **time-stamped public record of CAH's exploratory design direction** and to invite technical discussion. It is intended to preserve the project's design chronology and provenance.

Publication does **not** claim exclusive rights over the underlying ideas, methods, algorithms or system concepts, and it does not imply that every element described here is implemented or committed to the roadmap.

Unless otherwise noted, original CAH text, diagrams and other project-authored material in this repository and its project-authored Issues/Discussions are made available under **AGPL-3.0-only** as stated in [NOTICE](../../NOTICE). That license governs copyrightable CAH expression; independent implementations of underlying ideas may exist.

Implementation-specific know-how, security-sensitive details, credentials, private runtime data and unpublished research may remain outside the public materials.

