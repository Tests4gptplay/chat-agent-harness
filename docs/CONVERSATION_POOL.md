# Managed ChatGPT Worker conversation pool

Status: implementation contract for the fixed Harness Worker Project.

## Goal

Keep ChatGPT worker conversations replaceable while Git remains the durable engineering memory.

The Harness uses one permanent ChatGPT Project as its pseudo-background Worker Project:

```text
project_key = g-p-examplelane00
root_url    = https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project
soft_limit  = 5
```

All automated create/rollover/delete operations are scoped to this exact Project. Human discussion Projects, including the separate chat-agent Project, are outside the managed deletion domain.

Example:

```text
managed: 001 002 003 004 005
rollover creates 006
006 proves takeover through Git
retire/delete 001
managed: 002 003 004 005 006
```

The temporary sixth slot is intentional. Never delete the old worker before the replacement has proven it can continue from Git.

## Authority boundaries

- Git state is canonical task/result/continuity state.
- Worker conversations are replaceable reasoning processes, not durable memory.
- `workerPoolState` is disposable local coordination state.
- `C:\CAH\runtime` mirrors high-frequency runtime events for diagnostics.
- Only conversations created and registered by the Worker runtime are eligible for automated retirement.
- Other ChatGPT Projects are never inferred to be managed, even if their UI looks similar.

Cleanup is never required for task correctness. If deletion fails, keep the extra worker and continue.

Explicit whole-lane conversation reset is a separate maintenance primitive, not ordinary 5+1 retirement. See `docs/LIFECYCLE_MAINTENANCE.md` for `lane_clear` scope, safety boundaries, and completion semantics.

## Fixed Worker bootstrap

CAH-managed Workers do not rediscover their own Harness repository on every rollover.

```text
canonical_repo = example-owner/cah-private
resume_state   = state/chatgpt.json
```

The root handoff message keeps the normal `GAH_WAKE` marker and appends a small `GAH_BOOTSTRAP` hint containing the fixed repository and state path. A replacement Worker should access that repository directly, not search repository names first and not repeat agent-side owner/permission confirmation for the same registered resource. Re-confirm only if direct access fails, scope changes, or a new external repository/resource is introduced. Platform-level connector permission/consent UI remains authoritative and is not bypassed.

`example-owner/cah-workload` is the registered first consumer only when canonical Harness state explicitly routes work there.

### Managed Worker hot start

The replacement bootstrap also carries `GAH_HOT_START profile=managed-worker-state-only-v1`. A normal managed Worker wake therefore reads `state/chatgpt.json` first and may proceed directly from a concrete canonical `next_action` without rereading AGENTS/README/repo-map/docs/source. `hot_start.router` plus `hot_start.expand_read_set` are conditional expansion pointers for failures, evidence conflicts, repository-detail needs, or shared-contract changes.

This is an execution-latency optimization only. It does not weaken Git authority, evidence gates, foreground lifecycle rules, or post-compaction rollover.

## Worker conversation record

```json
{
  "conversation_id": "...",
  "url": "https://chatgpt.com/g/g-p-examplelane00/c/...",
  "project_key": "g-p-examplelane00",
  "created_at": "...",
  "status": "handoff_pending|current|standby|retire_pending|retired",
  "managed_by": "gah-worker",
  "handoff_verified": true,
  "handoff_id": "pool-..."
}
```

## Proven UI paths

Worker creation is based on live-browser evidence.

The Worker Project root exposes one usable composer after foreground activation and hydration. The exact Chinese `aria-label` is preferred; on this exact fixed Worker Project root only, a unique visible large contenteditable textbox is accepted as the hydration fallback.

The global `create-new-chat-button` is not used because its href points to `/`.

Creation flow:

1. Open the fixed Worker Project root in a foreground tab.
2. Wait until the Worker root textbox is visible and unambiguous.
3. Worker-only policy may clear disposable prefill/draft text on this fixed Project root before inserting the handoff.
4. Insert the normal `GAH_WAKE v=1 id=pool-... project=git-agent-harness` marker plus the fixed bootstrap hint, select the actual visible send control using viewport/topmost hit testing plus composer proximity, and click it. Synthetic Enter is not a valid Worker submission path.
5. Confirm ChatGPT actually accepted the click; if the marker remains on the fixed root, re-resolve the current composer/send control and retry exactly once.
6. Require the same tab to become `/g/g-p-examplelane00[-slug]/c/<conversation-id>`.
7. Register the exact conversation in `workerPoolState` as `handoff_pending`.
8. Leave the new worker open for takeover.

