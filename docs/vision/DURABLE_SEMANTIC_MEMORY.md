# Durable Semantic Memory as a Compute Asset

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Core idea

Useful reasoning is work that has already been paid for.

Verified conclusions, failed hypotheses, decisions, discriminating tests, capability discoveries and evidence should not be treated as disposable chat history when they can safely reduce future reasoning.

```text
hot Worker context
      |
task checkpoint / handoff
      |
project-level verified memory / reusable procedures
      |
evidence and artifact references
      |
cold archive
```

Different layers have different latency, capacity and durability. Correctness should depend on durable explicit state, while hot conversational context acts as a cache.

## What should be reusable

Potential reusable semantic assets include:

- verified conclusions;
- known failure modes and disproven paths;
- accepted design decisions and their rationale;
- validated tool procedures;
- capability requirements;
- compact evidence-backed summaries;
- reusable task-graph patterns.

Unverified model speculation should not silently become durable truth.

## Scheduling implications

Memory locality may become a scheduling input, but never identity.

A Worker with warm relevant context may be preferred if it is available. If it is gone or busy, another compatible Worker should be able to reconstruct the task from durable state.

## Goal

The long-term objective is not merely “long-term memory.” It is to reduce repeated semantic computation while keeping every reusable claim traceable to current evidence and scope.
