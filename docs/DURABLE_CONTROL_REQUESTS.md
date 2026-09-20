# Durable control request reconciliation

Push events are doorbells, not the request ledger. `harness/request_scan.py` can read an exact complete push range or the current canonical tree; `harness/request_drain.py` uses the latter for Worker wakes, semantic finalizers and parallel finalizers.

A receipt under `state/request_receipts/` is keyed by request kind, repository path and immutable blob OID. Requests changed in place get a different identity. Projections and receipts are committed together from a fresh canonical worktree. A publication race is resolved by re-reading and recomputing, never by rebasing a stale calculated state. Completed receipts and backoff entries do not consume the next drain batch limit.

Wake delivery first commits an exact current task/lane/project/dispatch/generation/fence claim. Only the exact stable wake ID may be retried after an unknown transport outcome: the loopback bridge explicitly deduplicates that operation. Extension admission reconciliation remains responsible for preventing semantic message reinjection. A mailbox receipt is not proof of model response or task completion. Paused, old-generation and protected control-role targets cannot become ordinary Worker wakes.

Failures in one request do not hide healthy neighbors. Business ERROR/BLOCKED is durably published before a nonzero job status is reported. Rejected stale identities have no task-state side effects. Unknown control transport outcomes are recorded explicitly rather than misreported as successful rollback.

The three event-triggered control workflows call the same driver, and a bounded fifteen-minute backstop scans independently of push delivery. GitHub `queue: max` increases the waiting-run capacity; it is not the authoritative queue and does not replace receipts.

## Deliberate boundary

This driver does not run Stage0 executors or host-update scripts. Those operations can have irreversible local effects and need an immutable pre-execution claim plus result/process reconciliation. They must never be retried merely because a job or response was lost. Issues #43/#44 remain open for that non-idempotent execution path until its separate integration is accepted.

Existing historical failures/evidence remain intact. This module does not release Task Cells, delete projects/tabs, change models, or upgrade the Windows host.

## Behavioral verification

`python -m unittest discover -s tests -p 'test_request*.py' -v` exercises complete-range discovery, missing events, multiple requests, content mutation, completed-entry starvation, idempotent wake crash recovery, wrong acknowledgements, current ownership gates, semantic failure publication, stale zero-mutation, immutable branch evidence, throwing-handler rollback and simultaneous Git publication races.
