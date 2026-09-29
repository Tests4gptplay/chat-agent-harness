# Memo: possible future distributed / sub-agent CAH

**Status:** speculative design memo only.  
**Binding:** none. This is not an implementation plan, roadmap commitment, or current requirement.  
**Activation gate:** do not implement any of this until the single-thread / single-background-task CAH path is stable enough to demonstrate end-to-end.

## Why record this

Once the current single-thread loop works reliably, CAH may have a natural path from a replaceable background Worker into a multi-agent, multi-node semantic compute system.

The idea is to preserve the current architectural principles rather than replace them:

- ChatGPT subscription reasoning remains the default reasoning substrate.
- Git remains canonical durable state.
- Worker conversations remain replaceable compute, not identity.
- runners/executors remain separate from reasoning.
- wake remains a doorbell, not task truth.
- foreground remains the human control/notification surface.

## Possible future shape

```text
Human / Foreground
        |
        v
Primary Worker / Planner
        |
        +---- subtask A ---> Worker A ---> Runner / Blender
        |
        +---- subtask B ---> Worker B ---> Runner / Unreal
        |
        +---- subtask C ---> Worker C ---> research / code / validation
        |
        v
Git canonical task graph + result refs
        |
        +---- dependency barrier / synchronization
        |
        +---- verifier / reduction
        |
        v
Primary Worker
        |
        v
Foreground result
```

This resembles a semantic version of distributed heterogeneous computing:

- Primary Worker / Planner ~ scheduler/control CPU
- Sub-agents ~ semantic compute units
- Runner jobs / local executors ~ device kernels
- Git ~ durable shared memory + control state + result exchange
- Actions ~ scheduling/dispatch
- capability manifests ~ device descriptors
- wake ~ interrupt
- `depends_on` ~ barrier/synchronization
- verifier/reducer ~ reduction/validation
- foreground ~ user-facing process console

The analogy is useful only as a design aid; CAH should not become infrastructure-heavy merely to imitate GPU/Kubernetes concepts.

## Sub-agent model

A future parent task could fan out into explicit subtasks:

```text
task-001
├── subtask-a
│   └── result-a
├── subtask-b
│   └── result-b
└── subtask-c
    └── result-c
```

Minimal fields might eventually include concepts such as:

- `subtask_id`
- `parent_task_id`
- `depends_on`
- `assigned_worker`
- `status`
- `result_ref`

These names are placeholders, not a schema decision.

The important property is that sub-agents exchange durable structured state through Git rather than relying on direct chat-to-chat conversation history.

## Background process / activity indicators

Before or alongside sub-agent concurrency, the foreground may need a lightweight process-status projection analogous to a process list or activity LEDs.

Example:

```text
Task: RUNNING

Planner          REASONING
├─ Blender       EXECUTING
├─ UE            WAITING_DEPENDENCY
├─ Research      SUCCESS
└─ Verify        QUEUED
```

Possible small state vocabulary:

- `QUEUED`
- `REASONING`
- `WAITING_EXECUTOR`
- `EXECUTING`
- `WAITING_DEPENDENCY`
- `BLOCKED`
- `SUCCESS`
- `FAILURE`

This projection must **not** become a second source of truth. It should be derived from canonical Git task state and runner evidence.

Avoid heartbeat commit spam. Prefer event-driven state transitions plus timestamps/leases; the UI can render "running for N minutes" without Workers repeatedly committing "still alive".

## Cross-computer execution

If multiple execution nodes exist later, capability-aware routing could let CAH choose where work runs:

```text
Blender task  -> node with Blender + suitable GPU
UE Cook       -> Windows node with required UE version
Python job    -> GitHub-hosted or local runner
GPU workload  -> cloud/local GPU node
research      -> reasoning Worker
```

This could make CAH a genuinely cross-computer distributed Agent system rather than merely a distributed deployment with one primary execution node.

A key architectural distinction would be that neither the reasoning Worker nor any one computer is the durable identity of the Agent. The durable task graph and Git state survive Worker replacement, browser failure, runner failure, or node migration.

## Map / barrier / reduce pattern

A complex task could eventually use:

```text
Split
  |
  +--> Agent A --+
  +--> Agent B --+--> Barrier --> Verify/Reduce --> next stage
  +--> Agent C --+
```

This should only be introduced where independent work can actually run in parallel.

Resource locking will matter. Two agents should not concurrently mutate the same `.blend`, UE project, or other non-mergeable workspace without an explicit lock/ownership protocol.

## Efficiency principle

Do not parallelize merely because multiple Workers exist.

Concurrency is useful when:

- subtasks are materially independent;
- the critical path shortens;
- runner/device resources can execute in parallel;
- synchronization cost is smaller than the saved latency.

Keep coarse-grained semantic work in CAH. Mechanical micro-steps should preferably stay inside deterministic local executors when that avoids expensive Worker -> Git -> Runner -> Git -> Wake round trips.

## Possible staged evolution

This is deliberately non-binding, but the natural order appears to be:

```text
Phase 1
single Worker / single task end-to-end demo
        |
Phase 2
background process visibility / activity projection
        |
Phase 3
sub-agent tasks + dependency graph
        |
Phase 4
multi-runner capability routing + resource locks
        |
Phase 5
cross-computer semantic distributed execution
```

Each phase should be justified by measured friction from the previous one.

## Possible future local CAH Host shell

A separate future packaging idea is to consolidate today's local pieces into one background desktop Host without reimplementing mature upstream components.

This is **not** current work. The immediate priority remains making the existing localhost + extension + official runner loop execute unattended end-to-end.

Possible future shape:

```text
CAH Host
├─ tray / small status UI
├─ deployment / repair scripts
├─ Runner Supervisor
│  └─ official GitHub self-hosted runner
├─ Bridge Supervisor
│  └─ current localhost bridge
├─ Browser Controller
│  └─ dedicated ChatGPT browser/profile
├─ Executor discovery / status
└─ logs / health projection
```

The Host should be a **shell, deployer and supervisor**, not a replacement implementation of Git, GitHub Actions, GitHub Runner, Blender, Unreal, Python, or Chromium/WebView2.

Prefer reuse:

- official GitHub self-hosted runner;
- Git;
- Python;
- Blender / Unreal;
- WebView2 or a managed Chromium runtime;
- PowerShell deployment/repair scripts, optionally wrapped by a simple `.bat` entry point for convenience.

A first Windows-oriented Host could use WebView2 or managed Chromium with a dedicated ChatGPT profile. During migration, the current extension can remain loaded inside that controlled browser and act as an executable specification for the later Browser Controller.

A gradual migration is preferable:

```text
Host v0.1
  -> start/stop/status for runner + bridge + dedicated browser
  -> current extension remains active

Host v0.2
  -> move configuration / health UI into Host

Host v0.3
  -> move wake and lifecycle orchestration into Host

Host v0.4+
  -> Browser Controller exposes guarded browser primitives
  -> extension can shrink or disappear if no longer needed
```

The interesting long-term gain is not packaging alone. A Host-controlled dedicated browser could expose a wider, safer AI-facing control surface than a fixed popup/extension handler set, for example navigation, tab management, guarded DOM query/click/input, screenshots, profile status, and ChatGPT-specific adapters.

That control surface should remain constrained to the dedicated CAH browser/profile and explicit domains/workflows. More autonomy should not mean unrestricted desktop/browser access.

Possible implementation preference if this becomes real:

