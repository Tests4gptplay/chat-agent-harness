# References

This file records external projects that materially inform `git-agent-harness`. It is not startup-required and should not become a catalog of every agent framework.

For the current adopt/defer decisions from Agent Harness, SandBase, Talon, Neutron, and related projects, see `docs/UPSTREAM_PATTERNS.md`.

## DeepSeek Harness

Repository: `deepseek-ai/deepseek-harness`
Status observed 2026-09-17: public MIT developer preview; TypeScript; architecture slogan `Everything is a Plugin`.

### Revised architectural interpretation

DeepSeek Harness should not be treated merely as a design reference for code we intend to reimplement. It is a plausible **inner agent runtime** underneath this project's Git-mediated control plane, but only when that integration preserves the economic goal of the project.

The primary goal of `git-agent-harness` is to leverage already-paid interactive/subscription model capacity (especially the current ChatGPT chat session, and where appropriate native subscription-backed Codex) by giving it durable Git state, external execution and recoverable feedback. Introducing a second metered model API merely to make the inner loop autonomous defeats that goal unless the user explicitly chooses that tradeoff.

The two layers solve different problems:

```text
outer: git-agent-harness
ChatGPT / Codex / human / scheduler
        -> Git durable state + task/action envelope
        -> GitHub Actions / self-hosted runner
        -> deterministic executor or optional inner runtime
        -> compact result / artifact identity / checkpoint
        -> Git

optional inner runtime
objective
        -> internal model/tool loop
        -> finish or block
```

The value of this repository is **transport, durability, cross-session coordination, cross-agent handoff, cross-machine execution, versioned evidence, and external project continuity through Git**. It is not valuable if it simply replaces a paid ChatGPT subscription with new API spending.

### Subscription-first rule

Default path:

```text
ChatGPT subscription session
        -> Git task/action
        -> runner
        -> deterministic/local tools (shell, tests, Blender, UE, validators)
        -> compact result
        -> Git
        -> next ChatGPT invocation
```

This remains the preferred architecture even though it is semi-automatic and may require a human `continue`/new invocation between external runs.

A mature inner agent runtime such as DeepSeek Harness is optional, not the default. Use it only when at least one of these is true:
- it can delegate to an already-paid native product/runtime without introducing separate metered model spend;
- it uses a local model the user deliberately accepts;
- a future supported path can invoke the user's ChatGPT subscription/session directly;
- the user explicitly decides that extra API spend is worth the autonomy gain.

Do **not** add DeepSeek/OpenAI/Anthropic API billing merely to remove the manual ChatGPT wake step unless the user explicitly asks for that tradeoff.

DeepSeek Harness documents built-in API providers and custom OpenAI/Anthropic-compatible providers; those require separate provider credentials and are **not** the current ChatGPT subscription session. Its optional Codex subagent provider uses native Codex configuration/authentication, which can be useful for Codex-backed work but still does not turn the current ChatGPT chat model into its parent brain.

### Deployment boundary if DeepSeek Harness is used

Do **not** vendor or fork the DeepSeek Harness source tree into `git-agent-harness` by default.

Keep only the integration contract here:
- a pinned supported dsh version;
- invocation wrapper / adapter;
- safe profile or patch template;
- Git task -> objective mapping;
- dsh output -> compact `result.json` normalizer;
- setup/discovery scripts and compatibility notes.

Install/provision the actual runtime on the execution host. For Blender/Unreal/private local work the preferred host is the user's Windows self-hosted runner because it must reach local software, local assets and the dedicated workspace.

Credentials must remain local/protected, never committed to Git. Before private game/MOD work is exposed to an inner runtime, audit and disable unwanted outbound session-log/telemetry paths.

### Boundary of responsibility

Keep these responsibilities owned by `git-agent-harness`:
- canonical Git checkpoint / recovery contract;
- agent-independent task/action/result envelope;
- GitHub Actions and self-hosted-runner dispatch;
- cross-chat and cross-agent handoff;
- external artifact lineage and hashes;
- project/revision state that survives replacement of the inner runtime;
- escalation policy (`ChatGPT`, `Codex`, optional inner runtime, human);
- compact evidence returned from long local work.

Borrow from mature runtimes rather than reimplementing blindly:
- durable facts vs live control events;
- reconstructable model-visible state;
- inbox / continuation semantics;
- turn/step separation;
- capability seams;
- headless execution;
- replaceability.

### Do not do yet

- Do not fork DeepSeek Harness into this repository.
- Do not copy Cordis or its plugin tree into the outer control plane.
- Do not duplicate its session/event/model/tool loop without a concrete need.
- Do not make a second paid model API a hidden dependency of the project.
- Do not sacrifice the original subscription-leverage goal merely to gain autonomous wake/continuation.

### Near-term experiment

The next real experiment should stay subscription-first: connect the Windows runner to deterministic Blender/UE or coding executors and let ChatGPT remain the reasoning brain across Git-mediated turns.

Only after that baseline is measured should an optional DeepSeek Harness experiment be considered, and then specifically to test whether it can add autonomy **without undermining the subscription-first economics**.
