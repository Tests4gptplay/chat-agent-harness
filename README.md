<p align="center"><img src="assets/brand/cah-mark.png" width="190" alt="CAH"></p>

# Chat Agent Harness (CAH)

**Harness AI with Git.**

Turn a chat-driven task into work that can run on your own machine, preserve its progress in Git, and survive replacement of the conversation doing the reasoning.

[中文说明](README.zh-CN.md) · [Installation](docs/INSTALL_WINDOWS.md) · [Camera showcase](showcases/camera/README.md) · [Self-update showcase](showcases/self-update/README.md)

## What it does

CAH connects a human-facing conversation, a Git-backed task record, a local bridge, a browser extension and a self-hosted runner. The conversation reasons; the runner executes; Git records what actually happened.

```text
User → Foreground → task contract / Task Cell when useful
                         ↓
                   Git task + result state
                         ↕
             local bridge + browser extension
                         ↕
              replaceable execution Workers
                         ↓
               runner → local tools → artifacts
```

Task Cell carries task-level planning and supervision; it is not part of the disposable Worker pool. Worker lanes can hand over with durable checkpoints. Small tasks do not need artificial fan-out or a round of approvals: use the shortest sufficient route.

The distribution includes canonical state and condition ledgers, dispatch/continuation handling, independent Worker lanes and 5+1 conversation lifecycle, deterministic executors, host capability discovery, an evidence-backed Skill Registry, and the host/extension self-update path. Skills can be accumulated, retrieved, promoted and reused; personal imported Skill libraries are not shipped.

## Recorded showcases

### A camera built through an actual reasoning–execution–review loop

[![Final procedural retro camera](showcases/camera/final.png)](showcases/camera/README.md)

Five Blender iterations, rendered visual review, executor repairs and a selected final asset. Browse the iteration images, download the cleaned `.blend`, and inspect the procedural scripts. The camera's **GAH 77** badge is its original historical artwork, produced before the project was renamed CAH; the images are not rebranded reconstructions.

### CAH updating its own installed runtime

[The self-update case](showcases/self-update/README.md) follows a tested source change through Git, the existing Windows runner, extension build, bridge restart, native extension reload and fresh runtime readback. The recorded update reached version **1.0.4** without restarting Chrome and retained both Worker bindings. A subsequent one-lane task produced a usable result.

These are **sanitized case reports**, not a dump of the maintainer's private chats, machine configuration or repository history. Reported failures and the limits of the measurements remain visible.


### Parallel test: Blender assets to UE/Cook + Viewer development

Two Worker lanes prepared the asset pipeline and Viewer under Task Cell coordination, then joined on a real native UE result.

[![Native UE camera result](showcases/ue-parallel-viewer/images/perspective.png)](showcases/ue-parallel-viewer/README.md)

[Read the case study](showcases/ue-parallel-viewer/README.md) · [Agent guide](showcases/ue-parallel-viewer/AGENT_VIEWER.md)

## Installation

> **ChatGPT / GPT users:** before installation, connect the **GitHub plugin/connector** in ChatGPT and grant it read access to this public repository. Then ask the installer AI to read CAH through the GitHub integration (starting with `AGENTS.md` and the installation guide) instead of manually pasting repository files into chat. When the live instance is created later, authorize the private operational repository separately.

**Recommended: let an AI Agent guide the installation instead of treating this as a manual sysadmin tutorial.**

CAH's core runtime is not intended to be Windows-only. **Windows is the currently validated reference host**, not an architectural requirement. The installer Agent should first identify the host OS and available tools, then preserve the same CAH contracts while adapting host-specific glue such as runner launchers, shell scripts, browser discovery, paths and startup/service integration.

Give an AI Agent access to this repository and ask it to install CAH from the public distribution into your own **private operational repository**. The Agent should read `AGENTS.md` and the current [installation reference](docs/INSTALL_WINDOWS.md), inspect the host, map any Windows-specific reference steps to native equivalents when necessary, prepare the repository/configuration, run build and smoke checks, and guide you through only the steps that require human authority.

