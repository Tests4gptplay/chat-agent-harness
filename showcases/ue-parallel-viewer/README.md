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

## Two Viewer implementations, side by side

The earlier Viewer showcase is preserved as a separate historical case:

**[Historical Viewer showcase — 2026-09-20](historical-2026-09-20/README.md)** · **[中文](historical-2026-09-20/README.zh-CN.md)**

The historical case is valuable precisely because the accepted 2026-09-28 P1 Viewer did **not** follow the same runtime path.

| | Historical Viewer — 2026-09-20 | Fresh P1 Viewer — 2026-09-28 |
| --- | --- | --- |
| Representative native view | ![Historical Viewer perspective](historical-2026-09-20/images/perspective.png) | ![Fresh P1 perspective](images/phase1-perspective-lit.png) |
| Starting point | Existing paused Viewer/source was reused, repaired, integrated and tested | Historical GAHQuickLook was **reference-only**; a new source/build tree was mandatory |
| Runtime host | Installed UE 5.6.1 **Editor runtime in `-game`**, DX11/SM5 | Fresh packaged **`WITH_EDITOR=0` standalone Game runtime** |
| Standalone package | No — explicitly not a standalone executable | Yes — Viewer was packaged as a standalone Game runtime for the acceptance path |
| Viewer packaging/runtime mode | Editor-hosted project/runtime with Pak platform mount/catalogue/shader-library repair | Viewer packaging was changed to **Pak-only / no-IoStore** while the external camera PAK remained unchanged |
| Input path | Classic PAK fixture dynamically handled inside the Editor-hosted `-game` runtime | Real fresh external camera PAK consumed by the packaged Viewer |
| Shader path | Runtime shader-library support repaired inside the Editor-hosted Viewer path | Packaged runtime opens the real external monolithic camera shader library after platform-file/shader-routing diagnosis |
| Evidence provenance | Existing source reused/repaired; historical run included supervised fixes and a synthetic turnover test | Old binaries/captures/Cook/PASS labels were forbidden as current evidence; fresh source/build, fresh Cook integration and fresh native captures were required |
| Acceptance meaning | Demonstrated that the original architecture could become a usable inspection tool | Demonstrated a **fresh reconstruction on a different runtime/packaging architecture** |

This is why keeping both cases is useful: the 2026-09-28 P1 result is not merely a newer screenshot from the same Viewer implementation. It reaches a similar user-facing goal through a materially different runtime architecture.

## How this differs from the historical Viewer

Part 1 did not copy an old Viewer binary or replay an old accepted project.

The original Phase-1B contract explicitly constrained historical GAHQuickLook to **source/design reference only**. Old binaries, captures, Cook output and PASS labels were not allowed to count as current evidence.

The P1 path was therefore a **fresh reconstruction**:

- create a new Viewer source/build tree for this run;
- build it again against the installed UE 5.6.1 environment;
- reuse only useful historical concepts where appropriate — package-group resolution, staged mount/catalog/preview reporting, loopback API, native viewport, and explicit shader/dependency diagnostics;
- reimplement and verify the required input state machine for this run: held-drag orbit, Shift+drag pan, immediate capture release on mouse-up, Esc release, wheel zoom, and normal idle cursor freedom;
- recreate inspection-oriented scene defaults with unobstructed low/bottom viewing;
- integrate the **fresh Cook produced by this run**, rather than an old package, placeholder or historical PASS.

The integration phase then showed that even the older loading approach could not simply be carried forward.

The initial dynamic-loader path hit a Pak mount/package-discovery boundary on the fresh Cook. A second attempt — staging the container set into `Content/Paks` and launching through the older assumption — still failed to expose the intended asset. The runtime path was therefore changed again.

The accepted P1 path ultimately used a **fresh packaged Viewer in Pak-only / no-IoStore mode**, opened the real external camera shader library, selected the actual camera mesh, and then passed native-pixel and interaction acceptance.

So the lineage is better represented as:

```text
historical Viewer
    ↓
source / UI / API / architecture reference only
    ↓
fresh source/build tree
    ↓
fresh interaction + inspection implementation
    ↓
fresh UE5.6.1 build and smoke
    ↓
fresh Cook integration
    ↓
older loader assumptions fail
    ↓
runtime path reconstructed
    ↓
Pak-only fresh Viewer + real external PAK
    ↓
native pixel + interaction acceptance
```

This was not “invent every concept from zero”; prior engineering knowledge was intentionally reused. But the accepted P1 Viewer was **not** an old binary reuse, an old-result replay, or a simple project copy-and-rebuild. Its source/build tree, integration evidence and final runtime path were reconstructed and revalidated for the rerun.

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

## Stability under a real task — not a dry run

Before the recovery failures, one point should be made explicit: this was **not a synthetic soak test, dry run, or scripted fault-injection benchmark**.

One concrete engineering task was submitted and CAH kept working on it: fresh Blender/UE asset work, native Viewer reconstruction, real Build/Cook operations, package/runtime investigation, native validation and later direct-preview research.

The useful stability metric is therefore not “the browser stayed open for N hours”. It is how long the system kept advancing a real task before human operational recovery was required.

From Parent Task start at **11:25:48 JST** to the first system-level G9 wedge at **21:24:07 JST**:

**9h58m19s**

elapsed without a recorded human operational intervention.

