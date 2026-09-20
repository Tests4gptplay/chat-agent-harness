# Toward a Permissionless Public Agent Network

**Status:** long-range exploratory research direction. Not implemented. Not a roadmap commitment. No token or blockchain dependency is implied by current CAH.

## Question

If CAH can eventually schedule durable tasks across mutually trusted heterogeneous nodes, could the same abstraction extend to mutually untrusted public nodes?

A possible future model is:

```text
task contract
     |
public capability market
     |
leased execution
     |
result + evidence commitment
     |
verification / challenge / dispute
     |
optional settlement
```

## Consensus is not work verification

A ledger or consensus mechanism can establish agreement about task ownership, leases, result commitments and settlement state.

It does **not** by itself prove that an AI or hardware node performed useful work correctly.

The harder research problem is verification of useful agent work.

Possible mechanisms may differ by workload:

- deterministic work: tests, hashes, replay or independent validators;
- expensive computation: redundant execution, sampled challenges or dispute protocols;
- artifact-producing work: reproducible metadata, content hashes and independent inspection;
- semantic work: multiple independent judges, explicit acceptance criteria, reputation or human acceptance.

## Possible public-network primitives

A permissionless design might eventually need:

- node identity;
- capability advertisement;
- staking or other Sybil-resistance mechanisms;
- task leases and fencing epochs;
- result/evidence commitments;
- challenge windows;
- dispute/referee mechanisms;
- reputation;
- rewards and penalties;
- optional tokenized or conventional settlement.

These mechanisms should remain separate from the core scheduler unless public untrusted execution creates a demonstrated need.

## Storage split

Large artifacts and working data should remain off the settlement layer.

A future settlement/consensus layer, if used at all, should contain compact commitments such as identities, leases, hashes, disputes and payments. Git, object stores, content-addressed storage or node-local managed storage can hold the actual source, logs and artifacts.

## Research principle

The goal would be a public market for verifiable AI-addressable capability, not “blockchain for its own sake.”
