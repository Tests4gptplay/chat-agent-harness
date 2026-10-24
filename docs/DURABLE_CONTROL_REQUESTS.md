# Durable control request reconciliation

Git request files are the ledger. `local_bridge/local_dispatch.py` consumes the existing `worker_wake`, `semantic_finalize` and `parallel_finalize` kinds during the Harness background control cycle, using the Bridge's confirmed immutable Git snapshot. The existing task, dispatch, fence, ownership and finalizer rules remain authoritative.

## Discovery and recovery

`harness/request_scan.py` discovers metadata from the complete changed commit range when the snapshot changes. Startup and a bounded recovery scan every five minutes inspect the canonical tree, including when no new commit arrives. Discovery hydrates only the selected request bodies, in pages of at most 100. Its in-memory cursor and pending metadata are disposable; restart rebuilds them from Git and the existing receipts and wake queue. A failed delivery can retry on an unchanged SHA, and one bad request must not starve healthy neighbors.

Request identity binds kind, repository path and immutable blob OID. Changing a request in place creates a different identity. Matching `DONE`, `SUPERSEDED` and `REJECTED` receipts are terminal; retry times remain effective, while `NEEDS_RECONCILE` stays held until explicit reconciliation changes the durable record. Completed or held entries do not consume runnable cache capacity or the next execution batch. A changed snapshot rechecks durable receipts so an explicit release can be discovered.

## Worker delivery and finalizer receipts

Local Worker delivery validates the exact current task/lane/project/dispatch/generation/fence and task owner from the same snapshot, and emits only lanes configured in `CAH_LOCAL_WORKER_LANES`. It uses the canonical stable wake ID and WakeStore's persistent deduplication rather than creating a new Git claim and receipt commit for every local delivery. Restart or an unknown emit response may retry only that exact ID. Fresh Worker admission still checks canonical identity before a semantic wake; queue acceptance is not proof of a model response or task completion. Paused, old-generation and protected control-role targets cannot become ordinary Worker wakes.

Semantic and parallel finalizers still use `harness/request_drain.py`'s existing fresh Git transactions. Projections and their `state/request_receipts/` records are committed together; a publication race re-reads and recomputes instead of rebasing a stale projection. The parallel finalizer retains its immutable branch-evidence checks. Business ERROR/BLOCKED remains a durable outcome and stale identities have no task-state side effects. A finalizer exception invalidates the snapshot, forces a fresh read and stops the current page; a failed refresh fails the tick. An unknown publication outcome is not reported as a successful rollback or used to authorize another wake from the old tree.

The local consumer replaces the three event-triggered control workflows and their fifteen-minute Actions backstop as one cutover; they must not run as a second automatic consumer. The request scanner, drain functions and explicit diagnostic/repair entry points remain available. Actions concurrency queues are not the control ledger.

## Deliberate boundary

This driver does not run Stage0 executors or host-update scripts. Those operations can have irreversible local effects and need an immutable pre-execution claim plus result/process reconciliation. They must never be retried merely because a job or response was lost. Issues #43/#44 remain open for that non-idempotent execution path until its separate integration is accepted.

Existing historical failures/evidence remain intact. This module does not release Task Cells, delete projects/tabs, change models, or upgrade the Windows host.

## Behavioral verification

`python -m unittest discover -s tests -p 'test_request*.py' -v` exercises complete-range discovery, content mutation, receipt starvation, wake crash recovery, ownership gates, semantic failure publication, stale zero-mutation, immutable branch evidence and simultaneous Git publication races. `python -m unittest discover -s tests -p 'test_local_dispatch.py' -v` covers missed events, restart, unchanged-SHA retry, local lane filtering, bounded payload discovery, persistent wake deduplication and both existing finalizer transactions.