- **Windows-first:** .NET + WebView2 is likely the cheapest initial productization path for the current Blender/UE/Windows environment.
- **Cross-platform later:** Tauri + managed Chromium/Playwright is a plausible migration path if cross-platform demand becomes real.
- Keep the Host adapter-oriented so Browser, Runner, and Executor backends can change independently.

Cash cost should be kept near zero for personal use by relying on open/free runtimes and existing subscriptions/hardware. Do not add paid infrastructure merely to make the packaging look more product-like.

## User-intended staged roadmap after the online proof

This section records the current intended order of operations. It is still gated: do not skip ahead before the preceding proof exists.

```text
Stage 0  ONLINE HARNESS PROOF
  existing single-thread CAH
  -> unattended Worker -> Git -> Runner -> result -> wake -> Worker
  -> foreground receives evidence-gated terminal result
  -> collect real runtime logs

Stage 1  SELF-HOSTED PARALLELIZATION
  use the proven single-thread self-loop to modify CAH itself
  -> add explicit subtask/DAG semantics
  -> add bounded parallel Worker lanes
  -> add dependency barriers / reducer-verifier
  -> add resource ownership/locks for non-mergeable workspaces
  -> preserve a single-thread fallback path

Stage 2  PARALLEL SUCCESS DEMO
  use the new multi-lane system on one real bounded task
  -> at least two materially independent branches run concurrently
  -> both produce durable evidence
  -> barrier/reducer completes correctly
  -> foreground receives one evidence-gated terminal result
  -> retain representative logs/timestamps

Stage 3  OPTIONAL DEPLOYMENT / STORAGE OPTIMIZATION
  only if measurements or maintenance needs justify it
  -> F: migration is optional, not a project milestone
  -> move only latency-insensitive / storage-heavy pieces when useful
  -> keep the known-good E: instance intact when it is useful as a reference/demo
  -> no migration is required merely to prove parallelism

Stage 4  PUBLIC PREPARATION
  after the online single-thread proof and one real parallel success case are reproducible
  -> use those logs/evidence as the demo basis
  -> prepare a clean-room public repository/export
  -> do not inherit private Git history, private issues, credentials, local IDs, or private state
  -> run secret/private-identifier scans and public install smoke
```

The purpose of this ordering is practical: first prove that CAH can actually operate as an online harness; then let that working single-thread system help build its own parallel "hands"; then prove one real parallel case. Storage/deployment migration is optional optimization, not a prerequisite for the demo or public preparation.

### Stage 0 acceptance boundary

"Online" means more than individual component tests. The minimum proof is one real, bounded task that completes this closed loop without manual continuation between internal steps:

```text
foreground request
-> background Worker takes ownership
-> durable action
-> official GitHub runner/local executor
-> durable result/evidence
-> localhost wake
-> Worker resumes from canonical Git
-> foreground terminal delivery
```

Record enough timestamps/log references to diagnose the loop, but do not turn this into artificial stress testing.

### Stage 1 parallelization boundary

Parallelization should be built by the already-proven single-thread loop, not as a separate manual rewrite project.

The first useful proof is intentionally small: two materially independent subtasks may run concurrently, both produce durable result refs, a barrier waits for both, and one reducer/verifier advances the parent task. Shared/non-mergeable resources must remain serialized.

Do not make "more Workers" itself the goal. The metric is shorter critical-path completion without multiplying ambiguous state or wasted reasoning.

### Optional persistent-versus-temporary storage intent

F: migration is no longer part of the success path. The parallel demo can run entirely on the existing E: environment.

If a later operational reason appears, persistent/temporary separation remains available as an optimization:

- keep a known-good E: instance when useful as a reference/demo;
- move lower-response-requirement or storage-heavy pieces to F: only when measurement/maintenance benefit justifies it;
- migration is selective, never required merely to demonstrate parallelism;
- never move latency-sensitive control/browser components for tidiness alone;
- any migrated path must pass its own smoke/evidence gate before becoming authoritative.

Likely F: candidates, if ever needed, are caches, scratch, large reproducible artifacts, low-frequency logs, and other throughput/storage-oriented data.

### Intended public case studies

If the roadmap succeeds, the public project should have two distinct proof stories rather than one generic demo:

**Case A — single-thread self-bootstrapping development**

```text
human asks for the harness itself to be improved
-> one background Worker takes ownership
-> Worker changes CAH through Git
-> runner/tests execute
-> result/evidence returns
-> wake resumes the same logical Agent
-> the harness advances its own implementation
```

This case proves continuity, unattended closed-loop execution, durable state, replaceable Worker identity, and self-hosted development of the harness itself.

**Case B — parallel multi-lane demo**

```text
parent task
├─ lane A -> durable result A
└─ lane B -> durable result B
        |
      barrier
        |
   reducer/verifier
        |
     terminal result
```

This case proves explicit task decomposition, bounded concurrency, dependency synchronization, result reduction, and resource-safe multi-lane execution.

The two cases should remain separate in documentation and logs because they prove different claims. Case A is primarily a **self-bootstrapping continuity/automation proof**; Case B is primarily a **parallel orchestration proof**.

### Stage 4 public gate

A public release is a separate sanitized productization step, not a visibility toggle on the private repository.

Use a new public repository or clean-history export. Preserve framework source/docs/examples/workflows only through an allow-list. Exclude private runtime state, credentials, private issue history, machine paths/identifiers, local telemetry, and project-specific confidential artifacts.

The public demo should be backed by real end-to-end logs/evidence from both the unattended single-thread loop and at least one real parallel success case, with sensitive data removed. F: migration is irrelevant to this gate.

## Concurrency-control model to study before Stage 1

Before implementing parallel Workers, borrow concepts from mature CPU/runtime synchronization models, but do **not** copy an OS thread scheduler literally. CAH Workers are slow, distributed, replaceable semantic tasks with Git durability, so the preferred model is coarse-grained message passing plus explicit task-state synchronization.

Useful primitives to map:

- **ready queue / scheduler** -> runnable semantic subtasks;
- **event / condition variable** -> wake a waiting lane when dependency/evidence changes;
- **barrier / join** -> parent/reducer cannot advance until required lanes reach terminal evidence;
- **mutex / resource lock** -> exclusive ownership of a non-mergeable workspace such as one `.blend` or UE project;
- **semaphore / capacity token** -> bounded concurrent access to a scarce resource, e.g. one UE editor or N GPU slots;
- **generation / fencing token** -> prevent a stale revived Worker from writing after ownership moved;
- **cancellation token** -> stop descendants after parent cancellation or invalidation;
- **CAS/revision check** -> reject stale state transitions instead of silently overwriting newer Git state;
- **cooperative yield** -> Workers checkpoint at durable semantic boundaries rather than attempting arbitrary mid-reasoning preemption.

Avoid busy-wait/poll loops where an event/wake can do the job.

### CL / Control-Light projection

If a visual "signal light" layer is added, treat it as a projection of canonical state, never as the synchronization mechanism itself.

A compact future control-light vocabulary could be:

```text
QUEUED          gray
READY           cyan
RUNNING         green
WAIT_DEP        amber
WAIT_RESOURCE   amber/red
VERIFY          purple
SUCCESS         blue/green
FAILURE/BLOCKED red
CANCELLED       dark gray
```

Internally, use symbolic states/events, not colors. The UI light is only a human/foreground projection.

A lane should advance because canonical predicates are satisfied:

```text
READY =
  all depends_on terminal-success
  AND required resource lease acquired
  AND generation still current
  AND parent not cancelled
```

### Prefer message passing over shared conversational memory

Default rule: Workers should not directly share or mutate one giant conversational context.

Use four memory planes:

