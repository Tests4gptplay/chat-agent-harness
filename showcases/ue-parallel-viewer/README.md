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

# Part 2 — Follow-up investigation exposed deeper problems

Part 2 began only after the successful Phase-1 baseline had already been accepted.

The new question was harder: could the Viewer path be extended from the known-good camera fixture toward a real package/direct-preview workflow with more complicated package provenance and IoStore/runtime behavior?

This became less a “success demo” and more a **problem-discovery case**.

## Part 2A — the first convenient interpretation was wrong

The combined-provider path successfully exported the logical target asset.

At first glance, that looked like evidence that the intended MOD had supplied the object.

Provenance inspection later showed otherwise.

There were multiple same-path candidates, and the default provider had selected a base-game PatchPak candidate. So:

```text
logical path loaded successfully
!=
desired source provenance proved
```

The task did not keep the convenient interpretation.

Archive-specific loading then directly proved that the exact selected package member contained a real `USkeletalMesh` with:

- **25 material slots**
- **26 morph targets**
- **1 LOD**

That reconnaissance boundary was formally accepted at **18:47:16 JST**, **7h21m28s** after Parent start.

So Part 2A succeeded as an investigation: it found a real hidden assumption and replaced it with a stronger implementation rule.

## Planner replacement became part of the experiment

During the accumulated Phase-1 + Phase-2 history, Planner G1 eventually handed off.

Planner G2 resumed from durable task surfaces rather than from the full previous chat transcript:

- Task;
- Plan;
- Planner Memory;
- Plan Note;
- Child Reply;
- Result;
- Evidence.

This was a useful secondary observation: the investigation survived Planner replacement without resetting the task.

## External execution outlived Worker turns

Generation 20 provides a clean timing example:

- Worker semantic interval: **3m37s**
- external Build/Cook interval: **2m20s**
- external execution continuing after Worker durable handoff: **1m10s**

The Worker did not need to remain alive merely to watch the machine.

The external operation remained addressable through durable state and could be inspected by a successor.

## Part 2B — pushing farther exposed infrastructure problems

The native direct-preview implementation reached real package-store/cooked-runtime work, but the follow-up also exposed failure modes outside the original Phase-1 success boundary.

One task-local execution wrapper blocked the primary Runner for about an hour.

Backup capacity then carried **three consecutive Worker generations** while the primary path remained occupied. This turned a theoretical redundancy mechanism into a real recovery observation.

Planner correctly classified the inherited blockage as an operational incident and invoked Helper.

The first Helper behavior was still insufficient: it diagnosed the problem but did not complete the required recovery/closure path.

That triggered a human operational intervention and a live Harness improvement. After the Helper recovery contract was strengthened, the same Parent Task resumed from its preserved semantic frontier.

So Part 2 was doing two things at once:

1. investigating the harder Viewer/runtime problem; and
2. revealing weaknesses in CAH's own recovery machinery.

## The follow-up did not finish

Part 2B continued through many Worker generations and accumulated useful native runtime evidence, but it never crossed the final direct-preview acceptance boundary.

The last admitted Worker generation was **G32**.

Its physical response started, but no durable semantic result followed. The user explicitly stopped further work.

The correct terminal state is therefore:

```text
Part 1 / Phase 1   ACCEPTED

Part 2A            ACCEPTED INVESTIGATION
Part 2B            STOPPED INCOMPLETE
Part 2C            NOT REACHED
```

That is not a failed Part-1 showcase.

It is a successful baseline followed by a deliberately harder investigation that discovered additional technical and Harness problems before being stopped.

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
