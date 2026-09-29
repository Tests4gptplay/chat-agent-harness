# Record archive

CAH separates active control state from cold history.

## Active repository

The active CAH repository contains current task/control state and one cold-store locator:

```text
state/record-store.json
 -> repository
 -> INDEX.json
```

Normal agents do not read archived task content.

## Record repository

Configured repository:

`CAH_OWNER/CAH_RECORD_REPOSITORY`

Archive layout:

```text
INDEX.json
records/<task_id>/
  task.json
  plan.json
  handoffs/
  receipts/
  control/
  evidence-index.json
  archive.json
```

Large user/source assets remain in their owning storage unless archival of those assets is explicitly requested.

## INDEX

`INDEX.json` is the authoritative locator for archived tasks.

Each archived task entry records enough immutable identity to find and verify its archive, for example:

```json
{
  "records": {
    "<task_id>": {
      "path": "records/<task_id>/archive.json",
      "archive_commit": "<commit>",
      "status": "ARCHIVED"
    }
  }
}
```

The active CAH repository does not keep a per-task archive pointer.

## Bulk Git-object archive

For already-committed cold history, a bulk archive may store an immutable Git-object manifest instead of duplicating every file body. The manifest records the source repository, immutable source commit/tree, original path, blob SHA and size for every archived item. Because ordinary Cleaner operations do not rewrite Git history, this identity is sufficient to recover the exact archived bytes while keeping them out of the active tree.

The same transaction rule applies: write and index the manifest in the record repository, read it back successfully, then remove those paths from the active tree. Normal agents do not scan source Git history during hot-path execution.

## Archive transaction

```text
active task records
 -> write records/<task_id>/...
 -> update record-repo INDEX.json
 -> read back indexed task + immutable archive commit
 -> remove archived cold records from active CAH tree
```

If archive or INDEX verification fails, active records remain unchanged.

After successful archival, task/plan bodies, retired handoff/takeover packets, terminal receipts and other archived control records may be removed from the current active repository tree. Git history is not rewritten by ordinary Cleaner operations.

After INDEX readback succeeds, terminal control-plane records should not remain in the active tree merely as historical context. Keep active state for current or explicitly paused work; keep cold history in the record repository.

Archive access is cold-path context used only for explicit history lookup, audit, recovery or user-requested archive/cleanup work.
