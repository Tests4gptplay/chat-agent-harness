# Codex user Skill import

CAH can incrementally reconcile a local Codex user Skill directory into the Git-backed Skill system.

## Source discovery

The importer reads `GAH_CODEX_SKILLS_ROOT` when set and otherwise uses `~/.codex/skills`. On the user's current Windows host this resolves to the existing Codex user Skill directory without committing that absolute filesystem path.

Any path component named `.system` is always excluded.

The source machine's absolute path is never written to Git. Repository evidence uses only logical identities such as `codex_user_skills/<skill-name>`.

## Incremental reconcile

This is deliberately **not** a high-frequency filesystem watcher.

```text
first reconcile
  -> scan safe user Skill files
  -> hash canonical text
  -> write sanitized source snapshot + per-Skill manifest
  -> create CAH CANDIDATE when absent
  -> write imports/codex-skills/index.json

later reconcile
  -> recompute fingerprints
  -> unchanged: keep existing candidate
  -> new: create candidate
  -> changed: refresh source snapshot/manifest but preserve existing candidate
  -> removed: record removal in import index; do not auto-delete/deprecate Skill
```

Preserving an existing candidate on source change prevents deterministic synchronization from overwriting later semantic refinement by a Worker.

The intended trigger is a lightweight reconcile at CAH Host startup/maintenance or before a Skill-planning session when the local import is stale. Reconcile should commit only when source state changed materially.

## Trust and privacy boundary

Imported Codex Skills start as `CANDIDATE`, never `ACTIVE`.

The importer:

- skips binary and oversized files;
- refuses to snapshot text matching common private-key/token/password patterns;
- keeps safe text snapshots plus hashes and logical source identities;
- infers only lightweight title/summary/capability hints;
- never records the user's absolute local source path;
- leaves semantic refinement and evidence-backed promotion to the normal CAH Skill lifecycle.

A Codex Skill may already be excellent, but its provenance alone is not terminal CAH execution evidence.

## Artifacts

A successful reconcile writes:

- `imports/codex-skills/index.json`
- `imports/codex-skills/sources/<skill>/source.md`
- `imports/codex-skills/sources/<skill>/manifest.json`
- `skills/candidates/codex-<skill>-v1.json` for newly observed Skills

The candidate points back to the sanitized source snapshot. Later Workers may refine it into native CAH procedure steps and promote it only after the Skill Registry evidence gate is satisfied.