Failure before a valid Worker conversation route causes local state rollback and closes the failed temporary tab.

Retirement entrypoint is also live-browser proven:

```text
conversation-options-button -> unique delete-chat-menu-item
```

Deletion is not enabled until the Git takeover handshake is durable.

## Rollover and takeover protocol

1. Current worker checkpoints meaningful progress to Git.
2. Local Worker runtime creates a replacement in the fixed Worker Project.
3. Replacement receives the compact `pool-*` `GAH_WAKE` plus the fixed bootstrap hint.
4. Replacement directly reads `example-owner/cah-private/state/chatgpt.json` and its `next_reads`.
5. Replacement performs at least one meaningful continuity read/write.
6. Replacement writes the exact `pool-*` id into `state/chatgpt.json:last_pool_takeover_id`, clears any stale predecessor `worker_rollover_request`, and records the verified continuation boundary.
7. Local runtime observes durable takeover acknowledgement and marks the new worker current / `handoff_verified`.
8. Only then may an older eligible managed worker become `retire_pending`.
9. If managed count exceeds 5, delete the oldest eligible worker through the exact conversation UI.
10. Confirm the intended conversation disappeared or became inaccessible before marking it retired.

If any step before durable takeover fails, no old worker is deleted.

## Automatic rollover after context compaction

The Worker itself is the compaction detector. The trigger is deliberately **post-compaction**, not a prediction of remaining context.

A Worker may request replacement only when its model-visible context positively shows that earlier raw conversation history has already been replaced by a system-generated summary/compaction capsule. Message count, conversation age, and guessed token usage are not accepted triggers.

After positive detection the Worker checkpoints Git and writes:

```json
"worker_rollover_request": {
  "handoff_id": "pool-current-worker-id",
  "reason": "context_compacted",
  "requested_at": "<ISO timestamp>"
}
```

Then it stops taking new substantive work. The local bridge reads canonical fetched Git state; the extension checks once per normal wake-poll interval. A request is actionable only when its `handoff_id` exactly matches the locally verified current Worker and the reason is exactly `context_compacted`. The runtime then starts the ordinary replacement → ACK → promotion → 5+1 retirement path.

The request is naturally idempotent: while a handoff is creating/pending, another create is rejected; after promotion, the predecessor request no longer matches the new current `handoff_id`. The replacement clears the stale request during its takeover write-back.

### Compaction handoff output packet

On positive `context_compacted` detection, persisting refs is not sufficient. Before requesting rollover, the outgoing Worker must also serialize a compact **handoff output packet** that contains both the current work frontier and the semantic memory needed to continue it.

The handoff packet has two distinct payloads:

1. **work checkpoint** — what is happening now;
2. **memory capsule** — what the next Worker must know without rereading the whole predecessor conversation.

Minimal work-checkpoint content:

```text
task_id
segment/round/action id
current state (RUNNING / WAIT_RESULT / VERIFY / BLOCKED / etc.)
what was completed
what is currently in flight
latest durable result/evidence refs
executor/run identity when relevant
known failure boundary
pending wake/result that must not be lost
exact next action
acceptance/verification gate for the next step
```

Minimal memory-capsule content:

```text
verified conclusions
decisions already made and why
user/task constraints that still apply
failed hypotheses / paths that should not be repeated
important assumptions and uncertainty
local terminology / identifiers required for continuation
small set of reusable semantic facts discovered during this Worker generation
```

The old Worker must output these as a durable handoff artifact, not merely leave them implicit in `state/chatgpt.json`. A recommended shape is:

```json
{
  "handoff_id": "pool-...",
  "task_id": "...",
  "generation": 3,
  "reason": "context_compacted",
  "work_checkpoint": { "...": "..." },
  "memory_capsule": { "...": "..." },
  "artifact_refs": ["..."],
  "evidence_refs": ["..."],
  "next_action": "...",
  "created_at": "..."
}
```

`state/chatgpt.json` should keep only the compact resume pointers and critical current frontier, for example `handoff_packet_ref`, `next_reads`, and `next_action`. The full compact handoff packet may live under a task-local durable path such as `memory/tasks/<task_id>/handoffs/<generation>.json` or another explicit canonical location.

