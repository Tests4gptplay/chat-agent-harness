# Foreground contract

Foreground is the human-facing owner of a CAH task.

## Direct bounded work

Foreground may dispatch or complete a small, clear task directly and report its durable result.

## Managed Task Cell work

Foreground records the Task Contract and hands it to the current Planner. After durable Planner takeover, routine phase-to-phase coordination belongs to Planner.

Foreground re-enters for:
- changed user intent;
- WAIT_USER / NEED_USER;
- explicit override or cancellation;
- final user-facing delivery;
- control-plane repair when the managed path is unavailable.

Foreground presence is not a scheduler heartbeat.

## Durable state

Git remains authoritative. Foreground reports progress and completion from canonical task/CL/result evidence.

Worker/executor transport ACK and semantic task completion are separate. A delivered wake is transport evidence; accepted result/CL state is task evidence.

## Final delivery

Managed work reaches Foreground after Planner produces a durable final delivery event. Foreground reads the final result/evidence and reports it to the user.

Browser foreground notification is a convenience/recovery transport, not task truth.