There were ordinary engineering failures during that span, but CAH handled them inside the normal Worker/Planner loop without requiring the user to step in and repair the Harness.

## G9 — first system-level failure after almost ten hours unattended

At **21:19:20 JST**, G9 reached Worker response-start. It launched an external staged runtime operation at **21:24:07** and wrote its own durable handoff at **21:25:42**.

The Worker-authored execution wrapper synchronously nested the Viewer launcher without an outer watchdog/timeout and reliable cleanup.

The external run remained stuck from:

**21:24:07 → 22:24:45 = 1h00m38s**

and occupied the primary Runner.

This was the first failure class that escaped the normal task/replanning envelope:

```text
ordinary task failure
→ Worker records evidence
→ Planner / successor continues

G9 system-level failure
→ Worker-created execution blocks shared infrastructure
→ normal continuation capacity is impaired
```

G10 had already been dispatched at **21:26:37**, but did not reach response-start until **22:13:13**, a **46m36s** admission/transport delay.

## First human intervention — recovery infrastructure hotfix around G10

The first human operational intervention followed the G9 incident and was completed around the G10 cycle.

It strengthened recovery infrastructure rather than merely fixing one Viewer experiment. Changes made during that recovery period included mechanisms that later mattered directly:

- backup / redundant Runner capacity;
- semantic-sync and durable-result reduction;
- Worker retention/binding repairs;
- Host/lifecycle recovery fixes.

G10 itself eventually produced a durable result at **22:58:18**, and the new semantic-sync path canonically finalized that already-existing result at **23:18:11** without rerunning the Worker.

The value of this first intervention became clearer when the system was stressed again.

## G12 — another destructive blockage, but intervention #1 now paid off

A later corrected hybrid-mode experiment launched at **00:21:01 JST** and again became stuck for roughly one hour:

**00:21:01 → 01:21:40 = 1h00m39s**

This time the Task Cell did not lose all forward motion.

While the primary Runner remained occupied, three consecutive Worker generations continued on backup capacity:

- **G13:** 00:26:13 → 00:31:45
- **G14:** 00:32:43 → 00:39:25
- **G15:** 00:40:41 → 00:45:43

This is the clearest proof that the first intervention had already improved resilience:

> **G12 again blocked the primary execution resource, but G13–G15 still advanced the same Parent Task instead of the whole Task Cell stalling as it had around G9.**

The system had moved from:

```text
one blocked Runner
→ Parent Task effectively loses forward progress
```

toward:

```text
one blocked Runner
→ backup capacity carries later Workers
→ durable semantic frontier keeps moving
```

## After G15 — the remaining weakness was Helper recovery, not the Worker

G15 itself did **not** stall.

It wrote its durable result normally at **00:45:43**.

Then:

- Planner response-start: **00:47:30**
- Planner writes `WAIT_HELPER`: **00:50:48**
- Helper request staged: **00:51:07**
- Helper response-start: **00:51:26**

The first Helper behavior could diagnose the inherited G12 operational incident, but it could not yet complete the full recovery loop:

```text
required mutation
→ exact cleanup
→ durable incident/result
→ canonical closure
→ return control
```

That was the trigger for the **second human intervention / Helper Hotfix 2.0**.

## Second human intervention — Helper Hotfix 2.0

The second intervention strengthened Helper from a diagnosis-heavy role into a closure-complete recovery owner.

The resumed Helper could now:

- execute bounded recovery mutations under Planner authority;
- cancel/clean the exact incident-owned external work;
- verify Runner cleanup;
- persist the durable incident/result;
- complete canonical closure;
- explicitly return control to Planner.

The same incident then completed:

- Helper result: **01:54:32**
- canonical Helper completion: **01:59:54**
- `helper_result` enqueued: **02:00:04**
- original Planner response-start again: **02:00:32**
- Planner durable done: **02:02:47**
- G16 response-start: **02:04:22**

From there, G16 onward continued automatically.

## Hotfix 2.0 → G31: another 3h13m12s without further Helper failure

After the original Planner resumed at **02:00:32**, the task continued through G16, G17, G18, G19 and the later P2-B generations without another recorded Helper failure.

G31 wrote its durable handoff at **05:13:44 JST**.

That gives a post-Hotfix-2.0 autonomous interval of:

**02:00:32 → 05:13:44 = 3h13m12s**

with no further human recovery intervention and no second Helper incident.


## Overall task endurance before the final physical-page failure

The larger number is more important.

From Parent start:

**2026-09-28 11:25:48 JST**

to G31 durable handoff:

**2026-09-29 05:13:44 JST**

the same concrete Parent Task remained alive and continued producing durable engineering progress for:

# **17h47m56s**

Across that entire period, only **two human operational interventions** were needed:

1. recovery infrastructure hotfix after G9;
2. Helper Hotfix 2.0 after the G15 → Helper recovery path failed to close.

This was not 17+ hours of an idle browser staying open. It was a live engineering task continuously moving through real source inspection, builds, Cooks, runtime experiments, package analysis, Worker generations, Planner reviews and durable handoffs.

That is the strongest stability claim this showcase can support.

The final G32 physical-page failure extended the Parent Task wall-clock life to the **07:41:16 JST** stop boundary:

**20h15m28s total wall-clock lifetime**

but the final **2h26m05s** after G32 response-start was a stuck physical conversation, so it should not be counted as productive uptime.

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
