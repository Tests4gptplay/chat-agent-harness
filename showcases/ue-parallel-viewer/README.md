# Viewer/Cook showcase — success first, then deeper problem discovery

[中文](README.zh-CN.md) · [Evidence](evidence.json) · [Rerun record](records/2026-09-28-rerun-record.md) · [Viewer client](viewer_client.py) · [Operation notes](AGENT_VIEWER.md) · [Downloads / scope](DOWNLOADS.md)

This showcase is best read as **two separate parts**.

**Part 1 is a completed success case.** A long-running managed task split into multiple workstreams, recovered from real failures, converged through repeated Worker generations, and ended with an accepted fresh Cook + native Viewer result.

**Part 2 starts only after that success.** The same task was deliberately pushed into a harder real-package/direct-preview problem. That follow-up uncovered provenance mistakes, deeper Unreal runtime boundaries, execution-wrapper failures, Runner blockage, and gaps in Helper recovery. Some reconnaissance succeeded; the later implementation was explicitly stopped incomplete.

That distinction matters: Part 2 does not retroactively make Part 1 unsuccessful.

---

# Part 1 — Successful long-running multi-workstream execution

## Goal

Rebuild the camera Cook and Viewer path from a fresh baseline and accept the result from **real native Viewer output**, not from API status alone.

The Parent Task became canonical at **11:25:48 JST on 28 September 2026**.

Planner immediately split the first wave into two useful, independent workstreams:

| Workstream | Goal |
| --- | --- |
| Fresh camera Cook | Re-establish the accepted Blender → Unreal import/build/Cook output from the real source. |
| Fresh Viewer foundation | Rebuild the Viewer baseline independently, with the interaction and inspection behavior needed for later integration. |

Those workstreams then joined in a third line:

| Integration line | Goal |
| --- | --- |
| Exact Cook → fresh Viewer | Open the real Cook output, load the intended mesh/material path, produce useful native views, and pass interaction + visual acceptance. |

This was therefore not one model turn trying one command. It was a long-running managed task with **multiple work lines, repeated execution/inspection cycles, and an explicit join condition**.

## The run did not follow a happy path

The fresh Cook first hit a Windows path-length problem. The task did not discard the accepted Blender source or restart the whole project. Only the task-owned UE working location was shortened, and the Cook continued from the useful frontier.

The Viewer integration then reached mechanical success before visual success:

- packages could be opened;
- the API responded;
- the intended asset could be discovered;
- native captures could be requested;

but the actual Lit output was still too dark to use.

That result was **rejected**.

The task continued through bounded diagnosis of stage/background setup, shader-library behavior, packaged runtime behavior, and platform-file routing. Several plausible explanations were tested and discarded instead of being preserved just because they sounded convenient.

The important loop was:

```text
execute
→ inspect the real result
→ reject false-positive success
→ narrow the fault
→ repair
→ execute again
→ inspect again
```

## Success boundary

Phase 1 was formally accepted at **16:27:18 JST**.

Total Parent elapsed time to acceptance:

**5h01m30s**

The accepted result included:

- a fresh Cook rebuilt from the accepted source;
- an external camera PAK whose bytes remained unchanged;
- successful package opening and preview loading;
- the intended camera mesh selected;
- one source shader library opened;
- interaction acceptance passed;
- four native Lit views inspected before acceptance.

The external camera PAK remained byte-identical at:

```text
e0486f3cbab4048ce7d76260d1e42f2d34177e64152f4c70dd7877825e191305
```

The exact native-capture byte sizes and SHA-256 hashes are preserved in [evidence.json](evidence.json).

### Accepted Phase-1 native captures

These are the actual four native Lit screenshots accepted by Planner in the 2026-09-28 rerun.

**Perspective**

![Accepted Phase-1 perspective Lit capture](images/phase1-perspective-lit.png)

**Front**

![Accepted Phase-1 front Lit capture](images/phase1-front-lit.png)

**Right**

![Accepted Phase-1 right Lit capture](images/phase1-right-lit.png)

**Below**

![Accepted Phase-1 below Lit capture](images/phase1-below-lit.png)

The images above are the exact rerun bytes whose sizes and SHA-256 hashes are recorded in [evidence.json](evidence.json).

One visible limitation is intentionally preserved: the accepted rerun background was black, not white. The camera itself was bright/readable and the below view was unobstructed, so the Phase-1 acceptance condition was satisfied without rewriting the pixels into a cleaner story.

## Why Part 1 is a useful success case

Part 1 demonstrates a concrete long-cycle pattern:

```text
one task input
    |
    +-- workstream A: fresh Cook
    |
    +-- workstream B: fresh Viewer
    |
    +-- integration: exact Cook → Viewer
             |
             +-- repeated diagnosis / repair
             |
             v
        accepted native result
```

The user did not need to manually issue a new “continue” prompt after every bounded failure.

Already accepted work was preserved. Failed hypotheses became evidence for the next decision. The task stayed anchored to the original acceptance criteria until the real Viewer output passed.

**Part 1 ends here. It is a successful case.**

---

# Part 2 — Recovery stress test under destructive external interference

Part 2 began only after the successful Phase-1 baseline had already been accepted.

Its most useful result is not another Viewer feature. It is that a much heavier follow-up load exposed a different problem class:

> **What happens when a Worker, while doing legitimate task work, produces an execution shape capable of blocking the execution system itself — or when the browser conversation surface stops producing usable output?**

Part 1 had already shown more than five hours of stable multi-workstream progress when failures remained bounded to the task itself. Part 2 pushed beyond that envelope and started hitting **system-level recovery failures**.

## Part 2A — technical investigation still behaved well

The first follow-up stage was still ordinary evidence-driven engineering.

A combined provider successfully exported the logical target asset, but provenance inspection later showed that default same-path selection had chosen a base-game PatchPak candidate rather than proving the intended MOD source.

Archive-specific loading then directly proved that the selected package member contained a real `USkeletalMesh` with:

- **25 material slots**
- **26 morph targets**
- **1 LOD**

Part 2A was formally accepted at **18:47:16 JST**, **7h21m28s** after Parent start.

This part still looked like Part 1: hypotheses could be wrong, Workers could be replaced, and the task could continue because the failures did not destroy the execution substrate.

## G9 — a Worker-authored mistake blocked the system

The recovery story changes at G9.

At **21:19:20 JST**, G9 reached Worker response-start. It launched an external staged runtime operation at **21:24:07** and wrote its own durable handoff at **21:25:42**.

The problem was inside the Worker-authored task-local execution wrapper: it synchronously nested the Viewer launcher without an outer watchdog/timeout and without reliable finally-style cleanup.

The result was not simply “this experiment failed”.

The external run remained stuck from:

**21:24:07 → 22:24:45 JST = 1h00m38s**

and occupied the primary Runner.

This is the important distinction:

```text
ordinary task failure
    → Worker records failure
    → next bounded decision

G9-class destructive failure
    → Worker-created execution blocks shared infrastructure
    → normal continuation path itself becomes impaired
```

G10 had already been dispatched at **21:26:37**, but did not reach response-start until **22:13:13** — a **46m36s** admission/transport delay — while the G9 external operation was still wedged.

That exposed a real weakness in the then-current CAH design: preserving semantic state was not enough if the physical execution/recovery path itself could be monopolized by a bad external action.

## Hotfixes got the task moving again

The task was not restarted from zero.

Several recovery changes were made while the same Parent Task remained alive.

G10 eventually completed its semantic work and wrote a durable result at **22:58:18**. During the semantic-sync hot upgrade, that already-durable result was recovered and canonically finalized at **23:18:11** without rerunning the Worker.

A later G11 dispatch exposed another lifecycle delay:

**23:31:42 → 00:01:31 JST = 29m49s**

The Worker-retention/binding path was repaired and the same dispatch eventually continued.

These are not Viewer achievements. They are evidence that Part 2 had turned into a live recovery-engineering exercise around CAH itself.

## G12 — the same class of danger appeared again, but redundancy helped

A later corrected hybrid-mode experiment launched at **00:21:01 JST** and again became stuck for roughly one hour:

**00:21:01 → 01:21:40 = 1h00m39s**

The difference this time was that backup execution capacity already existed.

While the primary Runner remained occupied, three consecutive Worker generations still reached response-start and durable continuation on backup capacity:

- **G13:** 00:26:13 → 00:31:45
- **G14:** 00:32:43 → 00:39:25
- **G15:** 00:40:41 → 00:45:43

That was the first real proof that redundant Runner capacity could keep the Task Cell alive while one execution lane was blocked.

## Helper recovery was still too weak

At **00:47:30 JST**, Planner reviewed the inherited blockage and correctly classified it as an operational incident rather than inventing another semantic experiment.

Helper reached response-start at **00:51:26**.

But the first Helper behavior still over-focused on diagnosis. It did not complete the full mutation → canonical closure → return-control sequence needed to restore the task automatically.

That triggered another human operational intervention.

The Helper role was strengthened during the live incident into a more active recovery owner. The resumed recovery completed the exact cleanup, persisted the incident result, and returned control to Planner. The original Planner reached response-start again at **02:00:32**, and later Workers continued from the preserved frontier.