```text
1. Foreground memory
   human goals / decisions / notifications
   no raw lane chatter

2. Canonical task memory (Git)
   task graph / dependencies / ownership / result refs / accepted decisions

3. Lane-local working memory
   one Worker's bounded conversation + local scratch/checkpoint
   private by default; replaceable

4. Inter-lane messages / evidence
   compact structured events, summaries and result refs
   no direct chat-history splicing
```

The reducer should normally consume compact lane outputs/result refs, not every raw Worker turn.

This is analogous to keeping thread-local working sets separate and using explicit synchronization/shared state for coherence. It also reduces context pollution and makes lane replacement/replay possible.

### Suggested lane lifecycle

```text
SPAWN
  -> QUEUED
  -> READY
  -> RUNNING
  -> {WAIT_DEP | WAIT_RESOURCE | VERIFY}
  -> SUCCESS | FAILURE | BLOCKED | CANCELLED
```

A lane owns only its assigned write-domain/resource lease. Parent/Planner owns DAG changes. Reducer/Verifier owns parent completion.

### Deadlock/race guardrails

- Require a canonical resource-acquisition order when a subtask needs multiple exclusive resources.
- Prefer one owner per non-mergeable write domain.
- Make result publication idempotent.
- Use leases + generation/fencing identities so expired Workers cannot resume as valid owners.
- Do not allow synchronous Worker-to-Worker conversational waits; dependency waits go through canonical task state/events.
- Keep parallel fan-out bounded; concurrency should shorten the critical path, not just multiply chats.
## Proposed parallel topology: lane projects + rolling conversation rings

The preferred first multi-Worker topology is to treat one ChatGPT Project as one **logical execution lane / isolation domain**, while conversations inside that Project are disposable incarnations of the same lane.

Do not make a conversation the durable lane identity.

Suggested first topology:

```text
Human foreground Project        # never automated/deleted
        |
        v
CAH Control / Planner Project   # one logical planner lane
        |
        +--> CAH Lane 0 Project # generic execution lane
        |      g0 g1 g2 g3 g4 -> g5 replaces g0 -> g6 replaces g1 ...
        |
        +--> CAH Lane 1 Project # generic execution lane
               g0 g1 g2 g3 g4 -> g5 replaces g0 -> g6 replaces g1 ...
```

For the initial proof, two execution lanes are enough. Add more only after the two-lane case is stable.

### Conversation ring inside each lane

Each lane keeps a bounded rolling ring of managed conversations. With ring size `N=5`:

```text
generation: 0 1 2 3 4 5 6 7 ...
slot:       0 1 2 3 4 0 1 2 ...
```

Rollover sequence:

1. current conversation for generation `g` checkpoints durable lane/task state;
2. create generation `g+1` as a temporary extra conversation;
3. new conversation reads canonical task/lane state and ACKs exact handoff/generation;
4. promote `g+1` to current;
5. only after durable takeover, retire `g-(N-1)` / the overwritten slot;
6. never infer takeover from tab existence alone.

This generalizes the existing proven 5+1 lifecycle. The current implementation is effectively one lane; parallelism should replicate the **lane abstraction**, not create ad-hoc independent chats.

### Project identity is routing/isolation, not task truth

Project names such as `CAH Sandbox 0` / `CAH Sandbox 1` are acceptable UI labels during development, but canonical state should use stable `lane_id` plus exact `project_key`.

Example conceptual registry:

```json
{
  "lanes": {
    "planner": {"project_key": "...", "kind": "planner", "capacity": 1},
    "lane-00": {"project_key": "...", "kind": "generic", "capacity": 1},
    "lane-01": {"project_key": "...", "kind": "generic", "capacity": 1}
  }
}
```

Do not encode semantic roles such as Blender/UE/Research into the Project identity initially. Prefer generic lanes whose assigned role comes from the canonical task packet. Specialized lanes can be introduced later only if measured routing value justifies them.

### Lane lease / fencing identity

Every active lane assignment should conceptually carry:

- `lane_id`
- `task_id` / `subtask_id`
- `generation` or fencing epoch
- current conversation id
- lease/status
- assigned write/resource domains

A lane result/write is accepted only if its task identity and generation still match canonical ownership. This prevents a stale/compacted/revived conversation from publishing after a newer incarnation took over.

### Scheduler behavior

The Planner owns DAG mutation. A scheduler maps `READY` subtasks onto free lanes:

```text
READY node
  + free lane
  + required resource lease
  + current generation/fence
        |
        v
ASSIGNED -> RUNNING -> result_ref -> terminal
```

A Worker that reaches `WAIT_DEP` or `WAIT_RESOURCE` checkpoints and stops; it does not sit in a chat busy-waiting. Canonical state/event wakes it when the predicate changes.

### Memory isolation

Project/lane separation should also be the default conversational-memory boundary:

- foreground keeps human goals/decisions and final summaries;
- planner keeps DAG/reducer reasoning;
- each execution lane keeps only its own bounded working context;
- inter-lane communication uses compact structured messages/result refs through Git;
- raw lane chat histories are not copied into other lanes or into foreground.

Project isolation is therefore useful both for browser routing safety and for context hygiene, but Git remains the only durable shared coherence point.

### Concrete reserved Project mapping

The user has already created the two intended Project containers:

```text
lane-00 / active Stage 0
  CAH Sandbox0
  project_key = g-p-CAHLANE00PLACEHOLDER

lane-01 / dormant until Stage 1
  CAH Sandbox1
  project_key = g-p-CAHLANE01PLACEHOLDER
```

Sandbox1 is a reserved container only. Do not bind browser automation, create managed Worker generations, or route wakes to it until the single-thread Stage 0 proof passes and Stage 1 explicitly begins.
### First implementation scope

The first parallel demo should **not** create a large pool. Use:

```text
1 Planner lane
+ 2 generic execution-lane Projects
+ existing 5+1 rollover semantics per lane
+ one two-branch DAG
+ one barrier/reducer
```

Only after this works should lane count become configurable/dynamic.
## Browser-instance isolation for parallel lanes

Multiple browser processes/profiles may be used as an additional isolation layer for parallel lanes.

Important distinction:

```text
Browser process/profile  = physical browser client / failure domain
ChatGPT Project          = logical lane / routing domain
Conversation             = one generation/incarnation inside that lane
```

Do not identify a lane only by a tab or window.

For the first two-lane proof, the simplest safe topology is:

```text
Human foreground browser/profile   # unmanaged

Managed browser A / profile A
  -> lane-00 Project
  -> its rolling conversation ring

Managed browser B / profile B
  -> lane-01 Project
  -> its rolling conversation ring
```

The planner may initially stay in the existing control Worker Project or be colocated with one managed browser; this is an implementation choice, not an identity rule.

### Stable browser identity

Each managed browser/profile should have a stable locally generated identity, conceptually:

- `browser_instance_id` (or reuse/rename the existing extension-local `clientId`);
- browser/profile label;
- allowed `lane_id` / exact `project_key` bindings;
- last-seen/health data kept outside canonical task truth.

The canonical lane registry may bind:

```json
{
  "lane-00": {
    "project_key": "g-p-...",
    "browser_instance_id": "browser-a",
    "generation": 12
  },
  "lane-01": {
    "project_key": "g-p-...",
    "browser_instance_id": "browser-b",
    "generation": 7
  }
}
```

A wake/action intended for one lane must be accepted only by the browser instance currently bound to that lane/project/generation.

### Profiles matter more than windows

Two windows from the same browser profile normally share extension local storage and therefore should not be treated as independent CAH browser clients. For deterministic separation, use either:

