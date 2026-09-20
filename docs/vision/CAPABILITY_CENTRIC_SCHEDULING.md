# Capability-Centric Scheduling Across Models, Hardware and Regions

**Status:** exploratory discussion draft. Not a roadmap commitment.

## Motivation

Fixed lane names are useful for a small proof but should not become the public scheduling abstraction.

A mature scheduler should ask what a task requires, then choose compatible capacity.

```text
task requirements
      |
      v
capability / resource matching
      |
      +-- model capability
      +-- operating system / software
      +-- CPU / GPU / memory
      +-- local data and artifact locality
      +-- authenticated browser or tool environment
      +-- trust / privacy domain
      +-- cost and latency class
      +-- current leases and availability
      |
      v
placement
```

## Worker heterogeneity is expected

Different Workers may expose different reasoning quality, tools, local software, hardware, network reachability or cost. CAH should not require a homogeneous cluster.

A Worker is compatible because it satisfies the declared task contract and resource constraints, not because it has a particular permanent lane number.

## Locality and trust

Scheduling should eventually treat locality and trust as first-class constraints.

Examples:

- large local assets may make one node the natural execution site;
- sensitive work may be restricted to a private trust domain;
- authenticated browser state may create temporary placement affinity;
- a scarce GPU or application license may be represented as a capacity token or lease.

## Evolution

A plausible path is:

```text
fixed lanes
 -> capability manifests
 -> scheduler-visible resource requirements
 -> dynamic placement
 -> multi-host placement
 -> multi-region / heterogeneous model fabric
```

The task/action/result contract should remain stable while placement backends evolve.