The outgoing Worker should also make the packet **visible to the successor**, not only store it. The replacement bootstrap remains small, but it should identify the exact handoff packet and task continuation, for example:

```text
GAH_HANDOFF task=<task_id> generation=<n> packet=<ref>
```

The successor must read that packet before doing substantive work, verify that it matches the current task/generation/fence, acknowledge takeover, and only then continue. It should not reconstruct continuation solely from raw predecessor chat history.

This produces the intended boundary:

```text
context_compacted
  -> serialize current work checkpoint
  -> serialize semantic memory capsule
  -> persist handoff packet
  -> publish handoff_packet_ref + rollover request
  -> stop substantive work
  -> replacement Worker reads packet
  -> takeover ACK
  -> continue from exact work frontier
```

The memory capsule is not a transcript dump and should not duplicate all evidence. It is a distilled semantic cache. The work checkpoint is operational continuation state. Both are required because one answers **what are we doing now?** and the other answers **what do we already know?**.

## Safe deletion policy

Before deletion, all of the following must be true:

- candidate is recorded in `workerPoolState` with `managed_by=gah-worker`;
- candidate Project key exactly equals `g-p-examplelane00`;
- candidate is not current;
- a newer worker has `handoff_verified=true`;
- canonical Git state acknowledges the matching handoff id;
- candidate URL contains the exact stored conversation id;
- the page exposes exactly one `conversation-options-button`;
- its menu exposes exactly one `delete-chat-menu-item`.

Any ambiguity causes cleanup to be skipped. Do not use history-item indexes. Do not call undocumented ChatGPT internal APIs.

## CL ring mapping

The managed conversation pool and backend CL lifecycle use the same 5+1 generation-ring idea.

A logical task does **not** treat the six positions as six workflow stages. They are physical retention slots for replaceable Worker generations:

```text
g1 -> slot1
g2 -> slot2
...
g5 -> slot5
g6 -> temporary slot6
g6 durable takeover -> release oldest slot1
g7 -> reuse physical slot1 with generation=7 and a fresh fence token
```

The foreground may display these six positions as status lights, but Harness owns the mapping. A slot can be reused only after its prior generation is safely released; slot number alone is never an identity.

Recommended identity:

```text
logical worker identity = task_id + generation + fence_token
physical retention position = slot_id (1..6)
```

This prevents an old/late generation from corrupting a reused physical slot.

## Retention policy

```text
soft_limit = 5
temporary_rollover_capacity = 6
```

If cleanup fails, exceeding 5 is allowed. That is clutter, not an execution failure.

## Local events

Worker runtime events use the `worker.*` namespace, including:

```text
worker.rollover_requested
worker.root_ready
worker.resume_submitted
worker.chat_created
worker.create_error
worker.handoff_verified
worker.compaction_rollover_triggered
worker.compaction_rollover_error
worker.retire_pending
worker.chat_retired
worker.cleanup_skipped
worker.cleanup_error
```

Only meaningful continuity milestones are promoted to Git.

## Recovery

If local Worker pool metadata is lost:

- do not infer existing Project conversations are managed;
- start a new managed generation;
- leave unknown old conversations untouched;
- recover engineering state from Git.

This intentionally prefers harmless leftover conversations over accidental deletion.

## Multi-lane Project registry

The extension stores each execution Project in its lane registry.

Each logical backend lane owns exactly one configured ChatGPT Project:

```json
{
  "lane_id": "lane-00",
  "display_name": "CAH Sandbox0",
  "project_root_url": "https://chatgpt.com/g/g-p-.../project",
  "project_key": "g-p-...",
  "enabled": true,
  "worker_pool_state": {
    "soft_limit": 5,
    "current": null,
    "managed": [],
    "handoff": null
  }
}
```

Rules:

- parse and validate project_key from the exact Project root URL when a lane is registered;
- never use display_name as a safety identity;
- each lane has an independent 5+1 Worker-generation ring and independent handoff/fencing state;
- worker pool state is stored per lane under the registry;
- adding/removing a lane changes CAH registration only, never creates/deletes the ChatGPT Project itself;
- lane removal is blocked while RUNNING work or an unverified handoff exists;
- backend wake routing must name lane/project identity explicitly;
- foreground conversations are outside this registry.