- different browser profiles / distinct user-data directories; or
- different browser products/profiles (for example Chromium profile A and Edge profile B).

Each profile should have its own extension storage and therefore its own stable browser client identity.

This gives another useful fencing layer:

```text
task/lane identity
+ project_key
+ browser_instance_id
+ conversation generation
```

A mismatch fails closed rather than routing a wake to whichever matching tab happens to be open.

### Why this helps

- one browser crash does not necessarily stop all lanes;
- tabs/projects are less likely to be cross-routed;
- extension state/config is isolated per lane host;
- later CAH Host packaging can supervise browser A/B as explicit child processes;
- parallel-demo logs can attribute each action/wake to an exact browser client.

Browser separation is optional capacity/isolation, not canonical task truth. Git remains the coherence layer.
## Focus-independent browser automation for parallel lanes

Parallel browser lanes must **not** compete for the operating-system keyboard, mouse, clipboard, or active-window focus.

Two managed browser profiles/windows are useful only if each lane is controlled through an exact browser/tab identity and DOM/browser API operations, not through global UI automation.

Preferred rule:

```text
lane-00 -> browser_instance A -> exact project/tab -> content-script/CDP DOM action
lane-01 -> browser_instance B -> exact project/tab -> content-script/CDP DOM action
```

Do not implement parallel input as:

```text
focus window A -> type -> press Enter
focus window B -> type -> press Enter
```

because OS focus is a single shared resource and would create races.

### Current extension implication

Extension 0.4.6 proactively changes managed Worker creation, retirement and hidden cleanup to open inactive background tabs (`active:false`) and removes focus-restore behavior. This is intentionally pulled into the single-thread proof so focus independence is validated before parallel lanes exist. CI/static guards can prove the absence of `active:true`; real ChatGPT background-tab behavior still requires live proof.

For Stage 1, preserve and generalize this lane-local focus-independent behavior:

- exact `browser_instance_id` + `project_key` + `conversation_id` targeting;
- content-script messaging or CDP/WebView2 DOM calls rather than global keyboard/mouse injection;
- no shared clipboard dependency;
- avoid `active:true` / window-focus changes except where a ChatGPT UI transition is proven to require it;
- if focus is temporarily required, serialize it behind an explicit `browser_focus` lease and restore the previous owner;
- a lane may never submit into a tab whose identity/generation does not match canonical ownership.

### Dedicated managed windows

A practical first implementation is one dedicated managed browser profile/window per lane. The window may remain open in the background while its extension controls only tabs inside that profile. The user-facing foreground browser remains a separate unmanaged client.

Background/inactive-tab behavior must be live-tested because browser throttling and ChatGPT UI behavior can differ when a tab is not active. If a particular transition truly requires an active tab, use a lane-local activation or a globally serialized focus lease rather than allowing both lanes to race for focus.

Longer term, a CAH Host + WebView2/CDP/Playwright controller can make this cleaner by controlling each browser instance directly without depending on desktop focus at all.
## Analogy boundaries: CPU control ideas, many-lane MIMD execution

The CPU/GPU analogies in this memo are deliberately split by layer and must not be treated as one literal hardware mapping.

### Control plane: CPU/runtime-inspired

Use CPU/runtime concepts for **coordination semantics**:

- ready queue / scheduler;
- events / condition variables;
- mutexes / semaphores;
- barriers / joins;
- cancellation;
- generation/fencing tokens;
- revision/CAS-style stale-write rejection.

These are synchronization ideas only. A CAH Worker is not an OS thread and should not be modeled as if it shares registers, stack, or low-latency coherent memory with another Worker.

### Execution plane: scalable many-lane, closer to MIMD than SMT/SIMT

Parallel CAH should scale by adding relatively independent semantic lanes:

```text
lane-00 -> task A
lane-01 -> task B
lane-02 -> task C
...
```

Each lane may execute materially different reasoning/tool paths. Therefore the closer architectural analogy is **coarse-grained MIMD many-core / GPU-style lane scaling**, not CPU SMT and not lockstep GPU SIMT/warp execution.

Useful mapping:

```text
many-core/GPU concept      CAH
-----------------------    ------------------------------
compute lane/core       -> Project/lane
thread incarnation      -> current Worker conversation generation
kernel/task packet      -> subtask
global memory           -> Git canonical task state
local working set       -> lane-local conversational cache
event/interrupt         -> wake / state event
barrier                 -> dependency barrier
fence/atomic check      -> generation + revision/fencing validation
reduction               -> reducer/verifier
```

### What must not be inferred from the analogy

- `Project = CPU core` is a routing/isolation metaphor, not a claim about shared-memory execution semantics.
- Conversation generations are rollover incarnations, not simultaneous hardware threads.
- The existing 5+1 ring is a lifecycle/retention mechanism, not a task queue.
- A future `8 lanes` design does not imply `16 threads` unless CAH deliberately adds multiple concurrent logical Workers per lane; that SMT-like step is separate and should be avoided until single-Worker-per-lane scaling is proven.
- Do not introduce warp/lockstep scheduling. Semantic workloads diverge heavily.
- Prefer coarse-grained fan-out -> barrier -> reduce over high-frequency inter-lane synchronization.

### Design default

Think of CAH as:

```text
CPU-like control/synchronization semantics
+
many-lane MIMD execution scaling
+
Git as durable coherence/state exchange
```

This hybrid framing is the intended architecture. Hardware analogies are explanatory aids only; protocol/state definitions remain authoritative.
## CUDA-inspired reference directions

CUDA is a useful **concept library** for future CAH orchestration, especially for asynchronous scheduling and many-lane execution. Borrow the control/dataflow ideas, not CUDA's literal SIMT execution model.

Highest-value concepts to study:

- **Streams** -> independent ordered work lanes with asynchronous overlap;
- **Events** -> explicit completion/dependency signals between lanes;
- **CUDA Graphs** -> capture a reusable DAG of work/dependencies and replay it with new task inputs;
- **host/device split** -> Planner/control plane versus execution lanes/executors;
- **global vs local/shared memory** -> Git canonical state versus lane-local working context;
- **async copies / staged transfers** -> compact result/message transfer instead of copying raw conversational history;
- **occupancy / utilization** -> measure useful lane work, queue depth and barrier wait rather than merely counting Workers;
- **fences / atomics** -> generation/revision checks and stale-write rejection;
- **cooperative groups / barriers** -> explicit subgroup synchronization where a reducer or phase boundary genuinely needs it;
- **bounded resources** -> semaphores/capacity tokens for UE, GPU, workspace or browser-instance limits.

Concepts to avoid mapping literally:

- warps and lockstep SIMT;
- per-instruction synchronization;
- shared-register/stack assumptions;
- assuming all lanes run the same algorithm or have similar runtimes.

A useful future mental model is:

```text
Planner / foreground control
        |
        +---- stream/lane 0 ---->
        +---- stream/lane 1 ---->
        +---- stream/lane 2 ---->
                     |
                 events/barriers
                     |
                 reducer/verify
```

CUDA Graphs are especially relevant to repeatable CAH workflows: once a task topology is proven, the graph structure can be reused while only task payloads, resource bindings and result refs change.
## External architecture references to study

Future Stage 1 design should treat mature compute/distributed runtimes as a **reference library of architectural patterns**, not as code to copy and not as a requirement to reproduce their full feature sets.

### CUDA

Study:
- streams as independent ordered work lanes;
- events and cross-stream dependencies;
- graphs as reusable DAG definitions separate from execution;
- barriers/reductions/fences;
- asynchronous submission and delayed synchronization;
- utilization/occupancy thinking.

