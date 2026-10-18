# Storage and retention policy

Purpose: keep long-running Git/runner automation from filling the execution host while preserving reproducibility and recovery evidence.

This policy applies to files created or managed by `git-agent-harness` executors. It MUST NOT be used as authority to delete arbitrary user files outside explicit managed roots.

## Storage classes

Every executor-produced path should be classified as one of:

### SOURCE
Authoritative inputs that cannot be reconstructed safely from other retained data.
Examples: source `.blend`/project files, hand-authored scripts/configs, release source assets, accepted user edits.

Policy:
- never auto-delete;
- may be versioned externally or referenced by path/hash;
- cleanup code must treat SOURCE as immutable/protected.

### EVIDENCE
Small/medium outputs needed to prove what happened.
Examples: preview PNGs, compact JSON reports, failure summaries, selected logs, hashes, validation snapshots.

Default policy:
- keep evidence for the active revision;
- keep the last-known-good (LKG) revision;
- keep pinned/user-selected revisions;
- keep recent failed-revision evidence long enough for diagnosis;
- raw evidence may expire, but compact facts/hashes needed for Git recovery should be written back before deletion.

Suggested default TTL: 7 days for unpinned raw evidence, configurable per executor/project.

### CACHE
Fully reproducible data whose loss only costs time.
Examples: dependency caches, generated intermediates, DDC-like caches, downloaded reproducible tool payloads.

Policy:
- LRU/age cleanup under disk pressure;
- may be deleted at any time when not in active use;
- never treat cache existence as proof of a completed task.

### SCRATCH
Single-run temporary data.
Examples: unpack directories, staging folders, temporary renders, transient stdout/stderr captures before compaction.

Policy:
- delete after successful task finalization;
- failed/crashed task scratch may remain briefly for diagnosis;
- default crash-orphan TTL: 24 hours.

## Protected set

The janitor MUST NOT delete:

- any SOURCE path;
- the currently executing task/revision;
- files explicitly marked `pinned`;
- the current last-known-good revision and its minimum evidence set;
- a currently published release or release source;
- any artifact referenced by a live state/action/result file unless the reference explicitly declares it reconstructable;
- anything outside configured managed roots.

## Disk-pressure guard

Cleanup is not only periodic. Every potentially large job should perform a preflight check.

Recommended configurable thresholds:

- `min_free_gb`: absolute reserve that must remain free;
- `min_free_percent`: percentage reserve that must remain free;
- `managed_quota_gb`: optional maximum size for Harness-managed disposable data;
- `target_free_percent`: cleanup target, higher than the hard minimum to avoid repeated thrashing.

A job that cannot satisfy the hard reserve after safe cleanup should stop as `BLOCKED_STORAGE` / `NEED_USER` rather than continue writing until the disk is full.

Do not hard-code one disk-size assumption into the protocol; projects/hosts may override the numeric thresholds.

## Cleanup order

When cleanup is needed, delete only within managed roots and in this order:

1. expired SCRATCH from completed/abandoned runs;
2. old CACHE by LRU/age;
3. raw logs that already have compact summaries;
4. expired unpinned EVIDENCE from old superseded revisions;
5. never SOURCE/protected files automatically.

Stop as soon as the target free-space threshold is restored.

## Revision retention

For versioned project work (`rNNN` or equivalent):

- active revision: protected;
- LKG revision: protected;
- pinned/user-compared/release revisions: protected;
- old revisions may keep only compact Git metadata after disposable artifacts expire;
- large derived artifacts do not become permanent merely because the revision number is immutable.

Revision immutability means history/identity must not be rewritten; it does **not** require retaining every generated binary forever.

## Logs

- Keep compact result/diagnostic JSON in preference to full logs.
- Compress raw logs when retention is useful.
- Default raw-log TTL should be short (for example 3-7 days) unless pinned by an unresolved incident.
- Before deleting a log that contains a durable root cause/fix, write the compact fact back to Git state/event/reference first.

## GitHub Actions artifacts

Use short retention for disposable CI evidence. Current smoke workflows use 3 days; larger production workflows should not increase this without a reason.

GitHub-hosted artifacts and local-runner artifacts are separate stores; both need retention rules.

## Safe deletion requirements

A janitor implementation must:

1. resolve/canonicalize every target path;
2. verify it is below an allow-listed managed root;
3. reject symlink/junction traversal outside that root;
4. check protected/pinned/active references;
5. support dry-run/report mode;
6. log what was deleted, why, bytes reclaimed, and resulting free space;
7. fail closed on ambiguous ownership/classification.

Never use broad cleanup commands against user project roots merely because they are adjacent to Harness-generated data.

## Suggested local layout

```text
<managed-root>/
  source/      # protected authoritative inputs
  evidence/    # previews/reports/selected logs
  cache/       # reproducible caches
  scratch/     # per-run temporary data
  runtime/     # tiny wake/state transport files
```

A consumer such as `3d-agent-lab` may keep its authoritative project source elsewhere and point the Harness only at dedicated evidence/cache/scratch roots.

## Janitor cadence

Recommended behavior once a self-hosted runner exists:

- preflight before every potentially large job;
- finalize/cleanup SCRATCH after every job;
- daily janitor while the runner is online;
- emergency janitor when free space crosses the soft threshold;
- no autonomous deletion outside the policy-defined managed roots.

## Core invariant

**Git should remember enough to resume; the disk should retain only what cannot be safely reconstructed or what is still useful evidence.**
