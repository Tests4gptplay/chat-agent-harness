# CAH v1.0.5 — major lifecycle/recovery update (2026-10-19)

> **Release gate:** This source release is generated only after public-tree privacy, file inventory, applicable offline unit tests and SHA256 archive checks pass. The package remains an experimental unconfigured source distribution, not a live Windows/ChatGPT instance. [v1.0.4](https://github.com/Tests4gptplay/chat-agent-harness/releases/tag/v1.0.4) remains historically unchanged.

## What changed compared with v1.0.4

This is a functional upgrade to the CAH execution lifecycle, **not** only a documentation refresh or a new version label.

| Subsystem | v1.0.5 change | Operational meaning |
|---|---|---|
| Helper fault recovery | Reported Harness/Host/Bridge errors initiate an immediate, bounded Helper incident without waiting for a Planner or watchdog threshold. | Diagnose and safely repair the exact fault; no fabricated task progress, recursive Helper storms or unrelated cancellations. |
| Worker context & watchdog | About **10 minutes** is the healthy semantic work/checkpoint budget; **30 minutes** is a separate hard physical watchdog. | A stalled physical conversation can be reborn in the **same generation** with the existing dispatch/fence; ordinary `continue` advances to a new generation with durable handoff. |
| Planner/Worker continuity | Task-scoped generation/lineage ownership, append-only memory/Child Reply, bounded retention and explicit review directions. | Shared lanes must not confuse one task's Worker conversations with another's. |
| Semantic completion | Final `turn_signal` in the **pre-bound Git Result/Outcome**; reconciliation verifies current identity/evidence. | Neither chat text nor `semantic_sync` is a second source of completion authority. |
| Runner capacity | Primary Managed, dedicated `cah-shot` for Short Task/bounded rescue, optional normally-off `backup` Cold/Burst Managed capacity. | Planner/Worker coordinate heavy detached work without stealing a healthy busy Runner or using Shot for managed business jobs. |
| Public packaging | Explicit de-identification and new-instance reconfiguration; maintain Tool/Skill subsystem **code**, not the maintainer's personal Skills or workflow payloads. | A cloned public source tree is not a ready-to-run private CAH instance. |

The maintained architecture remains an **experimental** Windows/ChatGPT-web/GitHub/Playwright-based harness, not a universal zero-configuration installer or a claim of supported non-ChatGPT adapters.

## Public/private boundary and installation impact

The public tree is a **sanitized, unconfigured distribution**. It must not contain private tasks, memories, Runner registrations, browser sessions, account/Project bindings, host-specific paths, secrets, private media, personal Skills or production workflow scripts. Original source/history from the private operational repository is not imported as private Git history.

Read [installation/README.md](installation/README.md) and **[v1.0.5 private re-deployment checklist](installation/UPGRADE_AND_REDEPLOY_V1_0_5.md)**. An installing AI/user must independently select a **private** operational repository, configure and verify paths/permissions, own ChatGPT Task Cell/lane/Foreground identities, a dedicated Chrome/CDP profile, Bridge/Host, primary Managed and Short Task Runner, and a separate optional Cold/Burst Runner. The public package intentionally omits private GitHub Actions/workflow payloads: a registered Runner is not sufficient to claim Managed, Helper or Short Task acceptance; `WORKFLOW_NOT_PROVISIONED` is an honest readiness outcome until equivalent authorized capabilities have been installed and tested.

Existing configured private instances require a separate per-instance migration/rollback plan. Do **not** overlay this public distribution onto active tasks or import the maintainer's private configuration.

## License

Current project-owned CAH code/documentation: **GNU AGPL-3.0-only**, as declared in [LICENSE](LICENSE) and [NOTICE](NOTICE). Previously distributed MIT-licensed copies retain the license rights granted to those copies; third-party dependencies/assets keep their own notices.

## Verification and release gate

The public distribution omits private operational GitHub Actions workflows and Host/Runner payloads. Workflow-dependent integration tests must be explicitly marked as packaging skips, not falsely counted as executed; Windows-only launcher tests cannot pass as Windows acceptance on an Ubuntu Runner. Source-level Helper/Worker/Tool behavior uses synthetic fixtures for offline checks.


- **Source present is not a tested installation.** The `v1.0.5` publication must have a *new* evidence record for the exact released file tree: privacy/inventory scanner, required export paths, Python/JSON/PowerShell syntax where applicable, source unit tests and updated install-placeholder mapping.
- Independently confirm that the actual new target GitHub repository is private and that newly registered Runner/project/CDP/Bridge connections work. A full fresh-machine deployment under another user's account is **not** claimed unless separately demonstrated.
- Missing private workflows, live fault-injection Helper acceptance, Windows host tests and any other unperformed checks must be identified accurately; do not reuse results recorded for the September source edition.
- Only a successful gate creates and publishes the `v1.0.5` tag and attached `CAH-1.0.5.zip` / `SHA256SUMS`. An unsuccessful CI run must leave the release unpublished.

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