Do not copy:
- warp/SIMT lockstep;
- per-instruction synchronization;
- assumptions of low-latency coherent device memory.

### ZLUDA

Study the compatibility-layer architecture rather than implementation details:
- preserve an external contract while translating onto a different backend;
- split translation/compiler/runtime/library adapters into replaceable modules;
- capability/unsupported-feature boundaries instead of pretending all backends are identical;
- incremental compatibility: make one path work, validate it, then expand coverage;
- keep the caller-facing interface stable while backend implementation changes.

CAH analogue: keep the task/action/result contract stable while BrowserAdapter, RunnerAdapter, executor backends, or future Host implementations change underneath.

### HIP / HIPIFY

Study:
- source/API portability layers;
- explicit differences between driver-level and runtime-level control;
- automated translation plus explicit handling of unsupported/architecture-specific features;
- incremental porting from a known-good implementation.

CAH analogue: preserve one semantic task interface while allowing different browser/runner/executor implementations and capability profiles.

### SYCL / OpenCL

Study:
- queues as submission domains;
- events as operation status/dependency handles;
- in-order versus out-of-order execution;
- runtime-managed dependency insertion;
- heterogeneous device selection without forcing callers to know every backend detail.

CAH analogue: logical lanes/queues plus explicit event/result dependencies, with scheduler/runtime handling the dependency plumbing.

### Ray

Study:
- tasks versus actors;
- object references rather than copying full data between workers;
- logical resource requirements and capability-aware placement;
- bounded resource scheduling;
- actor fault tolerance/restart semantics;
- placement concepts for locality/isolation.

CAH analogue: lane Workers as actor-like long-lived logical contexts; result refs as object-ref-like handles; capability manifests/resources drive placement.

### Temporal

Study:
- durable workflow state separated from worker process lifetime;
- resumability after crashes/network loss;
- workflow versus activity separation;
- retries/timeouts as explicit policy rather than ad-hoc loops;
- event-history-driven recovery.

CAH analogue: Git canonical state is durable workflow truth; browser Workers and runners are replaceable activities/executors.

### Erlang/OTP

Study:
- supervisor/worker separation;
- supervision trees;
- restart strategies such as one-for-one versus broader recovery;
- fail/restart at the smallest useful fault domain;
- explicit process isolation and message passing.

CAH analogue: browser instance/lane/Worker generations form independent failure domains; recovery should restart the failed lane rather than the whole system whenever possible.

### Selection rule

Borrow a pattern only when it solves a measured CAH problem. Prefer the smallest semantic primitive that fits:

```text
dependency ordering      -> event / depends_on
fan-out + join           -> barrier + reducer
scarce resource          -> semaphore / lease
stale Worker             -> generation/fencing token
backend portability      -> adapter/capability contract
crash recovery           -> supervisor/restart policy
durable long workflow    -> canonical workflow state + resumable activities
large result transfer    -> result_ref / object-ref style indirection
```

Do not grow CAH into a clone of CUDA, Ray, Temporal, Erlang/OTP, Kubernetes, or any other runtime. Their value here is architectural evidence for patterns that have already survived real concurrency/fault-tolerance problems.
## Future public-release AI deployment safety gate

This is a **future public-release requirement only**. Do not add the deployment gate file to the current private runtime yet.

When CAH reaches public distribution, add a small **AI-first deployment safety declaration** that any AI/installer must read before installation or binding. It should be outside the normal Worker hot path and explicitly marked `INSTALL/DEPLOY ONLY` so ordinary runtime Workers never spend context on it.

Suggested future shape:

```text
README.md
AI_DEPLOYMENT_SAFETY.md        # human-readable short warning
.gah/deployment-safety.json    # machine-readable gate
```

The important distinction must be explicit:

- the **CAH framework/source repository may be public**;
- the **operational/canonical runtime repository that receives state, task summaries, logs/evidence refs, user-derived content, lane state, and agent write-back must default to PRIVATE**;
- anything an Agent writes into a public Git repository is public by definition;
- current CAH does not scrape full assistant output, so do not falsely claim that all ChatGPT transcripts are automatically mirrored to Git;
- however, any conversation excerpts, summaries, prompts, paths, evidence, project facts, or other sensitive material that future Agents write into canonical state/logs would become public if the runtime repository were public.

### AI-enforced deployment behavior

The future declaration should instruct the deploying AI to fail closed:

```text
if operational_repo.visibility != PRIVATE:
    refuse normal CAH runtime deployment
    explain the exposure risk
    offer only:
      - create/use a private operational repo, or
      - an explicitly sanitized DEMO/PUBLIC-EXPORT mode with no private runtime state
```

Do not rely on the user reading documentation. The AI installer/deployer should treat repository privacy as a precondition and actively block unsafe default deployment.

The design should also avoid relying on model discretion alone. The AI-readable declaration is the semantic warning layer; the installer/deployer should independently enforce the same rule with a machine check when possible (for example, verify that the operational repository is private before enabling runtime write-back). This creates two gates:

```text
AI reads safety declaration
        +
installer verifies repository/runtime preconditions
        ↓
only then enable CAH runtime
```

If either layer cannot verify the privacy boundary, installation should stop in a safe state rather than assuming the user's intent.

Other likely preflight checks for the same gate:

- no credentials/tokens/secrets committed;
- operational state path is not the public framework repository;
- local managed roots are explicit and bounded;
- browser/Project bindings are explicit;
- destructive cleanup scope is limited to CAH-managed objects;
- public demo/export mode uses synthetic or sanitized state only.

### Runtime-context rule

This deployment safety declaration is **not** part of `state.next_reads`, Worker hot start, normal task routing, or lane memory. It is read only by install/upgrade/export workflows, because deployment safety and runtime reasoning are separate concerns.

Before the public release is actually built, re-check the then-current GitHub/OpenAI/browser behavior and rewrite the final warning from verified facts rather than copying this memo verbatim.
## Real-workload proof: evidence must escape the local tool loop

A green control-plane smoke is insufficient. Blender/UE/MCP workloads can still fail in ways that make the Agent effectively blind, for example:

- an MCP/tool call hangs forever and never returns;
- Blender completes work but the wrapper never emits a terminal result;
- renders/screenshots are written somewhere on local disk but the Agent does not know the path;
- a tool reports success without a durable artifact/evidence reference;
- the browser/Worker wakes but cannot discover what the local executor actually produced.

Before claiming a real workload proof, every substantial local action should end in one of two explicit outcomes:

```text
SUCCESS
  -> compact result JSON
  -> artifact/evidence manifest
  -> stable relative/managed path or uploaded artifact ref
  -> hashes/metadata needed for verification

FAILURE / TIMEOUT / BLOCKED
  -> terminal status
  -> bounded diagnostic summary
  -> last known artifact/log refs
```

Do not let the only evidence live in an unknown local folder.

### Artifact manifest

A future executor result should be able to expose a compact manifest such as:

```json
{
  "status": "PASS",
  "artifacts": [
    {
      "kind": "render",
      "logical_name": "front_view",
      "managed_path": "evidence/task-123/front.png",
      "sha256": "..."
    }
  ]
}
```

The path/ref itself may point to local managed storage, GitHub artifact storage, or another explicit evidence backend. The important invariant is discoverability: the Worker must know exactly what was produced and how to retrieve/inspect it.

### Tool/MCP timeout boundary

MCP or GUI-facing tools must not be allowed to hang the logical task indefinitely. Wrap them in an outer executor timeout/watchdog so the Harness can emit a terminal `TIMEOUT`/`BLOCKED` result even when the inner tool never returns.

