# Development workflow

CAH uses lightweight Git discipline for routine maintenance and a tracked workflow for defects whose blast radius or recovery cost justifies durable coordination.

The goal is to avoid two bad extremes:

- opening an Issue for every typo or obvious local repair;
- silently patching scheduler, state, safety, or cross-component failures without a reproducible record.

## Defect classes

### Incidental fix

A defect may be fixed inline with the current task when **all** of the following are true:

- it is local and low-risk;
- the affected boundary is clear;
- fixing it does not materially change the current task plan;
- it does not alter scheduler/canonical-state semantics, safety boundaries, public contracts, or cross-component coordination;
- it can be fully tested or otherwise verified inside the current task;
- preserving a separate failure/recovery history would add little engineering value.

Typical examples include a typo, a missing quote, an obvious include/import error, a narrow selector adjustment, or another mechanical defect discovered while completing the task.

Inline does not mean unverified: make the repair, run the relevant check, and keep the commit/evidence needed to support the task result.

### Tracked defect

A defect requires a GitHub Issue before substantive repair when **any** of the following is true:

- it stalls, or can indefinitely stall, a task;
- it corrupts or desynchronizes canonical state;
- it crosses components or execution domains;
- it affects scheduler, dispatch, fencing, handoff, rollover, liveness, recovery, data integrity, or safety behavior;
- it changes a public/shared interface or contract;
- it can cause durable evidence to become misleading;
- the repair requires a dedicated multi-step investigation, migration, or regression plan;
- the repair itself has become an independent task;
- fixing it materially changes the active task's execution plan.

Use blast radius and recovery cost, not line count, as the classification criterion.

When uncertain, prefer a tracked defect if failure could leave the system in a plausible-looking but incorrect durable state.

## Standard tracked-defect flow

~~~text
reproduce / collect evidence
        ↓
GitHub Issue
        ↓
dedicated fix branch
        ↓
implementation + focused tests
        ↓
regression against the original failure when practical
        ↓
Pull Request linked with Fixes #<issue>
        ↓
review / required checks
        ↓
merge
        ↓
close only when acceptance evidence is durable
~~~

The Issue should contain enough information for another Worker to resume without raw chat history:

- observed behavior;
- expected behavior;
- affected task/dispatch/generation/fence when applicable;
- exact failure boundary;
- durable evidence refs;
- known reproduction path;
- proposed acceptance criteria.

Do not require a complete root-cause theory before opening an Issue. Facts and reproduction come first; diagnosis can evolve in comments or the PR.

## Branch and PR discipline

For tracked defects:

- repair work must not be developed directly on `main`;
- use a dedicated branch with a descriptive name such as `fix/<topic>`;
- keep unrelated cleanup out of the repair unless it is required by the fix;
- link the PR to the Issue with `Fixes #N` (or an equivalent GitHub closing keyword);
- the PR description must state the failure being fixed, the implementation boundary, validation performed, and any residual limitation;
- merge only after the original acceptance criteria are satisfied or explicitly narrowed with durable rationale.

A documentation/process change may use a normal documentation branch/PR without first opening an Issue unless the change itself is correcting a tracked defect.

## Preserve real failure evidence

Do not erase or replace a valuable failing run merely to produce a clean success story.

When practical, validate a tracked defect by resuming or replaying the **same failing workload/checkpoint** that exposed it. Preserve:

- original failure timestamps;
- prior dispatch/fence identities;
- blocker/error artifacts;
- recovery transition;
- final passing evidence.

A clean new run may supplement recovery evidence, but it must not be used to hide failure of the original interrupted run when recovery is part of the claim.

## Relationship to the active task

An incidental fix remains subordinate to the task that exposed it.

A tracked defect becomes its own engineering unit. The active workload may be:

- safely paused at a durable checkpoint;
- explicitly blocked on the Issue;
- resumed after the fix;
- superseded only when the user or canonical task contract actually changes.

Do not silently restart from scratch merely because a defect made the existing run inconvenient.

## Emergency containment

If an active defect presents an immediate destructive or safety risk, deterministic containment may precede normal Issue/PR ordering when necessary to stop harm.

After containment:

1. preserve the evidence;
2. open a tracked Issue promptly;
3. move the durable repair through a dedicated branch and PR;
4. do not treat emergency containment itself as the final verified fix.

This exception is for containment, not convenience.

## Agent decision rule

Before repairing a newly discovered defect, ask:

> Can this be fixed locally, safely, completely verified inside the current task, and without changing the task plan or any shared control contract?

If yes, it may be an incidental fix.

If no—or if the repair has become a separate task—open a tracked Issue first.
