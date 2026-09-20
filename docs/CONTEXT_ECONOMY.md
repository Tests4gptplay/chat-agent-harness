# Context economy and knowledge placement

Status: maintenance/development design guidance.

CAH is a system in which conversations are repeatedly created, replaced, resumed, and routed. Therefore **context cost is an architectural cost**.

A rule that is useful once per installation, once per recovery, or once per feature design should not automatically live in a file that every Worker may read. Conversely, a critical invariant that is required on nearly every wake should not be hidden in a deep tutorial.

The goal is:

> keep hot-path context small, put authoritative low-frequency knowledge behind explicit routing, and promote repeatedly useful procedures into evidence-backed Skills.

## Core principle

The cost of a document is not only its file size.

A rough mental model is:

```text
context cost
  ~= bytes/tokens loaded
   x read frequency
   x number of Worker generations / lanes / resumptions
```

A few paragraphs in a high-frequency file can cost more over the life of the system than a much larger cold-path document that is loaded only when needed.

This matters especially in CAH because:

- Worker conversations are disposable and frequently recreated;
- multiple lanes multiply repeated reads;
- state-only hot start is intentionally optimized for cheap resume;
- context windows and model attention are finite resources;
- repeated irrelevant instructions increase latency and can reduce decision quality.

## Knowledge placement hierarchy

Use the narrowest layer that matches the knowledge.

```text
HOT
  state/chatgpt.json + task/dispatch refs
      current frontier / exact next action

  AGENTS.md
      small, stable runtime invariants required frequently

WARM / ROUTED
  focused docs/*.md
      authoritative subsystem contracts and low-frequency procedures

  ai/repo-map.json
      tells an Agent which focused document/code to load and when

REUSABLE PROCEDURE
  skills/index.json -> selected Skill
      evidence-backed procedure that should reduce future reasoning work

HISTORY / PROOF
  cases/ + evidence/
      what happened, why a capability is believed, reproducible proof

COLD
      architecture ideas that should never be normal startup context
```

Importance does **not** determine hotness. Frequency and necessity do.

A safety-critical maintenance operation may deserve a first-class contract while still remaining cold-path.

## What belongs in AGENTS.md

`AGENTS.md` is a high-frequency runtime contract and should remain intentionally small.

Put something there only when a Worker is likely to need it during ordinary execution or when omission would repeatedly cause unsafe/incorrect behavior before routed context can be loaded.

Good candidates:

- canonical authority rules;
- minimal wake/dispatch invariants;
- fencing/staleness behavior;
- required checkpoint/handoff semantics;
- small routing rules needed to reach deeper contracts.

Bad candidates:

- installation walkthroughs;
- rare maintenance procedures;
- historical incident narratives;
- UI click-by-click tutorials;
- long examples;
- future architecture discussions;
- subsystem implementation detail already reachable through repo-map;
- one-off troubleshooting knowledge.

When a new paragraph is proposed for `AGENTS.md`, ask:

```text
Will ordinary Workers need this on a large fraction of wakes?
Does reading it before routing materially prevent common failure?
Can the rule instead be a one-line pointer plus routed contract?
```

If the answer favors routing, keep it out of `AGENTS.md`.

## When to create a focused MD

Create or extend a focused `docs/*.md` contract when knowledge is:

- authoritative;
- important enough to preserve;
- needed only for a particular subsystem, maintenance mode, installation path, or design task;
- too detailed to justify repeated hot-path loading;
- primarily explanatory or contractual rather than a reusable semantic procedure.

Examples:

```text
INSTALL_WINDOWS.md
  first-time deployment/onboarding

LIFECYCLE_MAINTENANCE.md
  lane_clear and maintenance controls

CONVERSATION_POOL.md
  Worker generation/rollover lifecycle

SCHEDULER_MODEL.md
  dispatch and scheduler semantics
```

Prefer one canonical home and short pointers elsewhere. Do not copy the same procedure into README, AGENTS, state, and multiple docs.

## When to create a Skill

A Skill is not a place to hide documentation.

Create/promote a Skill when successful work exposes a **reusable procedure** that future tasks should invoke rather than rediscover.