Long local micro-loops are still allowed, but the Harness must retain control of timeout, checkpoint and artifact indexing outside the inner tool.

### Real Blender proof gate

A convincing Blender workload proof should demonstrate at least:

```text
open known .blend
-> inspect target scene/object
-> make a real modification
-> save a new revision/output
-> render one or more views
-> publish an artifact manifest
-> Worker retrieves/inspects evidence
-> acceptance or one corrective iteration
-> evidence-gated terminal result
```

If any step depends on a hidden local path, an unbounded MCP call, or a manual human screenshot hunt, the workload proof is not complete.
## Public positioning should stay domain-neutral

When CAH is prepared for public release, describe the framework in domain-neutral terms. Do not make the public pitch read like it was built specifically for the maintainer's 3D/Blender/Unreal interests.

Prefer capabilities such as:

- cloud reasoning with local/remote execution;
- cross-device and cross-node continuation;
- durable Git-backed state and long-lived memory;
- replaceable Workers and resumable tasks;
- task DAGs, parallel lanes, barriers and reducers;
- model/back-end portability;
- local/private executors in general;
- evidence-gated completion and recovery.

Avoid foregrounding narrow examples such as Blender, Unreal, MOD pipelines, or other maintainer-specific workloads in the top-level positioning. They may appear later as optional examples/case studies, but the framework description should clearly apply to coding, research, automation, data work, build systems, local tools, and other domains.

The public message should describe the architecture's general value, not reveal the maintainer's personal workload preferences.
## Dynamic help-after-finish: work stealing with durable handoff

After a lane completes its assigned work, it should be able to persist its useful working result into the durable memory/evidence pool, release its previous ownership, and help another lane on decomposable downstream work instead of remaining idle.

Conceptual flow:

```text
Lane B finishes task B
-> publish compact result / verified memory / evidence refs
-> release B resources
-> scheduler sees Lane A still has READY decomposable successors
-> B claims one eligible successor with a fresh lease/fencing token
-> A and B exchange only the required handoff contract/evidence refs
-> both proceed in parallel
-> barrier/reducer joins results
```

This is closer to work stealing / help-join scheduling than to fixed one-lane-per-task execution.

Important constraints:

- only steal `READY` work whose dependencies are satisfied;
- never split a non-decomposable critical section merely to keep a lane busy;
- persist reusable context before reassignment so useful reasoning is not trapped in the old conversation;
- use explicit ownership/lease/fencing so A and helper B cannot publish conflicting authoritative results;
- handoff should be compact and evidence-referenced, not raw conversation splicing;
- resource constraints still apply: a free reasoning lane does not imply the same `.blend`, UE project, GPU, browser-focus lease, or other exclusive resource is free;
- after helper completion, reducer/barrier decides what becomes canonical shared memory.

The control-light/state system can expose this naturally: a completed lane becomes schedulable capacity; downstream nodes transition through `WAIT_DEP -> READY -> RUNNING`, and helper claims are visible as ownership transitions rather than hidden chat-to-chat behavior.
## Event-driven continuation scheduling: logical tasks are not bound to Workers

A future lane should not behave like a permanently occupied OS thread. When a logical task reaches an unresolved dependency, it should checkpoint the minimum durable continuation state, transition to `WAIT_DEP` / `WAIT_RESOURCE`, and release the reasoning Worker so that Worker capacity can execute other `READY` nodes.

When the relevant control-light/event becomes satisfied, the suspended continuation is re-enqueued as `READY`. Any compatible Worker may claim and resume it using the durable checkpoint plus referenced evidence/memory. The continuation is therefore migratable across Worker conversations and browser generations.

Conceptually:

```text
task A running on Worker 0
-> dependency X unresolved
-> checkpoint continuation / memory refs
-> A = WAIT_DEP
-> Worker 0 becomes free and runs another READY task
...
event X satisfied
-> A = READY
-> any compatible free Worker claims A with fresh lease/fencing token
-> restore minimal continuation context
-> resume from the recorded semantic boundary
```

This permits out-of-order execution of independent DAG nodes and hides dependency/tool wait time. It improves utilization and can reduce end-to-end latency, but it does not remove true dependency critical paths. The more appropriate performance model is work/span rather than a naive fixed-thread interpretation of Amdahl's Law:

```text
T_P >= max(W / P, S)
```

where `W` is total work, `P` is available Worker capacity, and `S` is the dependency span / critical path. CAH should aim to keep execution close to this bound by ensuring blocked logical tasks do not pin expensive reasoning capacity.

Durable continuation records should eventually include at least task identity, resume boundary, dependency/event refs, required memory/evidence refs, capability/resource requirements, and fresh ownership lease/fencing on resume.

Important limitation: an LLM conversation is not a serializable CPU register file or hidden-activation snapshot. CAH can externalize explicit state, summaries, evidence, decisions and checkpoints, but cannot perfectly preserve unexposed internal model state. Resume correctness must therefore rely on durable explicit context plus verification, not on the assumption of lossless model-state migration.
### Worker affinity is optional, not identity

When a suspended task becomes `READY` again, the scheduler must not require the original Worker to resume it. Any compatible idle Worker may claim the continuation with a fresh lease/fencing token.

Example:

```text
A ran on Worker 0
-> A checkpoints and enters WAIT_DEP
-> Worker 0 does unrelated work
...
dependency event fires
-> A = READY
-> Worker 8 is currently idle and capability-compatible
-> Worker 8 claims A
-> restore A's durable semantic continuation
-> continue
```

The original Worker may be preferred only as an optimization when warm context/cache locality is useful. It is never the authoritative owner merely because it ran the previous segment.

Scheduling should therefore be capability- and resource-aware rather than identity-bound. Possible inputs include required tools, browser/profile affinity, local file/resource locality, model capability, cost/latency class, and exclusive-resource leases.

## One CL primitive, separate scheduling domains

Foreground supervision CLs and backend scheduling CLs are the **same semantic primitive**: durable Git-backed condition/event state with evidence, error, timeout and dependency semantics. Do not create a second incompatible "frontend status" type.

They differ by **scope and scheduler participation**, not by type:

- a foreground/Supervisor CL belongs to the human-facing supervision domain;
- backend task/lane CLs belong to scheduler/Worker execution domains;
- the foreground CL may reference or aggregate backend CL/result states;
- the foreground CL does **not** enter backend lane partitioning, READY queues, work stealing, resource scheduling or ownership arbitration merely because it uses the same CL type;
- backend schedulers must ignore CLs outside their declared scheduling scope/domain;
- a frontend supervision latch can remain held while backend CLs independently progress, fail, retry or complete.

Conceptually:

```text
same CL semantics
├─ scope=foreground_supervision
│    └─ observed by Foreground Supervisor
│       not schedulable in backend partitions
└─ scope=backend_execution
     └─ observed/claimed by scheduler + Workers
        participates in READY/RUNNING/WAIT/ERROR transitions
```

This keeps one coherent condition model across CAH while preserving isolation between front-end supervision and back-end execution scheduling.

## Control-plane topology and memory hierarchy

Use a computer-system style separation between front-end supervision, the Harness control plane, and backend execution domains.

```text
Human
  <-> Foreground Supervisor
        |
        +-- foreground CL / supervision latch
        |
        +-- read/write canonical Git state
                    |
                    v
                  Harness
              /      |      \
       backend CL backend CL backend CL
          |          |          |
       Worker/Lane Worker/Lane Worker/Lane
          |          |          |
       task-local  task-local  task-local
        cache        cache       cache
```

