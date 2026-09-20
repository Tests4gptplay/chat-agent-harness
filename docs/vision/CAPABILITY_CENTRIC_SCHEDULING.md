# Capability-Centric Scheduling Across Models, Hardware and Regions

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Role in the architecture

Multi-lane scheduling says that any compatible free Worker may claim READY work. Capability-centric scheduling defines **compatible** and chooses among eligible Workers/nodes.

Fixed lane names are useful for a small proof but should not become the public scheduling abstraction.

```text
READY task
    |
hard eligibility filters
    |
    +-- model/reasoning capability
    +-- required tools/software
    +-- OS / CPU / GPU / memory
    +-- authenticated browser/tool environment
    +-- trust/privacy domain
    +-- exclusive resource availability
    |
eligible capacity
    |
soft placement preferences
    |
    +-- data/artifact locality
    +-- warm semantic-memory affinity
    +-- latency
    +-- cost
    +-- reliability/reputation
    +-- current load
    |
claim + lease + fence
```

## Hard constraints versus soft preferences

A scheduler should distinguish requirements from optimizations.

Hard constraints may include:

- a required software/tool version;
- minimum model/tool capability;
- a private trust domain;
- a specific authenticated environment;
- an exclusive resource lease;
- enough local hardware capacity.

Soft preferences may include:

- warm Worker context;
- nearby artifacts/data;
- cheaper execution;
- lower latency;
- lower current load;
- historically reliable capacity.

A Worker that fails a hard constraint is not eligible even if it is idle.

## Capability manifests

Workers/nodes may advertise structured capability information, but scheduler-visible claims should be bounded and verifiable where practical.

A capability record may conceptually describe:

- reasoning/model class;
- tool adapters;
- operating system and software;
- hardware resources;
- data/artifact locality;
- trust/security domain;
- cost/latency class;
- current capacity and leases.

Exact schema is an implementation detail.

## Unknown capability should fail safe

The scheduler should not guess that a missing tool or resource exists.

An unresolved requirement should become an explicit state such as WAIT_RESOURCE / NEED_CONFIGURATION / BLOCKED rather than an optimistic dispatch that predictably fails.

## Worker affinity is optional

Warm context, cached artifacts or prior execution on the same task can reduce latency, so affinity can be scored as a preference.

It must not become identity. If another Worker is compatible and the original Worker is busy, dead or in the wrong trust/resource domain, the task should be migratable.

## Dynamic work stealing under capability constraints

Work stealing is allowed only after eligibility checks.

```text
idle Worker
  + READY node
  + hard constraints satisfied
  + resource lease available
        |
        v
fresh ownership / fence
        |
      execute
```

This prevents "free capacity" from being confused with "usable capacity."

## Cross-region placement

Once capacity spans multiple hosts or regions, placement may also consider:

- data sovereignty or privacy constraints;
- network latency/bandwidth;
- artifact replication cost;
- regional model/tool availability;
- failure-domain diversity for verification.

The scheduler should prefer moving compute toward data or scarce resources when that reduces total task span.

## Capability degradation

Some roles may prefer a high-capability model or tool but remain useful with a fallback.

The contract should distinguish preferred versus actual capability so the Planner can increase verification or change strategy when execution is degraded instead of treating every preferred-capability miss as task failure.

## Evolution

```text
fixed lanes
 -> capability manifests
 -> scheduler-visible requirements
 -> hard eligibility + soft scoring
 -> dynamic placement / work stealing
 -> multi-host placement
 -> multi-region heterogeneous fabric
```

The task/action/result contract should remain stable while placement backends evolve.

## Open questions

- Which capability claims must be attested rather than self-reported?
- How should cost, latency and reliability be balanced?
- How should memory/data locality be scored without creating sticky ownership?
- How should scarce-resource leases interact with work stealing?
- When is redundant placement justified for verification or fault tolerance?
