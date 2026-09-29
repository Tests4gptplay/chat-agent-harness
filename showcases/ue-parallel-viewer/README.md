# Viewer/Cook rerun — one task across many AI conversations

[Evidence](evidence.json) · [Rerun record](records/2026-09-28-rerun-record.md) · [Viewer client](viewer_client.py) · [Operation notes](AGENT_VIEWER.md) · [Downloads / scope](DOWNLOADS.md)

This showcase is a sanitized record of the CAH Viewer/Cook rerun that began on 28 September 2026 and continued into the next day.

The terminal result is intentionally split:

- **Phase 1 — fresh Cook + fresh Viewer integration: ACCEPTED**
- **Phase 2A — real package/dependency reconnaissance: ACCEPTED**
- **Phase 2B — native direct-preview implementation: STOPPED INCOMPLETE**
- **Phase 2C — final direct-preview acceptance: NOT REACHED**

The user stopped further Phase-2B work after Worker generation 32 stalled without a durable semantic result. The case does not turn that stop into a synthetic success.

## Why this run matters

The central question was not only whether CAH could build and inspect a Viewer. It was whether one engineering task could preserve its verified frontier while Planner and Worker conversations were replaced, external jobs continued independently, technical hypotheses were rejected, Runner capacity failed, and the Harness itself was repaired.

In this run, the durable object was the **task state in Git**, not one continuously surviving conversation.

```text
one task input
→ planning
→ execution
→ inspection
→ failure
→ diagnosis
→ revised direction
→ execution again
→ acceptance
```

That loop crossed multiple Worker generations, Planner handoff, external Build/Cook jobs, backup Runner use, and Helper recovery.

## Phase 1 — accepted from real native output

The Parent Task started at **11:25:48 JST**. Planner dispatched fresh camera Cook and fresh Viewer reconstruction as separate first-wave work.

The Cook path recovered from a Windows path-length failure without rebuilding accepted work. The Viewer path reached mechanical package/API success before visual success, but dark native output was rejected. Several shader, staging, packaging and platform-file hypotheses were tested and discarded before the accepted path was reached.

Phase 1 was formally accepted at **16:27:18 JST**, **5h01m30s** after Parent start.

Accepted facts include:

- external camera PAK remained byte-identical at SHA-256 `e0486f3cbab4048ce7d76260d1e42f2d34177e64152f4c70dd7877825e191305`;
- package opened and preview loaded;
- one source shader library;
- intended camera mesh selected;
- interaction acceptance passed;
- four native Lit captures were inspected before acceptance.

Their exact sizes and hashes are preserved in [evidence.json](evidence.json).

The accepted rerun background was visibly black. The model itself remained bright/readable and the below view was floor-free; this case does not rewrite that visual limitation.

## Phase 2A — evidence overturned the convenient interpretation

The next phase examined a real package/dependency problem.

A combined provider successfully exported the logical target path, but provenance inspection later showed that default same-path selection had chosen a base-game PatchPak candidate rather than proving the selected MOD source.

An archive-specific probe then directly loaded the exact MOD member and proved a real `USkeletalMesh` with:

- **25 material slots**
- **26 morph targets**
- **1 LOD**

Phase 2A was formally accepted at **18:47:16 JST**, **7h21m28s** after Parent start.

The practical rule changed: later Viewer work had to intentionally select the desired package member rather than trust default equal-order provider behavior.

## Task continuity across replacement

Planner G1 eventually handed off after the accumulated history became large. Planner G2 resumed from the same Task, Plan, Memory, Plan Note, Child Reply, Result and evidence surfaces rather than rediscovering the task from chat history.

Worker turnover behaved the same way: successors continued from durable Child frontiers instead of repeating already accepted work.

Generation 20 gives a clean timing example:

- Worker semantic interval: **3m37s**
- external Build/Cook run: **2m20s**
- external execution after Worker durable handoff: **1m10s**

The machine job continued after the Worker had already handed off.

## Runner and Helper recovery

Later, a malformed execution wrapper occupied the primary Runner for about an hour. Backup capacity then carried **three consecutive Worker generations**, preserving the task long enough to return the incident to Planner.

Planner escalated the infrastructure problem to Helper. The first Helper behavior was insufficient, so the recovery contract was improved during the live task. The resumed Helper completed the bounded recovery, returned control to Planner, and later Worker generations continued from the preserved frontier.

This was not a hands-off perfect run. Human operational intervention happened, and the Harness changed because of failures exposed by the task.

## Phase 2B — progress without a false finish

Phase 2B reached real native container/package-store and cooked-runtime work, but it never reached its final direct-preview acceptance boundary.

The last admitted generation was **G32**. Its physical response started, but no durable semantic result followed. The user explicitly stopped the task.

Therefore the only accurate terminal label is:

```text
P2-B = STOPPED_INCOMPLETE
```

## Public case artifacts

The exact Python client for the Viewer control surface is included as [viewer_client.py](viewer_client.py), together with corrected [operation notes](AGENT_VIEWER.md).

The public repository also preserves original native Viewer screenshots in its pre-clean history. The four newer Phase-1 rerun captures are represented here by their exact byte sizes and SHA-256 hashes; they are not silently substituted with older images.

The full UE-linked Viewer module itself is not distributed here. See [Downloads / scope](DOWNLOADS.md).

## What this demonstrates

This case does not prove universal autonomy or universal UE/game compatibility. It does demonstrate a narrower property:

> **A real engineering task can live longer than any single AI conversation, preserve a verified execution frontier in Git, and continue through multiple planning, execution, failure-analysis and recovery cycles without requiring the user to manually prompt every next step.**

The Viewer matters. The timeline matters more.
