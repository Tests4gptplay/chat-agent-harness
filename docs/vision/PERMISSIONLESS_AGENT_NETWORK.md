# Toward a Permissionless Public Agent Network

**Status:** long-range exploratory research direction. Not implemented. Not a roadmap commitment. No token or blockchain dependency is implied by current CAH.

## Relationship to the trusted distributed fabric

The trusted distributed-agent model assumes Workers/nodes belong to a known administrative or trust domain.

A permissionless design asks a harder question:

> Can the same durable task, dynamic READY scheduling, capability placement, continuation migration and evidence model operate when available compute is supplied by mutually untrusted public nodes?

```text
durable task / READY contract
          |
public capability offers
          |
eligibility + lease/fencing
          |
bounded task packet
          |
untrusted execution
          |
result + evidence commitment
          |
verification / challenge / dispute
          |
optional settlement
```

The permissionless layer should extend the existing scheduler model rather than replace it.

## Public work stealing is a market claim, not blind trust

A public node may claim an eligible READY task only if:

- capability requirements are satisfied;
- the task is allowed to leave its trust/privacy domain;
- required resources are available;
- a fresh lease/fencing epoch is established;
- the node accepts the verification/settlement terms.

The node receives a bounded task packet, not the entire project memory.

## Proof of useful agent work

Traditional proof-of-work only proves expenditure of a defined computation. It does not establish that an arbitrary AI/agent result is correct or useful.

The harder problem is **verification of useful agent work**.

Different workloads need different verification mechanisms:

- deterministic work: tests, hashes, replay or independent validators;
- expensive deterministic/near-deterministic compute: redundant execution or sampled challenges;
- artifact-producing work: reproducible metadata, content hashes and independent inspection;
- semantic work: independent judges, explicit acceptance criteria, reputation and/or human acceptance;
- long-running work: optimistic acceptance with a challenge window where appropriate.

No single proof mechanism should be assumed to verify every semantic task.

## Consensus and blockchain are optional settlement primitives

A blockchain/consensus layer can establish shared agreement about compact public state such as:

- node identity or stake;
- task/result commitments;
- lease/fencing epochs;
- evidence hashes;
- challenge/dispute state;
- reputation updates;
- payments/rewards/penalties.

Consensus **does not** by itself prove that useful semantic work was performed correctly.

The core CAH scheduler, semantic memory and artifact system should not depend on blockchain merely to appear decentralized.

## What stays off-chain

Complete task state, full semantic memory, raw prompts, private constraints, logs and large artifacts should remain off-chain.

```text
on-chain / settlement layer
  identities / stake
  task commitments
  lease/fence commitments
  result/evidence hashes
  disputes
  payment/reputation
          |
          | content refs / hashes
          v
off-chain CAH state
  task DAG / READY state
  continuation checkpoints
  semantic memory
  evidence records
  source/logs
  artifacts
```

Git, object storage, content-addressed storage or other explicit durable backends may carry the off-chain state.

## Privacy and bounded disclosure

Permissionless scheduling creates a strict disclosure boundary.

A public node should receive only:

- the task contract needed for its assigned work;
- explicitly allowed inputs;
- minimum required memory/evidence refs;
- verification requirements;
- output/commitment contract.

Tasks containing credentials, sensitive data or private semantic memory may be ineligible for public placement.

## Sybil resistance and incentives

A permissionless network may need some combination of:

- stake;
- reputation;
- rate/cost mechanisms;
- hardware/capability attestation;
- identity history;
- slashing or reward reduction after proven faults.

The exact mechanism is an open design choice. A token is not a prerequisite.

## Dispute and referee model

When deterministic verification is unavailable, disagreement may trigger:

- independent re-execution;
- additional semantic judges;
- a referee set;
- human acceptance;
- challenge windows;
- evidence comparison.

Dispute resolution should operate on durable commitments and evidence refs rather than private Worker chat histories.

## Relationship to durable semantic memory

Verified results from public nodes may become candidates for shared semantic memory only after the required verification/reducer gate.

Untrusted outputs must not directly poison the canonical memory pool.

## Research principle

The goal would be a market for **verifiable AI-addressable capability**, not "blockchain for its own sake."

## Open questions

- How can semantic work be verified without making verification more expensive than the work?
- Which tasks are safe to disclose to public nodes?
- What should be committed on-chain versus merely content-addressed off-chain?
- How should node capability claims be attested?
- How should disputes affect reputation and settlement?
- Can privacy-preserving verification be introduced without making the system impractical?

## Publication and provenance

This document is published as a **time-stamped public record of CAH's exploratory design direction** and to invite technical discussion. It is intended to preserve the project's design chronology and provenance.

Publication does **not** claim exclusive rights over the underlying ideas, methods, algorithms or system concepts, and it does not imply that every element described here is implemented or committed to the roadmap.

Unless otherwise noted, original CAH text, diagrams and other project-authored material in this repository and its project-authored Issues/Discussions are made available under **AGPL-3.0-only** as stated in [NOTICE](../../NOTICE). That license governs copyrightable CAH expression; independent implementations of underlying ideas may exist.

Implementation-specific know-how, security-sensitive details, credentials, private runtime data and unpublished research may remain outside the public materials.

