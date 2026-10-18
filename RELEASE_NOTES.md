# CAH v1.0.5 — sanitized source update (2026-10-18)

## Highlights

- Refreshed the public Git-canonical Planner/Worker/Helper contracts and current runtime code for immediate Harness error recovery, same-generation Worker watchdog recovery, durable Git semantic completion, task-scoped handoff, safer exact cleanup, and Cold/Burst Runner coordination.
- Migrated only reviewed, de-identified code/protocol files. Private live tasks, user/host identities, evidence, requests, memory, browser state, personal Skills, secrets, workflow payloads and private history are excluded.
- Changed **current** CAH original-code licensing to **AGPL-3.0-only**, matching the maintained private-source legal notice. Historical MIT-distributed copies retain their earlier license rights; third-party components retain their own licenses.
- Installation remains an **unconfigured source template**. The installer must re-plan and verify a new private repository, host paths, runner topology (Managed, Short Task, normally-off Cold/Burst), ChatGPT Projects/Foreground identity and workflow/tool capabilities before claiming readiness. See [installation/UPGRADE_AND_REDEPLOY_V1_0_5.md](installation/UPGRADE_AND_REDEPLOY_V1_0_5.md).
- Existing approved public donations and curated historical public showcase assets remain; the active private movie/other operational experiments remain private.

## Verification boundary

This release is gated on newly run, recorded privacy and offline source tests. It does **not** claim independent fresh-PC setup, owner-account browser login, new-runner registration, complete live Helper fault injection, or end-to-end installation under another user's credentials. Missing private workflow payloads require explicitly authorized provisioning during a new private deployment; no live CAH workload should run from the public source repository.

## Previous clean source publication (historical)

# Clean source edition — 2026-09-29

## Scope

This publication promotes the reviewed unconfigured clean template. It does not merge a separate development branch or change the reviewed production runtime or test code. Publication-specific changes are documentation, privacy-boundary guidance and integrity/provenance metadata.

The architecture includes Git-canonical continuity; Foreground, Planner, Worker and Helper role contracts; native Playwright transport; bounded generation/fencing; executor and Skill/Tool interfaces. Installation documents map deployment placeholders to their consumers and validation steps. Six obsolete test expectations have been retired rather than carried as current open runtime defects.

## Exact replacement, not an overlay

Previous public main: `604e4ff26757d3738e4ee4aaa0d8457953a98db7`.

Historical anchor: `archive/pre-clean-20260929`.

Reviewed input tree: `db969c35eb6d90c8faee45f86dfd8f9736db6071`.

The replacement commit descends only from public history. No private repository history, migration-control directory, workflow/toolbox payload, personal Skill or showcase is imported. Old-only files, including the former extension/build surface and old deployment workflows, are not retained accidentally on main. Historical files can be inspected through the historical anchor; they are not instructions for the current edition.

No empty-main intermediate commit or force-push is used. The `clean-2026-09-29` tag identifies the new public source snapshot after verification. Existing release assets are kept as historical material, not overwritten.

## Installation / operational safety

This public source is not an operational CAH repository. Verify a new private repository, configure actual approved paths and account bindings, then provision the required workflow entry points there. Do not operate real tasks against public main. The package intentionally contains no supplied workflows, toolbox payloads or personal Skills.

Do not overlay this edition on a live configured instance. Preserve active tasks, local profiles, workflow payloads and private evidence until an explicit instance-migration plan has verified them.

## Verification and limits

See `audit/PUBLICATION.json` for this candidate's recorded checks and `audit/verification.json` for the original reviewed-template validation. `audit/file-manifest.json` and `installation/audit.py` check the current file inventory and content. Source tests use synthetic/offline fixtures; they do not certify a new machine, browser account, Runner registration, or an independently performed security audit.
