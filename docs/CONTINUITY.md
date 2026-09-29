# Continuity protocol

This document defines how an agent survives chat/context/session boundaries without making conversation history the source of truth.

## Principle

Continuity is **read + proactive write-back**.

A useful harness must support both:
- **resume:** a new agent/session can recover the current verified state from Git;
- **checkpoint:** the current agent writes meaningful progress back before the present session becomes a dependency.

Git state is authoritative for engineering continuity. Account memory and chat history are convenience context only.

## Task Cell semantic continuity

Managed Task Cell continuity uses role-owned durable surfaces:

```text
Task Contract
= stable task goal / constraints / acceptance

Plan
= current formal future strategy

Planner Memory
= append-only parent-task semantic history

Worker Child Reply
= append-only child-local work + review + direction

Worker Handoff
= thin generation-switch continuation packet
```

A replacement Planner continues the same Planner Memory. A replacement Worker continues the same Child Reply and reads the current handoff packet when supplied.

Planner saves progress and outputs `handoff` when context has collapsed; normal `done` keeps its conversation. Worker saves its frontier in Child Reply and outputs `continue`; Harness prepares the replacement identities and any thin handoff refs. Helper records one incident result, outputs `done`, and is discarded after result receipt; the next Helper inherits durable incident refs rather than reusing the finished chat.

When a Helper recovery materially changes an active Child's operational execution state, continuity must not remain only in the short-lived Helper result. Carry a concise append-only `HELPER INCIDENT` breadcrumb into that Child's long-lived Reply, preserving the exact Runner/run/job/process disposition and post-recovery frontier. This breadcrumb does not become Worker-authored work or Planner direction; it exists so later Worker generations can recover the operational history that affects what is safe to resume or relaunch.

Conversation history is an execution context; these Git surfaces carry the continuity that must survive replacement.

## State ownership

For managed Task Cell roles, checkpointing means writing the role-owned Planner Memory, Child Reply or Helper result, not a shared state file per role or generation. Harness owns `state/chatgpt.json`, control/dispatch identities and lane topology. A normal semantic checkpoint must not overwrite that shared machine state. Helper's narrowly scoped operational recovery exception is defined in its role contract.

Outside managed role execution, a standalone agent may own one replaceable resume cache, for example:

```text
state/chatgpt.json
state/codex.json
state/<other-agent>.json
```

A standalone agent may initialize its own unowned cache from `state/_template.json`. Once active, one agent must not overwrite another agent's live state or a Harness-owned managed state file.

State is not history. It should answer:
- what are we doing now?
- what was verified?
- what remains unresolved?
- where is the current fault boundary?
- what minimal files/results should the next session read?
- what is the next concrete action?

## Startup / resume

1. In managed work, read the exact Task/role/continuity refs supplied by Harness and inspect machine state as needed without taking ownership of it. Otherwise read your own standalone state if it exists.
2. Read only `next_reads`, active action/result references, and required task context.
3. Treat `verified` as valid unless new evidence contradicts it.
4. Do not re-run or re-diagnose completed stages merely because the chat changed.
5. If state and an executor result conflict, preserve both facts and mark the boundary uncertain until reconciled.

## Mandatory checkpoint triggers

Proactively refresh the applicable role-owned durable surface (or your standalone resume cache) after any of the following:
- a meaningful production milestone;
- a PASS/FAIL/ERROR result that changes what is known;
- a decision expensive to reconstruct from scratch;
- a newly localized fault boundary;
- a new blocker or a blocker being cleared;
- a changed next step or handoff target;
- a user acceptance/rejection that changes the active candidate;
- before intentionally switching chat/session/agent;
- before a long or fragile operation when losing the current reasoning boundary would cause rework.

Do not checkpoint:
- greetings or ordinary discussion;
- wording-only edits;
- an identical retry with no new evidence;
- repeated inspection that confirms nothing new;
- speculative thoughts not yet affecting execution.

## Action/result loop

The first protocol uses four structured concepts:

```text
state -> action -> executor -> result -> state
```

The agent writes/chooses semantic work. The executor performs mechanical work. The result carries compact evidence. The next agent turn consumes the result and decides whether to retry, change strategy, verify, finish, block, or ask the user. In managed work Harness owns the machine state transitions below; they are not a response schema or fields for AI to echo.

Suggested phase flow:

```text
IDLE
NEED_AGENT
READY_TO_EXECUTE
EXECUTING
NEED_AGENT
VERIFY
DONE
BLOCKED
NEED_USER
```

`NEED_AGENT` means the environment has produced enough evidence for another reasoning turn.

## Evidence and fault boundaries

Prefer compact evidence such as:
- command/test identifier;
- exit code;
- failing test names;
- relevant error excerpt;
- artifact path/hash;
- validator verdict;
- screenshot/preview reference;
- first known failing stage.

Do not dump entire logs if a reducer can return the relevant subset.

Once stages are verified, carry them forward explicitly. A later failure should resume at the first unverified/failing boundary, not restart the whole pipeline.

## New-chat behavior

A new chat should behave like a lightweight `git pull`:

```text
read bound Task + role continuity (or own standalone state)
-> read next_reads
-> inspect current result/action
-> resume work
-> keep writing meaningful progress back
```

The user should not need to restate previous logs, decisions, or milestones merely because the conversation changed.
