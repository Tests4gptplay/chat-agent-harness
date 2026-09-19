# Chat Agent Harness (CAH)

**Harness AI with Git.**

Chat Agent Harness is a Git-native control plane for turning replaceable AI chat sessions into durable, schedulable workers backed by real local and self-hosted execution.

CAH is being built around a simple separation of responsibilities:

- **Git holds durable task state, handoffs, results, and evidence.**
- **AI chat sessions provide replaceable semantic compute rather than owning task identity.**
- **The Harness schedules work, coordinates continuations, and fences stale execution.**
- **Self-hosted runners and deterministic executors perform real work in local tools and workspaces.**
- **Single- and multi-worker execution share the same durable task model.**

The project is designed for long-running engineering work that should survive conversation rollover, browser/runtime failure, worker replacement, and execution across different machines or tool environments.

## Status

**Coming soon — Developer Preview.**

This repository is currently a public placeholder while the first release is prepared and validated. The initial public version is expected to include the core scheduler/runtime, managed chat-worker transport, self-hosted runner integration, durable handoff/recovery mechanics, and reproducible showcase cases.

The current focus is correctness and evidence: implemented, CI-proven, live-proven, and experimental capabilities will be identified separately rather than presented as equivalent.

No production-ready release is published here yet.