This is exactly the kind of weakness Part 2 was useful for exposing.

## The system recovered far enough to continue real work

After recovery, the task did not merely idle.

Later generations continued normally again.

G20 provides a clean example:

- Worker response-start: **02:45:31**
- independent Build/Cook launched: **02:47:58**
- Worker durable handoff: **02:49:08**
- external job terminal success: **02:50:18**

The Worker semantic interval was **3m37s**. The external job lasted **2m20s**, including **1m10s after Worker handoff**.

So the hotfix path did restore enough lifecycle continuity for real semantic and machine work to proceed.

## G32 — a second recovery class: the web conversation itself stopped producing output

The run eventually exposed a different failure mode.

G32 was requested at **05:14:51 JST on 29 September** and reached exact response-start / runtime ACK at **05:15:11**.

Unlike G9, there was no active external computation that needed to finish.

Instead, the physical ChatGPT conversation itself became the blocker.

A direct page inspection at **07:36:47 JST** — more than **2h21m** after response-start — found:

- the exact G32 conversation still open;
- **0 rendered assistant messages**;
- a visible **Stop** control, meaning the page still appeared busy/running;
- no durable Worker Reply;
- no durable Result.

At **07:41:16 JST**, the canonical backend was still `RUNNING`.

So G32 exposed a second failure class:

```text
G9:
bad Worker execution
→ shared Runner / external execution path wedged

G32:
single browser conversation runs too long
→ web page remains busy / non-responsive
→ response-start exists
→ no consumable semantic output ever arrives
```

At that point the user explicitly stopped further P2-B work.

The stop did not abandon an accepted result: Part 1 and Part 2A remained durable and accepted. It stopped an unresolved recovery problem.

## What Part 2 actually demonstrates

Part 2 therefore should not be read as “the Viewer failed after 32 generations”.

It is a **recovery stress test** that found two concrete boundaries in the current system:

1. a Worker can generate externally destructive execution that blocks shared infrastructure; and
2. the browser/ChatGPT physical execution surface can itself stall for hours after response-start without producing semantic output.

The first class was partially mitigated during the run through backup capacity, semantic-sync recovery, lifecycle fixes, and a stronger Helper role.

The second class — physical conversation failure — is exactly why the recovery model now needs to treat the browser conversation as a disposable resource rather than assuming that response-start implies eventual semantic completion.

## Current development direction

This showcase directly feeds the current recovery work.

The system is being strengthened around:

- physical Worker/Planner conversation watchdogs;
- same-generation physical retry instead of falsely advancing semantic generation;
- deterministic stop/delete/reinject behavior for dead browser conversations;
- clearer separation between semantic failure and transport/physical failure;
- a stronger semantic Helper role that can perform bounded recovery mutations, close incidents durably, and return control to the correct role;
- redundant execution capacity so one blocked external job does not automatically freeze the whole Parent Task.

This work is still being debugged and validated. The showcase should therefore not claim that recovery is already complete.

The current evidence supports a narrower conclusion:

> **Part 1 already demonstrates stable long-running CAH behavior when failures remain inside the normal task/replanning envelope. Part 2 shows that the next major reliability problem is recovery from strong external interference — especially Worker-created execution wedges and long-lived browser conversations that stop responding.**

That is now one of the main engineering directions for CAH.

## Terminal state

```text
Part 1 / Phase 1   ACCEPTED

Part 2A            ACCEPTED INVESTIGATION
Part 2B            STOPPED AT RECOVERY LIMIT
Part 2C            NOT REACHED
```

The Part-2 stop does not reduce the Part-1 success claim.

It identifies the next system boundary to fix.

---

## Public artifacts

The exact Python client for the Viewer control surface is included as [viewer_client.py](viewer_client.py), together with corrected [operation notes](AGENT_VIEWER.md).

The four 2026-09-28 Phase-1 native captures are represented in [evidence.json](evidence.json) by their exact byte sizes and SHA-256 hashes.

The full UE-linked Viewer module itself is not distributed here. See [Downloads / scope](DOWNLOADS.md).

---

## What the two parts demonstrate

### Part 1

> **CAH can carry one long-running, multi-workstream engineering task through repeated execution, validation and repair until a real acceptance condition is reached.**

### Part 2

> **Once that successful baseline is pushed into a harder domain, the same durable task model can expose incorrect assumptions, runtime boundaries and weaknesses in the Harness itself without erasing the already accepted result.**

Part 1 shows the system completing a difficult task.

Part 2 shows what happens when you keep pushing after success and start discovering where the next problems actually are.