The key boundary is that the foreground does not directly join backend scheduling partitions. It creates/holds its own supervision CL and inspects Harness-visible canonical state through Git. The Harness owns scheduler-side interaction with backend CLs, lanes, Workers and executors.

Memory should also be treated as a hierarchy rather than one undifferentiated "agent memory":

- **durable Git/evidence state** is authoritative persistent storage;
- the **foreground working set** is RAM-like: a longer-lived active project/task view reconstructed from canonical state and useful across user interaction while supervision remains active;
- **backend lane/Worker memory** is cache-like: short-lived task-local context used for execution, scheduling and inter-stage coordination;
- once a backend task is accepted and its useful verified result/checkpoint has been promoted to durable state, expendable backend cache may be discarded;
- deleting backend cache must never delete the durable result/evidence or the minimum continuation state needed for recovery;
- cache locality may improve performance, but correctness must never depend on an ephemeral Worker cache surviving.

This is an analogy for control and memory hierarchy, not a requirement to imitate CPU hardware literally.

## Architectural philosophy: semantic compute, not chat orchestration

These principles are architectural guidance rather than Stage 0 implementation requirements.

1. **Tasks belong to the DAG; Workers are temporary execution capacity.**
   A logical task is not owned by the Worker that happened to execute its previous segment. Workers are fungible execution slots subject to capability/resource constraints.

2. **State follows the task; compute follows available compatible Workers.**
   Durable checkpoints, memory/evidence refs, dependencies and resume boundaries must be sufficient for another Worker to continue after suspension or rollover.

3. **Blocked logical work must not pin expensive reasoning capacity.**
   `WAIT_DEP` / `WAIT_RESOURCE` should release Worker capacity. Completion events/CL signals re-enqueue the continuation as `READY`, where any compatible idle Worker may claim it.

4. **Out-of-order execution is allowed whenever dependencies permit it.**
   Preserve logical dependency correctness and authoritative result ordering, not historical execution order. Independent READY semantic nodes may run in any order and on different Workers.

5. **Prefer coarse semantic parallelism over fine-grained pseudo-CPU parallelism.**
   The Harness operates at reasoning/task/tool/workflow granularity. Split work only when expected useful work exceeds decomposition, handoff, synchronization, verification and reduction overhead.

6. **Use dynamic DAG expansion and work stealing.**
   Workers may discover new subproblems at runtime. Completed lanes should publish reusable state and may help other READY decomposable successors instead of remaining idle.

7. **Memory is a compute asset, not merely chat history.**
   Verified intermediate conclusions, failed paths, decisions and evidence can be cached and reused so future tasks do not repeatedly pay the same reasoning cost.

8. **Memory should behave as a hierarchy.**
   Hot Worker context, task checkpoints, project durable memory, Git/evidence/artifacts and cold archives have different latency/capacity/durability roles. Workers should retrieve the minimum sufficient context rather than reload the whole project.

9. **CL/control lights are scheduling semantics, not decoration.**
   States such as `READY`, `RUNNING`, `WAIT_DEP`, `WAIT_RESOURCE`, `VERIFY`, `SUCCESS`, `FAILURE`, `BLOCKED` and `CANCELLED` should drive scheduling, wakeups, resource release and continuation eligibility.

10. **Completion must be evidence-gated.**
    A task is not complete merely because a tool call returned. Results should publish compact structured state plus durable artifact/evidence refs that downstream Workers and reducers can actually retrieve and verify.

11. **Worker affinity is an optimization, never identity.**
    Warm context, local files, browser profiles, GPU/tool access or other locality may influence scheduling, but correctness must not require returning to the original Worker unless a true exclusive resource makes that necessary.

12. **Model heterogeneity is expected.**
    Different Workers may have different model capability, cost/latency class, tools, browser access or local resources. Scheduling should be capability- and resource-aware rather than assuming homogeneous Workers.

13. **The execution model is closer to a semantic distributed runtime than a multi-agent chat.**
    Prefer canonical queues, events, leases, fencing, checkpoints, reducers and durable shared state over direct Worker-to-Worker conversational coupling.

14. **Use work/span thinking for performance.**
    The goal is not to eliminate true critical paths, but to hide wait time, reduce duplicated reasoning, maximize useful Worker occupancy and shorten future dependency spans through reusable verified memory.

15. **Reasoning migration is semantic, not bit-exact.**
    LLM hidden state cannot be serialized like CPU registers. Resume correctness depends on explicit durable context, semantic checkpoints and verification.

16. **Borrow computer-architecture ideas at the control/scheduling level, not literally.**
    Useful analogies include ready queues, interrupts/events, out-of-order execution, work stealing, barriers, reductions, cache hierarchy, affinity, leases/fencing and task migration. Do not force instruction-level, SIMT/warp, SMT or hardware timing assumptions onto semantic Workers.

17. **Public positioning should remain domain-neutral.**
    Blender/UE or other maintainer workloads may be case studies, but the architecture is intended for general coding, research, automation, data, build and local-tool workloads.

## What not to do prematurely

Do not interpret this memo as justification to add now:

- a Kubernetes-like scheduler;
- a second durable database beside Git;
- a generic distributed queue before multiple nodes exist;
- complex ownership epochs before concurrent writers exist;
- chat heartbeat spam;
- automatic multi-agent fan-out for trivial tasks;
- an LLM inside every runner;
- a large dashboard before a minimal process projection proves useful.

The single-thread path remains the current priority.

## Potential differentiator if eventually proven

Other harnesses already implement pieces such as persistent CLI agents, self-hosted workers, queues, sandboxes, and multi-backend execution. The possible CAH differentiator is the **combination**:

```text
subscription-native Web reasoning
+ replaceable browser Workers
+ Git-canonical durable state
+ semantic task DAG
+ sub-agent fan-out / barrier / reduce
+ cross-machine capability routing
+ local/private executors
+ foreground/background separation
```

This is a hypothesis to test after the basic system is proven, not a novelty claim and not a promise to build it.

## Control-plane refinement: Foreground, Planner, Watchdog and expert Helper

**Status:** design memo only; record the current architectural direction before implementation. This section does not require changing the existing task-confirmation or Worker flow yet.

### Core separation

The control path should separate semantic decisions from deterministic orchestration:

    Human
      -> Foreground
      -> confirmed Task Contract
      -> Planner / runtime coordinator
      -> CAH deterministic scheduler/runtime
      -> Workers / executors

Worker results and evidence return to the Planner, which may preserve, revise or replace the current execution plan. CAH then executes the resulting routing intent. Watchdog and expert Helper are side-path control roles rather than normal execution Workers.

The short form is:

- **Foreground:** determine and confirm what the user actually wants.
- **Planner:** determine, from the current facts, what should happen next.
- **CAH:** reliably make the selected next step happen, exactly under canonical ownership/fencing rules.
- **Worker:** perform the concrete semantic work.
- **Watchdog:** detect that execution is behaving abnormally.
- **Helper:** provide scarce expert diagnosis/replanning input when the normal system is stuck.

### Foreground and Task Contract

The existing pre-execution task confirmation is valuable and should remain. It acts as a semantic checkpoint between conversational intent and the Agent runtime.

After confirmation, the durable task description should capture the minimum authoritative contract, conceptually including:

- goal / intent;
- constraints;
- acceptance criteria;
- non-goals / explicit do-not-do boundaries;
- relevant evidence or inputs.

Once this contract is confirmed, the Planner should not reinterpret or silently rewrite the user's goal. Its job is execution planning against the confirmed contract.

