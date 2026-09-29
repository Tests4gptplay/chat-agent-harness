# Cleaner rules

Cleanup is deterministic. Distinguish automatic task-owned chat cleanup from explicit deep resource reclamation.

## Normal managed-task completion

A managed Task Cell automatically cleans its own disposable conversations after Planner accepts the parent task. This is not unrestricted Deep Cleaner or Project-wide deletion.

Normal completion is:

```text
Planner complete
-> semantic authority closes
-> Playwright deletes this task/epoch's remaining Worker and Planner/Helper chats
-> archive process records to the bound engineering directory and verify readback
-> remove only the archived task-owned CAH continuity caches
-> Terminal State Restore
-> final delivery / cleanup status reaches Foreground
-> DONE
```

Terminal State Restore releases hot machine ownership:

- Planner runtime/wait/inbox/successor state;
- active Planner/Helper browser bindings;
- active Worker owner / rollover pointers;
- local Worker current/handoff pointers.

Before releasing conversation bindings, confirm deletion of the exact task-owned chats: all retained Worker generations in its lane pools and Planner/Helper in Task Cell. Retrying or diagnosing a failed cleanup must retain the ownership needed to address the same exact conversations; do not forget their ids and claim deletion.

It preserves durable history and non-target resources:

- Task Contract and Plan;
- Planner Memory and Worker Child Reply content, archived before CAH cache removal;
- Worker/Planner handoff history;
- accepted results and evidence;
- Project containers, other tasks' and unknown conversations, and the user's Foreground chat;
- branches/worktrees and task sandboxes unless separately approved for reclamation.

Planner's semantic acceptance remains a fact if deletion fails, but Harness must not report chat cleanup/reset as successful until the required deletion is observed. Surface the concrete operational fault through the existing runtime/Helper path. No extra user clean command is required for the automatic task-owned conversation cleanup already authorized by this lifecycle.

During normal execution, Helper `done` deletes only that single-use Helper after result receipt; Planner `done` keeps Planner. Planner `handoff` replaces its predecessor, while Worker `continue` uses the task-owned retention rule. None of these alone authorizes full task cleanup.

## Engineering files are never cleanup targets

Clean/reset restores CAH working state. It never deletes or resets the actual local engineering project, source files, user assets, build outputs or accepted deliverables. It never recursively cleans an engineering directory.

Foreground binds the existing absolute engineering directory in the Task Contract `project_directory`. This is a destination/path, not model-maintained bookkeeping. At terminal cleanup Harness mechanically copies the original Task/Plan, owned Child Tasks, Planner Memory (including `plan_note.md`), Worker Replies, turn records and final acceptance into `records.md` there. Records are copied verbatim with source paths and hashes; roles do not spend tokens rewriting a retrospective.

Harness writes and reopens the archive before removing any CAH record. If `records.md` already contains different content, retain it and use a task/digest-qualified filename. Write/readback failure prevents cache removal and reset; the original Git records remain. Legacy tasks without a bound engineering directory retain all records instead of inventing a destination.

Only the archived task's `memory/planner/<task>/`, `memory/worker/<owned-child>/`, `state/planner_turns/<task>/` and `state/worker_turns/<owned-child>/` tracked files are removed from the active Git tree. Keep Task/Plan, final artifacts, cleanup receipts, unrelated tasks and Git history. The archive receipt is `evidence/<task>/cleanup/project-records-g<epoch>.json`, recording the absolute archive path, content hashes, source commit and exact removed refs. This normal local project backup does not authorize the separate cross-repository archive operation below.

## Explicit Deep Cleaner

Deep Cleaner runs only from an explicit cleanup request. In particular, automatic chat cleanup is not approval to delete a branch/worktree, reclaim a task sandbox, remove source assets or clear an entire Project.

The deterministic janitor accepts an exact cleanup manifest and applies the existing ownership/retention policy.

Supported user intent is expressed as:

```text
clean task <task>
clean completed
clean all
```

The scope means:

- `clean task <task>`: clean safely removable CAH-owned residue for the named task;
- `clean completed`: clean safely removable residue for completed tasks;
- `clean all`: clean all safely removable CAH-owned residue.

The scope never means arbitrary repository or Project deletion.

Cleanup preserves:

- active task authority;
- unknown/unowned resources;
- SOURCE and user assets;
- final/protected evidence;
- release/LKG material;
- live rollback-window material;
- resources whose retention/dependency gate has not cleared.

The current low-level execution primitive is `planner_cleanup_execute`, which consumes the exact task-owned cleanup manifest. Project-wide thread clearing remains a separate explicit maintenance primitive.

## Project conversation maintenance

Explicit full Project conversation clearing remains available:

- Worker lanes use `lane_clear(scope=all_project_conversations)`;
- CAH Task Cell uses `task_cell_project_clear(scope=all_project_conversations)`.

These operations preserve Project registration/binding and are separate from normal task completion.

Unknown conversations outside exact CAH ownership stay outside task-scoped cleanup.

## Record archive

Long-term archive work remains explicit.

Archive flow:

1. collect the exact task-owned cold records;
2. write them to `records/<task_id>/...` in the record repository;
3. update the record repository `INDEX.json`;
4. read back the indexed task entry and immutable archive commit;
5. remove only the archived active-tree records covered by that successful archive.

Cleanup/archive failure leaves the task's semantic completion intact.
