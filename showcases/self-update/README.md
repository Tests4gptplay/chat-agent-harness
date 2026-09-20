# Showcase: CAH updates its own runtime

This case records the normal-task optimization release moving from tested Git source into an already-running Windows installation, followed by an actual one-lane task. It is a **sanitized factual summary**, not public access to the original private run logs.

## The loop

```text
Foreground confirms the update
  → focused source changes + tests + Task Cell implementation review
  → accepted code merged in the private runtime repository
  → existing runner receives host-update request
  → installed checkout updated and extension built
  → bridge restarted; extension reload requested
  → extension performs native reload
  → fresh hello reports version 1.0.4, both bindings and 30s mailbox interval
  → one existing Worker performs a bounded task and returns an artifact
```

The recorded host update took approximately **43.3 seconds**. Code update, extension build, bridge restart and native extension reload all reported PASS. Chrome restart was not requested. Both managed tabs reconnected successfully, with no failed tab in that readback. The original task-level control Project was not released by Worker maintenance.

## What changed, and what was measured

| Measurement | Before | After |
|---|---:|---:|
| Local request-discovery Git processes | 605 | 5 |
| Request bodies read | 201 | 1 |
| Windows discovery median, three runs | 17,957.227 ms | 156.727 ms |
| Hot canonical state UTF-8 bytes | 45,803 | 5,897 |

The fixture contained 200 completed requests and one pending request. Discovery uses metadata and matching receipts before loading pending bodies; immutable reads are batched. The timing excludes remote fetch, Actions queueing, browser handling, model reasoning and executor work. It must not be described as a 114× end-to-end speedup.

Normal mailbox polling was separated from heavier maintenance. Already-recorded admission preflight is reused; terminal dispatches stop repeated liveness probes. The observed mailbox configuration was 30 seconds, not a promise that a task always starts within 30 seconds.

## Actual post-update task

One existing Worker wrote a short release note in a single semantic turn. No second lane was dispatched and no additional Task Cell approval was inserted into that writing task. The runtime recorded response-start admission and finalized the matching result as GREEN / DONE.

Wake commit → result commit: **98 seconds**. Wake commit → backend finalization: **125 seconds**. These are one post-update observation, not an old/new comparison or an exact first-token measurement. Writing would normally remain in the foreground; it was delegated once here to exercise the runtime loop.

The shared wake workflow initially appeared red because an unrelated obsolete request lacked current dispatch identity. The new request had succeeded. The obsolete request was individually retired after inspection, preserving its history instead of replaying the old workload. The report does not hide that operational intervention.

## What this supports

CAH can use its existing Git/runner/bridge/extension path to deploy changes to its own installation and verify the new runtime before returning to work. It does not establish that every future update is failure-free, that all model selection is implemented, or that ordinary delegation is instantaneous.

See [machine-readable summary](evidence.json), [normal-task policy](../../docs/NORMAL_TASK_PATH.md), and [operations limits](../../docs/OPERATIONS.md). Raw host paths, account IDs, Project URLs, private commit identifiers and browser/session data are intentionally absent.
