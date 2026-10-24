"""Recover existing Git control requests through the local Harness consumer.

Worker delivery uses its immutable request and WakeStore's durable wake ID.
Special result projection keeps the original fresh Git transaction/finalizers.
The small in-memory discovery cursor is disposable; Git remains authoritative.
"""
from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from harness.git_process import run_git
from harness.request_drain import FINAL, HOLD, process, wake_decision
from harness.request_scan import discover, hydrate, normalize_path, read_receipts, receipt_matches

KINDS = ["worker_wake", "semantic_finalize", "parallel_finalize"]
MAX_REQUESTS = 100
MAX_PENDING = 4096
RECOVERY_INTERVAL = 300.0


@dataclass
class _Discovery:
    last_sha: str | None = None
    full_scan_at: float | None = None
    scan_before: str | None = None
    scan_after: str | None = None
    scan_full: bool = False
    blocked: bool = False
    local_lanes: frozenset[str] | None = None
    cursor: tuple[str, str] | None = None
    pending: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)


def reconcile_local_dispatch(store: Any, base_sha: str, *,
                             now_fn: Callable[[], float] = time.time) -> dict[str, Any]:
    """Consume at most one bounded page, without fetching for Worker wakes."""
    with store.git_lock:
        state = getattr(store, "_local_dispatch_discovery", None)
        if state is None:
            state = _Discovery()
            store._local_dispatch_discovery = state
        now = now_fn()
        local_lanes = frozenset(part.strip() for part in str(os.environ.get("CAH_LOCAL_WORKER_LANES") or "").split(",")
                                if part.strip().startswith("lane-"))
        if state.scan_after is None:
            full = (state.last_sha is None or state.full_scan_at is None
                    or now < state.full_scan_at or now - state.full_scan_at >= RECOVERY_INTERVAL
                    or state.local_lanes != local_lanes
                    or state.blocked and state.last_sha != base_sha)
            if full or state.last_sha != base_sha:
                state.scan_before = None if full else state.last_sha
                state.scan_after = base_sha
                state.scan_full = full
                state.cursor = None
        state.local_lanes = local_lanes

        receipts: dict[str, Any] | None = None
        receipt_revision = base_sha

        def prior(entry: dict[str, Any]) -> tuple[str | None, float]:
            nonlocal receipts
            if entry.get("receipt_sha") == receipt_revision:
                return entry.get("receipt_phase"), entry.get("receipt_due", 0.0)
            if receipts is None:
                receipts = read_receipts(store.repo_root, receipt_revision)
            record = entry["record"]
            old = receipts.get(record["request_key"])
            if receipt_matches(record, old):
                phase = old.get("phase")
                retry = old.get("retry_after", 0)
                due = float(retry) if isinstance(retry, (int, float)) else 0.0
            else:
                phase, due = None, 0.0
            entry.update(receipt_sha=receipt_revision, receipt_phase=phase, receipt_due=due)
            return phase, due

        discovered = 0
        if state.scan_after is not None:
            records = discover(store.repo_root, after=state.scan_after, before=state.scan_before, kinds=KINDS)
            exhausted = True
            added = 0
            for record in records:
                position = (record["kind"], record["path"])
                if state.cursor is not None and position <= state.cursor:
                    continue
                key = record["request_key"]
                entry = state.pending.get(key, {"record": record, "attempts": 0, "retry_at": 0.0})
                phase, _ = prior(entry)
                if phase in FINAL:
                    state.pending.pop(key, None)
                    state.cursor = position
                    continue
                if phase in HOLD:
                    # Git retains held identities. A marker makes every new
                    # snapshot recheck them, including receipt-only releases,
                    # without filling all runnable slots with blocked work.
                    state.pending.pop(key, None)
                    state.blocked = True
                    state.cursor = position
                    continue
                # A new blob supersedes only the old cached identity at its path.
                for old_key, old_entry in list(state.pending.items()):
                    old = old_entry["record"]
                    if old_key != key and (old["kind"], old["path"]) == position:
                        del state.pending[old_key]
                if key not in state.pending and len(state.pending) >= MAX_PENDING:
                    retried = [(old_key, old_entry) for old_key, old_entry in state.pending.items()
                               if old_entry["attempts"] > 0]
                    if not retried:
                        exhausted = False
                        break
                    # A permanently broken request cannot monopolize discovery
                    # capacity. Evicted metadata is recoverable from the next
                    # full scan; no canonical evidence or payload is removed.
                    victim, _ = max(retried, key=lambda row: (row[1]["attempts"], row[1]["retry_at"], row[0]))
                    del state.pending[victim]
                    state.blocked = True
                state.pending[key] = entry
                state.cursor = position
                discovered += 1
                added += 1
                if added >= MAX_REQUESTS:
                    exhausted = position == (records[-1]["kind"], records[-1]["path"])
                    break
            if exhausted:
                state.last_sha = state.scan_after
                if state.scan_full:
                    state.full_scan_at = now
                state.scan_after = None
                state.scan_before = None
                state.cursor = None

        selected = []
        # Retry fairness prevents a bad request at the front hiding valid work.
        for key, entry in sorted(list(state.pending.items()),
                                 key=lambda row: (row[1]["attempts"], row[1]["record"]["kind"] == "worker_wake",
                                                  row[1]["record"]["path"])):
            phase, due = prior(entry)
            if phase in FINAL:
                state.pending.pop(key, None)
                continue
            if phase in HOLD:
                state.pending.pop(key, None)
                state.blocked = True
                continue
            if due > now or entry["retry_at"] > now:
                continue
            selected.append((key, entry, dict(entry["record"])))
            if len(selected) >= MAX_REQUESTS:
                break
        hydrate(store.repo_root, [record for _, _, record in selected])

        current_sha = base_sha
        reader_sha = None
        values: dict[str, dict[str, Any]] = {}
        cell_refs: list[str] = []

        def read(ref: str) -> dict[str, Any]:
            ref = normalize_path(ref)
            if ref not in values:
                value = json.loads(run_git(store.repo_root, "show", f"{current_sha}:{ref}").stdout.lstrip("\ufeff"))
                if not isinstance(value, dict):
                    raise ValueError(f"{ref}: expected object")
                values[ref] = value
            return values[ref]

        output = []
        canonical_commit = None
        for key, entry, record in selected:
            entry["attempts"] += 1
            try:
                actual = run_git(store.repo_root, "rev-parse", f"{current_sha}:{record['path']}", check=False)
                if actual.returncode or actual.stdout.strip() != record["blob_oid"]:
                    result = {"phase": "SUPERSEDED"}
                elif record["kind"] == "worker_wake":
                    if not record.get("valid"):
                        result = {"phase": "REJECTED", "error": record.get("error")}
                    else:
                        if reader_sha != current_sha:
                            values.clear()
                            reader_sha = current_sha
                            paths = run_git(store.repo_root, "ls-tree", "-r", "--name-only", current_sha,
                                            "--", "state/task_cells/").stdout.splitlines()
                            cell_refs = [p for p in paths if p.endswith(".json")]
                        phase, wake = wake_decision(store.repo_root, record["payload"], reader=read,
                                                    task_cell_refs=cell_refs, check_owner_cell=True)
                        result = {"phase": phase}
                        if wake is not None:
                            if wake.get("lane_id") not in local_lanes:
                                result = {"phase": "DEFERRED", "error": "lane_not_local"}
                            else:
                                phase, _ = prior(entry)
                                if phase in {"CLAIMED", "UNKNOWN"}:
                                    if receipts is None:
                                        receipts = read_receipts(store.repo_root, current_sha)
                                    old_wake = (receipts.get(key) or {}).get("wake")
                                    if isinstance(old_wake, dict):
                                        stable = lambda value: {k: v for k, v in value.items() if k != "created_at"}
                                        if stable(old_wake) != stable(wake):
                                            raise ValueError("claimed wake differs from current canonical identity")
                                        wake = old_wake
                                emitted = store.emit(wake)
                                if not isinstance(emitted, dict) or emitted.get("ok") is not True or emitted.get("wake_id") != wake["wake_id"]:
                                    raise RuntimeError("emitter did not accept the exact wake")
                                result.update(phase="DONE", duplicate=bool(emitted.get("duplicate")))
                else:
                    result = process(store.repo_root, record, remote=store.git_remote, branch=store.git_branch)
                    if result.get("canonical_commit"):
                        current_sha = str(result["canonical_commit"])
                        canonical_commit = current_sha
                        store.record_git_commit(current_sha)
                        receipts = None
                        receipt_revision = current_sha
                phase = result.get("phase")
                if phase in FINAL:
                    state.pending.pop(key, None)
                elif phase in HOLD or phase == "DEFERRED":
                    state.pending.pop(key, None)
                    state.blocked = True
                else:
                    entry["retry_at"] = max(now + 1.0, float(result.get("retry_after") or 0.0))
                output.append({"path": record["path"], "request_key": key, **{
                    k: result[k] for k in ("phase", "outcome", "error", "duplicate") if k in result
                }})
            except Exception as exc:
                entry["retry_at"] = now + min(30.0, float(2 ** min(entry["attempts"] - 1, 5)))
                output.append({"path": record["path"], "request_key": key, "phase": "RETRY",
                               "error": str(exc)[:300]})
                if record["kind"] != "worker_wake":
                    # A failed finalizer may have committed before losing its
                    # reply. Never authorize another wake from the older tree.
                    store.invalidate_git_snapshot()
                    current_sha = store.read_git_snapshot(force=True)
                    store.record_git_commit(current_sha)
                    canonical_commit = current_sha
                    break

        return {"ok": True, "source_commit": base_sha, "canonical_commit": canonical_commit,
                "discovered": discovered, "processed": len(output), "pending": len(state.pending),
                "scan_pending": state.scan_after is not None, "payloads_read": len(selected),
                "results": output, "non_idempotent_actions_executed": 0}
