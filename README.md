# Chat Agent Harness (CAH)

**Turn chat sessions into schedulable agent workers.**

Chat Agent Harness is an API-optional agent runtime for AI systems that may expose little more than a normal chat interface.

CAH wraps chat-native AI sessions with external coordination, lifecycle management, scheduling, durable task state, and real execution tools. The AI provider does not need to expose its own agent runtime for a chat session to participate as a Worker.

A minimal CAH-compatible endpoint only needs a usable interaction surface:

- send a message;
- observe when a response starts and ends;
- identify or rebind a session;
- report basic transport/lifecycle health.

The underlying endpoint may be a browser chat, desktop app, CLI, API-backed model, or another interactive AI surface.

## Architecture direction

CAH separates semantic compute from the machinery around it:

- **AI chat sessions provide replaceable semantic compute.**
- **The Harness owns task identity, scheduling, continuation, recovery, and stale-worker fencing.**
- **Durable external state keeps work alive when a conversation, browser, device, or Worker is replaced.**
- **Runners and deterministic executors perform real work in local tools and workspaces.**
- **Single- and multi-worker execution share the same durable task model.**

Git is especially convenient for the current implementation because it already provides durable state, versioned evidence, collaboration, and a path to self-hosted execution. It is an implementation substrate, not a requirement that the AI itself understand or directly access Git.

A pure chat endpoint can therefore participate through an adapter that reads external task state, delivers the required semantic context through the chat interface, observes the result, and returns it to the Harness.

**APIs are supported, but they are not a prerequisite.**

The project is intended for long-running engineering work that should survive conversation rollover, browser/runtime failure, Worker replacement, and execution across different machines or tool environments.

## Status

**Coming soon — Developer Preview.**

This repository is currently a public placeholder while the first release is prepared and validated. The initial public version is expected to include the core scheduler/runtime, managed chat-worker transport, self-hosted runner integration, durable handoff/recovery mechanics, and reproducible showcase cases.

The current focus is correctness and evidence: implemented, CI-proven, live-proven, and experimental capabilities will be identified separately rather than presented as equivalent.

No production-ready release is published here yet.

## Future architecture

CAH's current two-lane work is the smallest live proof of a broader multi-lane scheduling model, not a fixed two-worker architecture. Exploratory directions include event-driven Worker release and resumption, migratable continuations and work stealing, capability-aware multi-host scheduling, durable semantic memory, shared-nothing distributed execution, and a possible permissionless public agent network.

These are explicitly separated from implemented and live-proven capabilities. See [Future Architecture discussion drafts](docs/vision/README.md).

## License

CAH is available under the **GNU Affero General Public License v3.0 only (AGPL-3.0-only)**. See [LICENSE](LICENSE).

Commercial use is permitted under the AGPL when its terms are followed. For users who need different terms — for example proprietary distribution, embedding, hosting, or service operation without the applicable AGPL obligations — a separate commercial license may be available from the copyright holder. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md).

Contributions are subject to the inbound licensing terms in [CONTRIBUTING.md](CONTRIBUTING.md) so that the project can preserve both the AGPL public edition and separate commercial licensing.

