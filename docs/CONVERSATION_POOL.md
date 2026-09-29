# Managed ChatGPT Worker conversation pool

Status: active Playwright Worker-pool contract.

## Purpose

Worker conversations are replaceable execution contexts. Git holds durable task state.

Each logical task owns an independent Worker pool inside each physical lane:

```text
pool identity = owner_task_id + owner_control_epoch + lane_id
soft retained conversations = 5
temporary replacement capacity = 6
```

The physical ChatGPT Project is shared transport. Pool state remains task-owned.

## Current Worker creation

A new Worker conversation starts with **real work**.

```text
claimed semantic wake
-> open registered Worker Project root
-> submit the real GAH_WAKE / GAH_DISPATCH message
-> ChatGPT creates the conversation
-> assistant response starts
-> Harness accepts the exact dispatch
-> the same admission commit binds last_pool_takeover_id = worker_ref
-> local pool marks that conversation current
```

There is no bootstrap-only Worker takeover turn.

If the wake carries an attachment, Playwright attaches it before sending the first real message.

## Canonical Worker owner

`state/lanes.json` stores task-pool owner identity:

```json
{
  "owner_task_id": "...",
  "owner_control_epoch": 1,
  "last_pool_takeover_id": "pool-...",
  "worker_rollover_request": null
}
```

`last_pool_takeover_id` is machine-owned. Harness updates it at exact response-start admission.

The Worker does not write an ownership ACK.

## Worker conversation record

The local Playwright pool tracks physical conversations:

```json
{
  "conversation_id": "...",
  "url": "...",
  "lane_id": "lane-00",
  "owner_task_id": "...",
  "owner_control_epoch": 1,
  "status": "current | standby",
  "handoff_id": "pool-...",
  "handoff_verified": true,
  "worker_watchdog": {
    "kind": "WORKER_HELPER",
    "state": "ARMED | WORKER_HELPER_REQUESTED | DISARMED",
    "timeout_seconds": 1800,
    "started_at": "...",
    "deadline_at": "...",
    "child_task_id": "...",
    "backend_cl": "...",
    "dispatch_id": "...",
    "dispatch_generation": 32,
    "fence_token": "...",
    "wake_id": "..."
  }
}
```

`handoff_verified=true` is local mechanical lifecycle state meaning the conversation has completed response-start admission for its current Worker ref.

For a dispatch-aware managed Worker, the same successful response-start/admission arms `worker_watchdog` for a fixed 30 minutes. This deadline belongs to the physical Worker attempt and is not extended by semantic lease renewal. If the exact Worker is still unresolved at the deadline, Playwright invokes the distinct Worker Helper path directly from this retained binding; it does not need to rediscover the dispatch from the page DOM.

## 5+1 retention

A task retains up to five managed Worker conversations per task/epoch/lane pool. This is a cap on retained history, not a requirement to create five before continuing or finishing.

On replacement:

```text
g1 g2 g3 g4 g5
        +
       g6
-> g6 becomes current
-> prior current becomes standby
-> oldest eligible standby retires
-> retained count returns to 5
```

Conversation retention is physical history. Logical Child state remains in Git.

Planner review is a separate cadence: g5, g10, g15 and later multiples of five trigger an intermediate review only when that generation requests `continue`. After approval, even an unchanged Task/direction resumes the pending continuation. A `complete` at g7 goes immediately to Planner acceptance; it does not wait for g10.

Deletion failure may leave more than five conversations temporarily; semantic execution can continue.

## Context-compaction rollover

The outgoing Worker records the useful frontier and artifact refs in Child Reply and outputs `continue`. Harness publishes the bound `worker_rollover_request` and prepares any thin handoff packet. Worker does not write global lane/pool state or repeat its Reply merely to generate a mechanical request. g1 can request g2 immediately when context replacement is needed.

Harness performs the replacement mechanically:

```text
rollover request
-> complete handoff packet into a fresh backend dispatch/fence
-> create replacement conversation with that real continuation wake
-> replacement reads GAH_HANDOFF + Child Reply
-> response-start binds the replacement Worker owner
-> rollover request clears
-> replacement continues work immediately
```

No semantic takeover-ack turn is inserted.

## Handoff packet

The handoff packet is a thin, Harness-prepared generation-switch reference surface, not an extra long semantic report demanded from Worker.

It carries immediate continuation state such as:

- current work frontier;
- in-flight action/result identity;
- relevant artifact/evidence refs;
- exact next action;
- acceptance gate for that continuation.

Long-lived child semantics live in Worker Child Reply; Harness reuses those refs rather than making AI duplicate the ledger or invent envelope fields.

## Retirement

Harness retires only conversations recorded in the exact task-owned pool.

The current conversation remains active. Older admitted standby conversations become retirement candidates when the pool exceeds its retention limit.

Unknown Project conversations remain outside task-owned pool retirement.

After Planner's final `complete`, Harness also cleans every remaining conversation in the exact completed task/epoch's pools through Playwright before resetting their bindings. The ordinary retention rule does not preserve the final current Worker beyond task cleanup. Preserve the Project container, other tasks and unknown conversations.

## Recovery

Git reconstructs engineering continuity.

If local pool metadata is lost, CAH can start a fresh managed Worker conversation from the current canonical dispatch/Child state while leaving unknown old conversations outside automatic retirement.
