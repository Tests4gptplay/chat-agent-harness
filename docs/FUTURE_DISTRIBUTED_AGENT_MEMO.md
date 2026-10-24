# Distributed architecture

CAH separates semantic reasoning, durable task state and machine execution.

## Roles and state

- Foreground interprets the user's intent and presents results.
- Planner coordinates the logical task and independent Child outcomes.
- Workers execute bounded Child work and write durable replies/results.
- Helper handles a bounded operational incident.
- Harness, Bridge and the browser Host reconcile Git state, carry machine-owned identity and manage transport and conversation lifetimes.

Git stores the task contract, Planner Memory, Worker Child Replies and Result/Outcome. Conversations are replaceable contexts around that record.

## Useful parallel work

Split work when its outputs are independently useful and its resources permit concurrency. Shared files, CPU/GPU/memory and external application state determine placement. Preserve the task-owned lineage when browser lanes are shared.

Keep consecutive deterministic steps in one bounded execution request until an AI decision is needed. Use existing Tool and workflow surfaces. Durable dependencies and result references connect Child outcomes to the parent task.

## Placement and continuity

Two normally online Managed Runners provide execution slots. The second retains the dedicated backup route. The independent Short Task channel carries small direct work and operational recovery.

Checkpoint useful progress before replacing a conversation. Continue from the existing task contract, memory and replies. Resolve a reported mechanical error through the current Helper path while retaining task-level intent.

## Current implementation references

- [Role model](task-cell/README.md)
- [Scheduler](SCHEDULER_MODEL.md)
- [Continuity](CONTINUITY.md)
- [Worker generations](WORKER_GENERATIONS.md)
- [Tools](TOOL_SYSTEM.md)
- [Skills](SKILL_SYSTEM.md)
- [Installation and directory configuration](../installation/README.md)

These subsystem contracts describe the maintained implementation and its execution interfaces.
