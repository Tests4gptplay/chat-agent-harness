# CAH Wake Bridge WebExtension

Status: **1.0.2 control + execution browser host**.

Version 1.0.2 separates the persistent task-level control host from replaceable backend execution capacity. The extension manages a protected **Task Cell** binding alongside backend ChatGPT lanes and their replaceable Worker conversations.

## 1.0.2 Task Cell boundary

The configured Task Cell is:

```text
CAH Task Cell
project_key = g-p-exampletaskcell
root        = https://chatgpt.com/g/g-p-exampletaskcell-cah-task-cell/project
```

It is a **control-plane Project**, not an execution lane:

- no `lane_id`;
- no 5+1 Worker pool;
- no Worker rollover/retirement;
- no backend topology registration;
- normal lifetime is task-bound;
- normal release is gated on authoritative result delivery to Foreground;
- intended resident roles are Planner, Watchdog and Helper.

The popup stores this binding separately under `taskCellBinding`. Backend topology remains under `laneRegistry` and canonical `state/lanes.json`. A Project key may not be registered in both domains.

This 1.0.2 boundary establishes the browser-side host and lifecycle protection only. Planner/Watchdog/Helper dispatch protocols can be added on top without pretending the Task Cell is another disposable Worker lane.

## Normal responsibilities

1. poll the localhost wake bridge;
2. route each backend wake by `lane_id + worker_project_key`;
3. wake the verified current Worker for that lane;
4. bootstrap a Worker from that lane's ChatGPT Project root when no current Worker exists;
5. manage each lane's independent 5+1 Worker-generation lifecycle and retirement;
6. provide a small popup for desired backend topology;
7. keep small disposable local diagnostics.

Foreground progress/success notification is no longer part of the normal path. The human-facing Foreground Supervisor monitors Git CL state directly. Foreground browser recovery can be added/used separately without requiring a normal exact foreground URL binding.

## Built-in bridge defaults

Normal users do not need to edit:

```text
project_id = git-agent-harness
endpoint   = http://127.0.0.1:8765/api
```

The popup keeps these only under Advanced as developer overrides.

## Popup

The normal popup shows the protected Task Cell first, then backend execution lanes.

The Task Cell has its own exact Project-root binding and **Save / Open / Status** controls. Saving it never stages a backend topology change.

Each execution lane is configured with:

- display name, e.g. `CAH Sandbox0`;
- exact ChatGPT Project root URL;
- enabled flag.

The extension derives the exact `g-p-...` project key from the URL and assigns a stable `lane-XX` id.

Buttons:

- **Add lane**: add a desired backend lane;
- **Remove**: remove it from desired topology; the ChatGPT Project itself is never deleted;
- **Status**: show local Worker-pool state plus canonical Git lane state;
- **Save topology**: stage a Git-backed topology control request and wake a live control Worker to reconcile canonical `state/lanes.json`;
- **Refresh**: re-read local and canonical topology state.

The first 0.5.0 migration build refuses a desired topology with zero lane entries. This keeps one control lane available until the final-lane shutdown path is separately live-proven.

## Desired vs canonical topology

```text
popup desired lanes
  -> localhost bridge stages state/topology_request.json
  -> state/chatgpt.json.control_request = PENDING
  -> lane-addressed wake
  -> live control Worker
  -> reconcile state/lanes.json
  -> control_request DONE/ERROR
  -> popup shows Synced/Pending
```

Git `state/lanes.json` is authoritative. Browser local storage is not task/topology truth.

## Legacy migration

On first 0.5.1 load, existing pre-lane Worker runtime cache is deliberately discarded. Only the registered `CAH Sandbox0` Project configuration is retained as `lane-00`; `current/managed/handoff` conversation cache is reset and a fresh Worker is bootstrapped from canonical Git state. This avoids reopening deleted legacy conversation URLs.

The old fixed-project runtime files remain in source/build output temporarily for rollback/reference, but 0.5.0 manifests no longer load them.

## Build

```sh
python extension/build.py chromium
python extension/build.py firefox
```

Load `extension/dist/chromium` as an unpacked Chromium extension.

For the full Windows setup, including Chrome's `chrome://extensions` Developer mode / **Load unpacked** flow, self-hosted runner setup, ChatGPT lane Project registration, and the `Start_CAH.bat` one-click launcher, see `docs/INSTALL_WINDOWS.md`.

The future public package must keep this browser-extension installation path reproducible while removing maintainer-specific Project keys, account URLs, and filesystem defaults.

## Background UI recovery layer

Version 1.0.2 promotes ChatGPT UI-obstruction recovery to a shared browser-runtime concern rather than a Task Cell or Worker special case.

Before managed UI operations, CAH runs a bounded preflight on the existing tab. The preflight recognizes only the specific conversation-history access restriction dialog, clicks its affirmative dismissal control, waits for the modal to disappear, and then resumes the original operation without changing task, dispatch, generation, fence, lane, or Project identity.

The same recovery path is used by backend Worker bootstrap, Worker semantic submission, Project-root list/delete operations, and Task Cell control delivery. A periodic background maintenance pass also checks existing managed Worker/Task Cell tabs without opening new pages. Ambiguous dialogs fail closed.

## Conversation-history access modal recovery

High-frequency lane/Worker opening and switching can cause ChatGPT to show a modal headed `请求过于频繁` whose body says that access to **conversation history** is temporarily restricted.

CAH treats this specific modal as a browser-UI obstruction, not as semantic/executor failure and not as proof that prompt sending is rate-limited. When the dialog is unambiguously identified, the content script clicks the affirmative dismissal button, waits for the modal to disappear, reacquires the composer/send controls, and continues the **same** pending wake without changing task/dispatch/generation/fence identity.

A generic `请求过于频繁` message is not enough to trigger this recovery. The dialog must explicitly identify conversation-history access as the restricted resource. Ambiguous dialogs fail closed.

## Safety

- Task Cell binding is stored outside lane topology and is rejected if its Project key collides with an execution lane;
- Task Cell is excluded from 5+1 Worker retirement/cleanup by construction;
- wake routing validates exact lane id and Project key;
- display names are never safety identities;
- Worker tabs are opened inactive;
- unknown/manual ChatGPT conversations are not automatically deleted;
- removal of a lane from CAH does not delete its ChatGPT Project;
- stale/ambiguous UI actions fail closed;
- Git remains canonical.

## Local diagnostics

Typical root:

```text
C:\CAH\runtime\
  extension-events\
  errors\
```

This spool is disposable.


## Runtime epoch 3 / Sandbox1 activation

Version 0.7.0 promoted the previously reserved `CAH Sandbox1` Project into the default two-lane topology.

The migration is one-time and fail-closed:

- existing lane-00 runtime state is preserved;
- lane-01 is registered only with its exact Project key/root;
- a `bootstrap_parallel` transaction stages the normal Git-backed topology reconciliation;
- the control wake is handled through the already-proven lane-00 path;
- each lane then obtains its own takeover-verified current Worker;
- the transaction reaches `DONE` only when both enabled lanes are verified.

The popup should therefore converge to:

```text
Desired: 2 | Canonical: 2 (2 enabled) | Synced
```

This is a deployment/topology proof. A later application case must still demonstrate overlapping semantic work before claiming a real parallel workload.
