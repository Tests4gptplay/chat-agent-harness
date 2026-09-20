# Continuity protocol

This document defines how an agent survives chat/context/session boundaries without making conversation history the source of truth.

## Principle

Continuity is **read + proactive write-back**.

A useful harness must support both:
- **resume:** a new agent/session can recover the current verified state from Git;
- **checkpoint:** the current agent writes meaningful progress back before the present session becomes a dependency.

Git state is authoritative for engineering continuity. Account memory and chat history are convenience context only.

## State ownership

Each agent owns one replaceable resume cache:

```text
state/chatgpt.json
state/codex.json
state/<other-agent>.json
```

An agent may initialize its own file from `state/_template.json`. Once active, one agent must not overwrite another agent's live state.

State is not history. It should answer:
- what are we doing now?
- what was verified?
- what remains unresolved?
- where is the current fault boundary?
- what minimal files/results should the next session read?
- what is the next concrete action?

## Startup / resume

1. Read your own state if it exists.
2. Read only `next_reads`, active action/result references, and required task context.
3. Treat `verified` as valid unless new evidence contradicts it.
4. Do not re-run or re-diagnose completed stages merely because the chat changed.
5. If state and an executor result conflict, preserve both facts and mark the boundary uncertain until reconciled.

## Mandatory checkpoint triggers

Proactively refresh your own state after any of the following:
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

The agent writes/chooses an action. The executor performs mechanical work. The result carries compact evidence. The next agent turn consumes the result and decides whether to retry, change strategy, verify, finish, block, or ask the user.

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
read own state
-> read next_reads
-> inspect current result/action
-> resume work
-> keep writing meaningful progress back
```

The user should not need to restate previous logs, decisions, or milestones merely because the conversation changed.