This also explains why replacing or opening a fresh execution conversation need not cause large semantic loss: correctness should depend on the confirmed durable contract and canonical state, not on preserving an entire chat transcript.

### Planner is both planner and runtime semantic coordinator

At the current scale, do **not** introduce a separate dispatcher/telephone-operator AI. The logical dispatcher role exists, but it naturally belongs to the Planner because Worker returns may change the plan rather than merely select the next lane.

A Worker result may contain more than `DONE` or `FAILED`, for example:

- a newly discovered constraint;
- an invalidated assumption;
- a new dependency;
- a better execution path;
- partial completion;
- evidence that an existing branch is no longer worth pursuing;
- a capability/resource requirement that was not known at plan time.

Therefore the runtime loop should be:

    Plan -> Execute -> Observe -> Replan

rather than:

    Plan once -> blindly execute until completion

Planner responsibilities may include:

- choose single-lane versus multi-lane execution;
- decompose work and define dependencies;
- assign logical lane/capability requirements;
- define barrier/reducer or merge points;
- consume Worker results/evidence;
- decide whether the existing plan is still valid;
- issue the next routing intent;
- request retry, replan, pause, human input or expert escalation;
- select an appropriate model/capability class where heterogeneous Workers exist.

This makes the Planner a low-frequency, relatively high-value semantic control role rather than a one-shot task splitter.

A separate lightweight runtime dispatcher may become worthwhile only if future scale produces enough routine routing traffic that it is wasteful to involve the deeper Planner on every event. That is a later optimization, not a current architectural requirement.

### CAH remains deliberately non-semantic

CAH should not need general intelligence in order to execute well. Its advantage is the opposite: it should be the deterministic mechanism beneath AI policy.

Planner decides policy; CAH enforces mechanism.

CAH may reliably handle operations such as:

- canonical state transitions;
- task/lane creation and assignment;
- dependency readiness checks;
- dispatch and wake delivery;
- generation and fencing validation;
- ownership/lease enforcement;
- handoff recording and consumption;
- retry counters and deterministic thresholds;
- result/evidence registration;
- barrier satisfaction and the next declared transition.

CAH must not invent semantic next steps when a Worker returns an unexpected finding. If a result cannot be interpreted by deterministic plan rules, the state should become an explicit routing/replan condition and return to the Planner.

The intended invariant is:

> AI decides what the next semantic step is; CAH guarantees that the selected step is executed once, by the correct owner, against canonical state.

Keeping semantic intelligence out of the reliability core preserves the value of generation tokens, fences, canonical state, leases and durable handoffs. A smart Harness that can casually override the Planner would create a second competing policy authority.

### Watchdog as a separate control role

A Watchdog can be a dedicated Project/conversation role because observation and anomaly detection are different from task execution.

The Watchdog should **not** directly mutate task goals, take over Worker work, or become a second scheduler. It observes canonical state/progress and emits alerts or recommendations that the Planner/CAH path consumes.

Much of Watchdog behavior should remain deterministic where possible, for example:

- repeated error fingerprints;
- generation/attempt growth without accepted progress;
- repeated execution of an already disproven path;
- long-lived `BLOCKED` / stale states;
- unconsumed handoffs;
- dependency deadlock or impossible wait;
- timeout/resource-stall signals;
- conversation rollover/checkpoint pressure.

AI judgment is useful only for ambiguous semantic questions such as whether two attempts are genuinely different hypotheses or merely paraphrases of the same failed approach.

Example output should be advisory and structured:

    WATCHDOG_ALERT
    reason: repeated failure signature with no semantic progress
    recommendation: REPLAN or ESCALATE_HELPER

### Expert Helper / capability escalation

Rare, expensive, high-capability models should be treated as scarce expert resources rather than permanent Workers. A current example is a limited-quota top-tier model used only when ordinary Workers repeatedly fail or the user explicitly requests expert review.

The Helper is not normally part of the main execution chain and should not take over a task indefinitely. Its role is closer to a principal engineer consultation:

- diagnose root cause;
- review failed hypotheses;
- identify the minimum discriminating next tests;
- propose a robust fix or revised planning direction;
- identify paths that should not be retried.

Possible escalation signals include:

- same error/failure fingerprint repeating;
- multiple distinct repair attempts with no semantic progress;
- repeated return to an already falsified hypothesis;
- contradictory lane conclusions that the Planner cannot cheaply resolve;
- a persistent blocker after normal replanning;
- explicit user request for expert escalation.

The Helper should receive a compact **Expert Packet**, not the entire project history. A packet should contain only the information needed for the consultation, such as:

- confirmed task goal and constraints;
- current blocker;
- attempts already made and their outcomes;
- relevant logs/diffs/evidence refs;
- current plan/hypotheses;
- one explicit expert question.

The Helper returns diagnosis/advice or revised-plan input. The Planner incorporates that result into the canonical execution plan, and ordinary Workers resume execution. This minimizes scarce-model usage while preserving its value for hard failures.

The architecture should remain generic: `expert-helper` is a capability class, not permanently tied to one model name. Future Helpers may represent other specialist models or tool/capability classes.

### Helper capability degradation must fail soft

The Helper role must not be equivalent to one specific premium model. A top-tier model such as GPT-6 Pro may be the preferred capability, but the task-level Helper role must remain available even when the preferred model cannot be selected, its quota is exhausted, the UI/model switch fails, or that model is temporarily unavailable.

The reliability rule is:

> **Role availability has priority over preferred-model availability.**

Conceptually:

```text
Helper role
  -> try preferred expert model
       -> success: run as preferred expert
       -> failure/quota unavailable/model unavailable:
            record degraded capability
            select strongest allowed fallback
            continue Helper duty
```

A model-binding failure is therefore a capability-degradation event, not automatically a task failure:

```text
MODEL_BIND_FAILED
  -> record evidence/state
  -> select fallback model
  -> mark Helper capability DEGRADED
  -> continue consultation
```

A future capability ladder may conceptually look like:

```text
preferred expert model
  -> strongest available general model / highest reasoning tier
  -> next allowed fallback
  -> current strongest compatible model
```

Exact product/model names are runtime configuration and must not be hard-coded into the architectural contract.

Canonical state should be able to distinguish requested versus actual capability, for example:

```json
{
  "role": "expert_helper",
  "preferred_capability": "top_expert",
  "actual_capability": "strong_general",
  "capability_status": "DEGRADED"
}
```

Planner may use this signal when deciding how much additional verification or cross-checking is needed, but the Helper must still participate in the Task Cell/control loop.

Only if **no compatible model/Worker is available at all** and the current task explicitly requires expert review before safe continuation should the Helper path become truly `BLOCKED`. Scarce premium-model quota must never become a single point of failure for the task-control architecture.

### Dedicated pages/projects are role hosts, not durable identity

Foreground, Planner, Watchdog and expert Helper may each live in dedicated ChatGPT Projects/conversations for role clarity. Worker lanes may live in separate execution Projects as already explored.

However, a page/conversation remains replaceable compute. The durable role state, task contract, plan, alerts, helper packets and accepted results should remain reconstructable from Git canonical state/evidence. Replacing a Planner or Watchdog conversation must not change task identity or authority.

A useful control/execution split is therefore:

    Control plane:
      Foreground
      Planner / runtime semantic coordinator
      Watchdog
      expert Helper

    Execution plane:
      CAH deterministic runtime
      Workers
      runners / local executors

This is a role boundary, not a requirement to immediately create all physical pages or implement every protocol described above.