A good Skill has:

- a recurring task class;
- clear applicability boundaries;
- capability/resource requirements;
- a bounded procedure;
- evidence that the procedure actually worked;
- enough abstraction to transfer beyond one incident.

Do **not** use a Skill for:

- static architecture explanation;
- policy/invariant text;
- one-off historical facts;
- installation instructions that are not a reusable semantic procedure;
- raw troubleshooting logs;
- unverified model suggestions.

The distinction:

```text
MD contract
  -> what the subsystem means / rules / boundaries / how to reason about it

Skill
  -> when this recurring problem appears, execute this proven procedure

Executor
  -> deterministic implementation of mechanical work
```

Skills should be retrieved selectively through the compact index. Loading the entire Skill library defeats the purpose.

## When to create a Case

A Case records a real run and its evidence.

Use a Case for:

- chronology;
- failures and corrections;
- representative commits;
- proof boundaries;
- measured outcomes;
- why a capability was promoted from experiment to supported behavior.

A Case is not the operational contract.

When a lesson becomes durable architecture:

```text
case proves lesson
  -> focused contract records current rule
  -> tests/manifest protect required capability
  -> repo-map routes future maintenance
```

Keep the historical detail in the Case instead of copying it into runtime docs.

## What belongs in canonical State

State answers **what now?**, not **everything we know**.

Keep:

- current phase;
- current task/action/dispatch;
- exact next action;
- minimal verified boundary;
- active blockers;
- small refs to required evidence/docs/handoffs.

Avoid:

- tutorial prose;
- duplicated subsystem contracts;
- large histories;
- full logs;
- general architecture essays.

State may point to a focused contract or Skill rather than embedding it.

## README role

README is the human entry point and architectural overview.

It should:

- explain what CAH is;
- summarize proven capabilities and major limitations;
- show the mental model;
- provide a small repository map;
- point to focused documents.

It should not become a duplicate operations manual.

A useful rule:

> README names the door; the focused document contains the room.

## repo-map role

`ai/repo-map.json` is the routing layer that makes cold knowledge practical.

When a focused document or subsystem is added:

1. give it one clear canonical purpose;
2. add a module/read-set route when an Agent may need to discover it;
3. keep it out of ordinary state-only startup;
4. define `expand_on_failure` code/tests only when deeper inspection is needed.

This allows CAH to accumulate documentation without forcing every Worker to read it.

## New feature / maintenance knowledge workflow

For meaningful new capability work, prefer:

```text
implement
  -> test
  -> live proof when required
  -> record evidence/case
  -> decide durable knowledge form
       |
       +-- frequent invariant?      -> minimal hot-path contract
       +-- subsystem contract?     -> focused MD
       +-- reusable procedure?     -> candidate Skill
       +-- historical proof only?  -> Case/evidence
  -> add repo-map routing
  -> add public/export guard if capability is core
```

A feature should not automatically create text in every layer.

## Duplication policy

Prefer:

```text
one canonical explanation
+ short links/pointers
+ machine-readable routing
```

Avoid:

```text
same detailed rule copied into
README
AGENTS
state
three docs
a Skill
```

Duplicated prose creates synchronization cost, ambiguity, and repeated model context cost.

If two documents need the same detail, choose one owner and make the other reference it.

## Refactoring trigger

Documentation/context should be refactored when:

- a hot-path file keeps growing;
- Workers repeatedly load sections irrelevant to most wakes;
- the same instructions exist in multiple places;
- a troubleshooting lesson has matured into a stable subsystem contract;
- repeated task reasoning has matured into a reusable Skill;
- a large state file contains historical knowledge rather than current frontier;
- multi-lane expansion multiplies unnecessary context reads.

Context bloat is a performance regression even when functional tests still pass.

## Design rule for future maintainers

When adding knowledge, do not ask only:

> Is this important?

Also ask:

> How often must an executing Worker read it?

Then place it accordingly.

The preferred CAH pattern is:

```text
small hot contracts
    +
precise routed docs
    +
compact selective Skill retrieval
    +
durable cases/evidence
```

This is part of the runtime architecture, not merely documentation style.
