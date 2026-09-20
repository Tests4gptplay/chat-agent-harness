# CAH Skill accumulation and reuse

Status: active architecture contract.

CAH Skills are **evidence-backed reusable procedures**, not conversational memories and not arbitrary prompts. The goal is for completed work to reduce the reasoning and rediscovery cost of later work without turning unverified model guesses into durable behavior.

## Memory vs Skill vs executor

```text
chat / Worker context   -> hot temporary working set
task checkpoint         -> resumable task-local state
project memory          -> durable facts/decisions about one project
Skill                   -> reusable semantic procedure + applicability + evidence
executor/script         -> deterministic/mechanical implementation
Git evidence            -> authoritative proof of what actually happened
```

A Skill may point to executors/scripts, but the Skill itself answers **when to use a procedure, what sequence to follow, what constraints apply, and what evidence supports it**.

## Canonical layout

```text
skills/index.json
skills/candidates/<skill_id>.json
skills/active/<skill_id>.json
skills/deprecated/<skill_id>.json
harness/skill.schema.json
harness/skills.py
```

`skills/index.json` is a compact retrieval index. The referenced Skill JSON is authoritative.

## Lifecycle

```text
successful task
   |
   v
Worker asks: is there a reusable procedure here?
   | no -> ordinary task evidence only
   |
  yes
   v
CANDIDATE Skill
   |
   +-- applicability / capabilities / constraints
   +-- reusable procedure
   +-- implementation refs
   +-- terminal result evidence
   |
   v
deterministic validation
   |
   +-- refs exist
   +-- result task_id matches
   +-- declared outcome matches result status
   +-- at least one verified PASS before activation
   |
   v
ACTIVE Skill
   |
   +-- Planner retrieval on later tasks
   +-- subsequent PASS/FAIL evidence is appended
   +-- use_count accumulates
   |
   v
DEPRECATED when evidence or architecture makes it unsafe/stale
```

The model may propose a candidate, but **model authorship alone never proves a Skill**.

## Planner reuse

For a non-trivial task, the Planner may prefilter active Skills with:

```sh
python harness/skills.py match --query "iterative Blender asset visual refinement" --capability blender --capability python
```

This is an advisory retrieval step. User requirements, current repository state, tool capability, and fresh evidence override a Skill whenever they conflict.

Do not load every Skill into context. Read the compact index first, then only the selected Skill refs.

## Accumulation after work

After a terminal successful task, a Worker should create/update a candidate only when the work exposed a procedure likely to recur. Good candidates have:

- a stable applicability boundary;
- a reusable multi-step procedure;
- explicit capability/resource requirements;
- one or more durable terminal result refs;
- enough abstraction to apply beyond the exact original artifact.

Avoid turning one-off facts, aesthetic preferences, raw logs, or task-specific coordinates into Skills.

## Evidence updates

Every later real use should append its terminal result:

```sh
python harness/skills.py record-evidence \
  --skill skills/active/<skill_id>.json \
  --task-id <task_id> \
  --result-ref results/<task>.json
```

PASS and non-PASS outcomes are both retained. Failures are not silently discarded. The registry exposes success/failure/use counts so the Planner can see whether a procedure is repeatedly holding up.

## Promotion

A candidate may be promoted only when deterministic validation can resolve at least one terminal PASS result:

```sh
python harness/skills.py promote --candidate skills/candidates/<skill_id>.json
```

Promotion is a trust boundary, not a formatting change.

## First proven Skill

The first active Skill is distilled from `blender-retro-camera-001`: a bounded Blender procedural generation + render/review/refinement loop. It exists as a concrete demonstration that a successful CAH workload can become reusable procedural knowledge without confusing the original case history with the Skill itself.


## External Skill imports

External procedure libraries may be reconciled into the same registry without bypassing the trust boundary. The first adapter is the local Codex user Skill importer documented in `docs/CODEX_SKILL_IMPORT.md`.

Imported procedures enter as `CANDIDATE`. Source provenance, hashes and sanitized snapshots are retained, but external origin is not treated as terminal CAH execution evidence. Existing candidates are preserved when an external source changes so deterministic synchronization cannot overwrite later semantic refinement.
