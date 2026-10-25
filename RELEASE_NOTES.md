# CAH v1.1.0 — 2026-10-25

CAH v1.1.0 updates task continuity, operational recovery, reusable execution and AI-led installation.

## Runtime and execution

- Two normally online Managed Runners, with the second retaining the `backup` route; independent `cah-shot` short-task and recovery capacity.
- Task-owned Worker lineage, generation retention, durable handoff and Planner continuity.
- Git-canonical Result/Outcome completion through `turn_signal`.
- Immediate bounded Helper handling of reported Harness/Host/Bridge errors.
- 30-minute physical Planner recovery and same-generation Worker recovery, preserving task continuity during conversation replacement.
- Reusable Managed, cold-Managed, Stage 0, recovery, capability-probe, Skill-import and Short Task workflow entry points.
- Consecutive deterministic work can stay in one bounded request until the next semantic decision.

## Skills and distribution

- Reusable Skill procedures and guides are retained, including Blender, NTE asset workflows, command reliability, image transfer and audiovisual timeline analysis.
- Personal task payloads, local bindings, private case artifacts and runtime records are separated from the public source.
- Existing public branding, sponsorship, showcases and license notices remain available.

## Installation

The [AI-led guide](https://github.com/Tests4gptplay/chat-agent-harness/blob/v1.1.0/installation/README.md) covers dependencies, account access, browser-read Project URLs, configuration, Runner registration, startup and daily use.

[configure.py](https://github.com/Tests4gptplay/chat-agent-harness/blob/v1.1.0/installation/configure.py) creates a configured copy from a small local JSON file. It fills repository/path defaults, Task Cell, lanes and Foreground, retaining the current two-Managed-plus-Short-Task topology.

Existing installations keep their local bindings, browser profile, task state and personal Skills while updating the reusable code.

## Downloads

- [CAH-1.1.0.zip](https://github.com/Tests4gptplay/chat-agent-harness/releases/download/v1.1.0/CAH-1.1.0.zip) — versioned source with runtime, workflows, Skills and installation tools.
- [SHA256SUMS](https://github.com/Tests4gptplay/chat-agent-harness/releases/download/v1.1.0/SHA256SUMS) — SHA-256 checksum for the source bundle.

## License

Original CAH material uses **AGPL-3.0-only**. Third-party material retains its respective notices. See [LICENSE](https://github.com/Tests4gptplay/chat-agent-harness/blob/v1.1.0/LICENSE) and [NOTICE](https://github.com/Tests4gptplay/chat-agent-harness/blob/v1.1.0/NOTICE).
