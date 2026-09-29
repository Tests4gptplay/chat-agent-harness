# Chat Agent Harness

## Clean source edition — 2026-09-29

CAH connects browser-based semantic roles, Git-backed task continuity, and scoped local execution. Foreground captures user intent; Planner coordinates work; Worker executes a bounded task; Helper handles operational incidents. Harness and the native Playwright Host own transport, authority checks and mechanical lifecycle supervision.

**This public repository is a source distribution, not your live operational repository.** Create or select your own private operational repository, verify its visibility and configure a new installation before running real tasks. Never commit real task state, credentials, browser profiles, personal paths or account bindings here.

## Installation starts here

Read [installation/README.md](installation/README.md), then the [configuration map](installation/CONFIGURATION_MAP.md) and [omitted components](installation/OMITTED_COMPONENTS.md). Installation is AI-guided but requires real environment and account checks; `Start_CAH.bat` starts a configured instance, not an unconfigured download.

The configuration map explains what each removed private setting did, its replacement key, which source files consume it, and how to verify the new value. `installation/placeholders.json` provides the exact token/file/line inventory. The installer creates a **new private operational copy**; it refuses to overwrite an existing instance.

```text
python installation/audit.py
python installation/configure.py --config <LOCAL_CONFIG_JSON> --output <NEW_OPERATIONAL_DIRECTORY>
```

Supply actual approved paths, repository identity, dedicated Task Cell/lane Projects and a Foreground conversation. Browser profiles, Runner credentials and runtime logs remain outside the source distribution. The installation guide distinguishes template rendering, dependency setup, Runner registration, browser login and actual task acceptance.

## Included and intentionally absent

Included: core scheduler and Task Cell roles; Git-canonical state and generation/fencing; browser Host and provider interfaces; executor interfaces; Skill/Tool schemas and mechanisms; installation/configuration tools; synthetic tests; privacy/integrity evidence. The Skill registry starts empty.

Not included: personal Skills or imported Skill snapshots, supplied GitHub workflows or toolbox payloads, live tasks/memory/results, private cases, browser bindings, machine capability caches or showcases. A registered Runner without a provisioned workflow is **not** an operational task path. See [OMITTED_COMPONENTS.md](installation/OMITTED_COMPONENTS.md).

## Verification

```text
python installation/audit.py
python -B -m unittest discover -s tests -v
```

[audit/PUBLICATION.json](audit/PUBLICATION.json) identifies the reviewed source tree and this publication's checks. [audit/verification.json](audit/verification.json) preserves the earlier offline validation record. The six obsolete test expectations were retired, with current behavioral protections retained: [resolved record](audit/TEST_CONTRACT_CLEANUP.md).

Checksums and source tests do not constitute independent security certification or a fresh-machine installation test. Environment-, workflow- and live-browser-dependent skips are reported explicitly.

## Upgrading and historical material

This edition replaces the previous public file tree completely, not by overlaying files. The pre-replacement public main remains at tag `archive/pre-clean-20260929`. Existing release assets are historical snapshots; they are not silently rebuilt or relabeled as this edition. Use this main tree or the `clean-2026-09-29` source tag for the current clean template.

Do not pull this public tree over a configured live private installation and assume its local bindings, workflow payloads or active tasks have been migrated. Follow the installation guide to create a new private copy; migration of an existing active installation requires its own state and permission review. See [RELEASE_NOTES.md](RELEASE_NOTES.md).

MIT license: [LICENSE](LICENSE) and [NOTICE](NOTICE). `GAH_*` and the generic project identifier remain wire-protocol compatibility names, not deployment-account settings.