Do not copy a Windows command literally onto another platform merely because it appears in the reference guide. Preserve the intent and invariant instead. For example, an official GitHub runner may expose `run.cmd` on Windows or `run.sh` on another host; a launcher may be `.bat/.ps1`, shell, app or service integration depending on the platform.

The human should normally handle the trust-boundary actions that an Agent should not silently perform on its own, such as:

- preparing or approving the machine and required applications;
- creating/confirming the private GitHub repository;
- granting GitHub/ChatGPT permissions;
- creating the required ChatGPT Projects and sharing their Project URLs with the installer;
- approving runner registration and other account-level authorization;
- loading/approving the browser extension when the browser requires user confirmation.

The Agent should **actively guide those human steps**, not merely say "go authorize this." It should give one concrete step at a time, including the relevant site/settings path, what control to click, what the user should expect to see, and how to confirm success. If the user's UI differs from the guide, the user can upload a screenshot of the current page and the Agent should use that screenshot to identify the next action instead of guessing.

Never ask the user to paste or screenshot secrets such as passwords, session cookies, access tokens, runner registration tokens, recovery codes, or other credentials. Guide the user through using those values locally without exposing them to the chat.

The Agent should handle the mechanical setup around those approvals: environment checks, repository wiring, configuration generation, builds, topology checks and first smoke validation. If a prerequisite is missing, it should tell the user exactly what to install or authorize, verify the result, then continue from the same installation step.

**Do not attach your computer's runner or live CAH state to this public distribution.** A real CAH instance runs from a verified private operational repository.

After the one-time setup, start CAH through the launcher appropriate to the host. On the validated Windows reference installation that is `Start_CAH.bat`; on another OS the installer Agent should create or select an equivalent native launch path and verify the same bridge/runner/topology health conditions. Your paths and bindings are configured for your host, not inherited from the maintainer. The source package builds Chromium and Firefox extension variants; the recorded live deployment used Chrome on Windows.

## Status and limits

**Experimental 1.0.4 release.** This is a working engineering project, not a production service or an official OpenAI/GitHub product. Browser UI changes, Git/network latency and model availability can interrupt work. The public package is sanitized and tested, but a fresh third-party installation has not yet been independently field-tested.

The normal-path benchmark reduced local request discovery from 605 Git processes to 5 for 200 completed requests plus one pending request. That is not a whole-task speed claim: the recorded post-update delegated task took **98 seconds from wake commit to result commit**, and **125 seconds to backend finalization**. Details and scope are in the self-update report.

General adaptive scheduling and automatic model/effort selection are still unfinished; the repository does not present those development branches as shipped features. There is **no single-button emergency stop that reliably kills every already-running child process**. Stopping a runner prevents new jobs; inspect and stop active tools separately when necessary. Read [OPERATIONS.md](docs/OPERATIONS.md).

## Future architecture

CAH's current two-lane work is the smallest live proof of a broader multi-lane scheduling model, not a fixed two-worker architecture. Exploratory directions include event-driven Worker release and resumption, migratable continuations and work stealing, capability-aware multi-host scheduling, durable semantic memory, shared-nothing distributed execution, and a possible permissionless public agent network.

These are explicitly separated from implemented and live-proven capabilities. See [Future Architecture discussion drafts](docs/vision/README.md).

## Support CAH

If CAH saves you time, API cost, or simply makes your existing ChatGPT subscription more useful, you can support continued development by starring the repository, reporting reproducible bugs and real-world cases, or sponsoring the project on GitHub.

**[❤️ Sponsor CAH on GitHub](https://github.com/sponsors/Tests4gptplay)** · **[☕ Buy me a coffee on Ko-fi](https://ko-fi.com/kou392350)**

Sponsorship helps cover testing, tools and continued development. It does not buy priority fixes or private support; commercial licensing and commissioned integration remain separate.

## License and contribution

[GNU AGPL v3](LICENSE) applies under the version choice stated in [NOTICE](NOTICE). An alternative commercial license is available by agreement with the copyright holder: [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Commercial activity is not automatically forbidden by the AGPL route. Third-party applications and dependencies retain their own licenses.

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting code. Use public issues for reproducible bugs or licensing inquiries; do not post credentials, session cookies, private Project URLs or raw personal execution logs.
