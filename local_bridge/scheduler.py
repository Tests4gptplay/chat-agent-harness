#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import harness.parallel_branch_finalize as parallel_branch_finalize
from harness.parallel_branch_finalize import (
    ParallelBranchFinalizeError,
    apply_branch_result,
    branch_result_ref,
)
from harness.semantic_finalize import finalize as finalize_semantic_result

try:
    from .topology import _run, utc_now
    from .task_cell_ledgers import (
        worker_child_reply_path,
        worker_turn_reply_entry_header,
        worker_turn_reply_entry_path,
        worker_reply_has_write,
    )
except ImportError:
    from topology import _run, utc_now
    from task_cell_ledgers import (
        worker_child_reply_path,
        worker_turn_reply_entry_header,
        worker_turn_reply_entry_path,
        worker_reply_has_write,
    )


def _safe_rel(value: Any, name: str) -> str:
    s = str(value or "").replace("\\", "/").strip()
    p = Path(s)
    if not s or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"invalid {name}")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-/")
    if any(ch not in allowed for ch in s):
        raise ValueError(f"invalid {name}")
    return s


def _safe_id(value: Any, name: str) -> str:
    s = str(value or "").strip()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    if not 3 <= len(s) <= 256 or any(ch not in allowed for ch in s):
        raise ValueError(f"invalid {name}")
    return s


def _condition(cl: dict[str, Any], cid: str) -> dict[str, Any] | None:
    for item in cl.get("conditions") or []:
        if isinstance(item, dict) and item.get("id") == cid:
            return item
    return None


def _task_pool_key(owner_task_id: str, owner_control_epoch: int) -> str:
    owner = _safe_id(owner_task_id, "owner_task_id")
    epoch = int(owner_control_epoch)
    if epoch < 1:
        raise ValueError("owner_control_epoch must be positive")
    return f"{owner}::{epoch}"


def _worker_pool_identity(bg: dict[str, Any], fallback_task_id: str) -> tuple[str, int]:
    scheduling = bg.get("scheduling") if isinstance(bg.get("scheduling"), dict) else {}
    owner_task_id = _safe_id(
        scheduling.get("owner_task_id") or fallback_task_id,
        "owner_task_id",
    )
    owner_control_epoch = int(scheduling.get("owner_control_epoch") or 1)
    if owner_control_epoch < 1:
        raise ValueError("owner_control_epoch must be positive")
    return owner_task_id, owner_control_epoch


def canonical_worker_owner(
    lanes: dict[str, Any],
    *,
    lane_id: str,
    worker_project_key: str,
    owner_task_id: str,
    owner_control_epoch: int,
) -> tuple[str, bool, str]:
    """Resolve the exact task-owned Worker pool authority for one physical lane."""
    lane_record: dict[str, Any] | None = None
    if isinstance(lanes, dict):
        for item in lanes.get("lanes") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("lane_id") or "") != lane_id:
                continue
            if str(item.get("project_key") or "") != worker_project_key:
                return "", False, "lane_project_mismatch"
            lane_record = item
            break
    if lane_record is None:
        return "", False, "lane_missing"

    pools = lane_record.get("task_pools")
    if not isinstance(pools, dict):
        return "", False, "task_pool_missing"
    key = _task_pool_key(owner_task_id, owner_control_epoch)
    pool = pools.get(key)
    if not isinstance(pool, dict):
        return "", False, "task_pool_missing"
    if (
        str(pool.get("owner_task_id") or "") != str(owner_task_id)
        or int(pool.get("owner_control_epoch") or 0) != int(owner_control_epoch)
    ):
        return "", False, "task_pool_identity_mismatch"
    return str(pool.get("last_pool_takeover_id") or "").strip(), False, "task_pool_owner"


def stage_action_submit(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")

    task_id = _safe_id(req.get("task_id"), "task_id")
    backend_cl_rel = _safe_rel(req.get("backend_cl"), "backend_cl")
    foreground_cl_rel = _safe_rel(req.get("foreground_cl"), "foreground_cl")
    dispatch_id = _safe_id(req.get("dispatch_id"), "dispatch_id")
    generation = int(req.get("dispatch_generation") or 0)
    fence_token = str(req.get("fence_token") or "")
    worker_ref = _safe_id(req.get("worker_ref"), "worker_ref")
    lane_id = str(req.get("lane_id") or "")
    worker_project_key = str(req.get("worker_project_key") or "")
    action = req.get("action")

    if generation < 1:
        raise ValueError("dispatch_generation must be positive")
    if not 8 <= len(fence_token) <= 256:
        raise ValueError("invalid fence_token")
    if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
        raise ValueError("lane identity required")
    if not isinstance(action, dict):
        raise ValueError("action object required")

    action_id = _safe_id(action.get("action_id"), "action_id")
    if str(action.get("task_id") or "") != task_id:
        raise ValueError("action task_id mismatch")
    if str(action.get("backend_cl") or "") != backend_cl_rel:
        raise ValueError("action backend_cl mismatch")
    if str(action.get("foreground_cl") or "") != foreground_cl_rel:
        raise ValueError("action foreground_cl mismatch")
    if str(action.get("lane_id") or "") != lane_id:
        raise ValueError("action lane_id mismatch")
    if str(action.get("worker_project_key") or "") != worker_project_key:
        raise ValueError("action worker_project_key mismatch")
    if action.get("worker_continuation") is not True:
        raise ValueError("action_submit requires worker_continuation=true")
    if not isinstance(action.get("payload"), dict):
        raise ValueError("action payload must be object")
    if not isinstance(action.get("expected_evidence"), list):
        raise ValueError("action expected_evidence must be array")
    try:
        round_no = int(action.get("round") or 0)
    except Exception:
        round_no = 0
    if round_no < 1:
        raise ValueError("action round must be positive")

    action_rel = f"actions/stage0/{task_id}.json"

    for attempt in range(3):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                shown = store._git("show", f"FETCH_HEAD:{backend_cl_rel}")
                bg = json.loads(shown.stdout)
                fg = json.loads(store._git("show", f"FETCH_HEAD:{foreground_cl_rel}").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_ACTION_SUBMIT_STATE_FAILED"}
            try:
                canonical_state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
            except Exception:
                canonical_state = {}
            try:
                canonical_lanes = json.loads(store._git("show", "FETCH_HEAD:state/lanes.json").stdout)
            except Exception:
                canonical_lanes = {}

            if not isinstance(bg, dict) or bg.get("task_id") != task_id or bg.get("scope") != "backend_execution":
                return {"ok": False, "error": "ACTION_SUBMIT_BACKEND_CL_INVALID"}
            owner_task_id, owner_control_epoch = _worker_pool_identity(bg, task_id)
            canonical_owner, owner_conflict, owner_source = canonical_worker_owner(
                canonical_lanes,
                lane_id=lane_id,
                worker_project_key=worker_project_key,
                owner_task_id=owner_task_id,
                owner_control_epoch=owner_control_epoch,
            )
            if owner_source != "task_pool_owner" or canonical_owner != worker_ref:
                return {
                    "ok": False,
                    "error": "ACTION_SUBMIT_WORKER_MISMATCH",
                    "canonical_owner": canonical_owner or None,
                    "owner_source": owner_source,
                    "owner_mirror_conflict": owner_conflict,
                }
            dispatch = bg.get("dispatch")
            if not isinstance(dispatch, dict):
                return {"ok": False, "error": "ACTION_SUBMIT_DISPATCH_MISSING"}

            exact = (
                str(dispatch.get("dispatch_id") or "") == dispatch_id
                and int(dispatch.get("generation") or 0) == generation
                and str(dispatch.get("fence_token") or "") == fence_token
            )
            if not exact:
                return {"ok": False, "error": "ACTION_SUBMIT_STALE_DISPATCH"}

            state = str(dispatch.get("state") or "")
            acked_by = str(dispatch.get("acked_by_worker_ref") or "")
            if state == "WAIT_RESULT" and str(dispatch.get("wait_ref") or "") == action_id:
                return {
                    "ok": True,
                    "duplicate": True,
                    "action_id": action_id,
                    "action_path": action_rel,
                    "dispatch_state": state,
                }
            if state not in {"ACKED", "RUNNING"}:
                return {"ok": False, "error": f"ACTION_SUBMIT_DISPATCH_STATE_{state or 'UNKNOWN'}"}
            if acked_by and acked_by != worker_ref:
                return {"ok": False, "error": "ACTION_SUBMIT_WORKER_MISMATCH"}

            worktree = store.runtime / f"action-submit-{task_id}-{generation}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")

                bg_path = worktree / backend_cl_rel
                fg_path = worktree / foreground_cl_rel
                action_path = worktree / action_rel
                current_bg = json.loads(bg_path.read_text(encoding="utf-8"))
                current_dispatch = current_bg.get("dispatch") or {}
                current_exact = (
                    str(current_dispatch.get("dispatch_id") or "") == dispatch_id
                    and int(current_dispatch.get("generation") or 0) == generation
                    and str(current_dispatch.get("fence_token") or "") == fence_token
                )
                if not current_exact:
                    return {"ok": False, "error": "ACTION_SUBMIT_DISPATCH_CHANGED"}

                action_path.parent.mkdir(parents=True, exist_ok=True)
                action_path.write_text(json.dumps(action, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                current_bg["dispatch"] = {
                    **current_dispatch,
                    "state": "WAIT_RESULT",
                    "wait_ref": action_id,
                }
                current_bg["overall"] = "RUNNING"
                current_bg["wait_ref"] = action_id
                current_bg["updated_at"] = utc_now()
                ex = _condition(current_bg, "executor")
                if ex is not None:
                    ex["state"] = "WAIT"
                    ex["detail"] = f"Action {action_id} submitted; waiting for deterministic executor result"
                    ex["evidence_ref"] = action_rel
                bg_path.write_text(json.dumps(current_bg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                current_fg = json.loads(fg_path.read_text(encoding="utf-8"))
                current_fg["overall"] = "RUNNING"
                current_fg["updated_at"] = utc_now()
                fg_path.write_text(json.dumps(current_fg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                _run(worktree, "add", action_rel, backend_cl_rel, foreground_cl_rel)
                _run(worktree, "commit", "-m", f"Schedule {action_id} and enter WAIT_RESULT")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt < 2:
                        continue
                    return {"ok": False, "error": "GIT_ACTION_SUBMIT_PUSH_FAILED"}

                return {
                    "ok": True,
                    "duplicate": False,
                    "task_id": task_id,
                    "action_id": action_id,
                    "action_path": action_rel,
                    "dispatch_id": dispatch_id,
                    "dispatch_generation": generation,
                    "dispatch_state": "WAIT_RESULT",
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)

    return {"ok": False, "error": "ACTION_SUBMIT_RETRY_EXHAUSTED"}


SEMANTIC_LEASE_SECONDS = 600
SEMANTIC_LEASE_RENEW_MARGIN_SECONDS = 180


def semantic_lease_expires_at(seconds: int = SEMANTIC_LEASE_SECONDS) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _handoff_identity(
    task_id: str,
    generation: int,
    source_dispatch_id: str,
    worker_ref: str,
) -> tuple[str, str, str]:
    seed = f"{task_id}|handoff|{generation}|{source_dispatch_id}|{worker_ref}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return (
        f"dispatch-{digest[:24]}",
        f"fence-{digest[24:56]}",
        f"wake-{digest[:32]}",
    )


def _fresh_handoff_dispatch(
    *,
    task_id: str,
    current: dict[str, Any],
    worker_ref: str,
    continuation_ref: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generation = int(current.get("generation") or 0) + 1
    source_dispatch_id = str(current.get("dispatch_id") or "")
    dispatch_id, fence_token, wake_id = _handoff_identity(
        task_id,
        generation,
        source_dispatch_id,
        worker_ref,
    )
    now = utc_now()
    predecessor = {
        "dispatch_id": source_dispatch_id,
        "generation": int(current.get("generation") or 0),
        "fence_token": str(current.get("fence_token") or ""),
        "state": str(current.get("state") or ""),
        "acked_at": current.get("acked_at"),
        "acked_by_worker_ref": current.get("acked_by_worker_ref"),
        "reason": "worker_handoff",
        "recovered_at": now,
    }
    dispatch = {
        "dispatch_id": dispatch_id,
        "wake_id": wake_id,
        "generation": generation,
        "fence_token": fence_token,
        "state": "READY",
        "requested_at": now,
        "delivered_at": None,
        "acked_at": None,
        "acked_by_worker_ref": None,
        "lease_expires_at": None,
        "continuation_ref": continuation_ref,
        "wait_ref": None,
        "ack_source": None,
        "predecessor_worker_ref": current.get("acked_by_worker_ref"),
        "recovered_from": predecessor,
    }
    return dispatch, predecessor


def _handoff_wake_payload(
    *,
    project_id: str,
    owner_task_id: str,
    owner_control_epoch: int,
    task_id: str,
    backend_cl_rel: str,
    lane_id: str,
    worker_project_key: str,
    dispatch: dict[str, Any],
    result_ref: str | None,
) -> dict[str, Any]:
    wake = {
        "v": 1,
        "wake_id": dispatch["wake_id"],
        "project_id": project_id,
        "state": "NEED_AGENT",
        "created_at": dispatch["requested_at"],
        "repo": "CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
        "run_id": f"{task_id}-handoff-g{dispatch['generation']}",
        "lane_id": lane_id,
        "worker_project_key": worker_project_key,
        "owner_task_id": owner_task_id,
        "owner_control_epoch": int(owner_control_epoch),
        "kind": "task_continue",
        "task_id": task_id,
        "backend_cl": backend_cl_rel,
        "dispatch_id": dispatch["dispatch_id"],
        "dispatch_generation": dispatch["generation"],
        "fence_token": dispatch["fence_token"],
    }
    if result_ref:
        wake["result_ref"] = result_ref
    return wake


def _matching_rollover_request(
    lanes: dict[str, Any],
    *,
    lane_id: str,
    worker_project_key: str,
    owner_task_id: str,
    owner_control_epoch: int,
    outgoing_worker_ref: str,
    packet_ref: str,
) -> dict[str, Any] | None:
    for lane in lanes.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        if str(lane.get("lane_id") or "") != lane_id:
            continue
        if str(lane.get("project_key") or "") != worker_project_key:
            return None
        pools = lane.get("task_pools")
        if not isinstance(pools, dict):
            return None
        pool = pools.get(_task_pool_key(owner_task_id, owner_control_epoch))
        if not isinstance(pool, dict):
            return None
        request = pool.get("worker_rollover_request")
        if not isinstance(request, dict):
            return None
        request_handoff = str(request.get("handoff_id") or request.get("outgoing_pool_id") or "")
        if request_handoff != outgoing_worker_ref:
            return None
        if str(request.get("handoff_packet_ref") or "") != packet_ref:
            return None
        if str(request.get("reason") or "") not in {"context_compacted", "semantic_stall"}:
            return None
        return request
    return None


def complete_worker_handoff(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Close a durable semantic Worker handoff and redispatch on a fresh fence."""
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    lane_id = str(req.get("lane_id") or "").strip()
    worker_project_key = str(req.get("worker_project_key") or "").strip()
    successor_worker_ref = _safe_id(req.get("successor_worker_ref"), "successor_worker_ref")
    packet_ref = _safe_rel(req.get("handoff_packet_ref"), "handoff_packet_ref")
    if not project_id:
        raise ValueError("project_id required")
    if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
        raise ValueError("lane identity required")

    for attempt in range(3):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                packet = json.loads(store._git("show", f"FETCH_HEAD:{packet_ref}").stdout)
                state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
                lanes = json.loads(store._git("show", "FETCH_HEAD:state/lanes.json").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_HANDOFF_COMPLETE_STATE_FAILED"}

            checkpoint = packet.get("work_checkpoint") if isinstance(packet, dict) else None
            if not isinstance(checkpoint, dict):
                return {"ok": False, "error": "HANDOFF_PACKET_INVALID"}
            task_id = _safe_id(packet.get("task_id"), "task_id")
            old_generation = int(packet.get("generation") or 0)
            old_fence = str(packet.get("fence_token") or "")
            old_dispatch_id = str(checkpoint.get("dispatch_id") or "")
            backend_cl_rel = _safe_rel(checkpoint.get("backend_cl_ref"), "backend_cl_ref")
            outgoing_worker_ref = _safe_id(packet.get("handoff_id"), "outgoing_worker_ref")
            if old_generation < 1 or not old_dispatch_id or not old_fence:
                return {"ok": False, "error": "HANDOFF_PACKET_IDENTITY_INVALID"}

            try:
                bg = json.loads(store._git("show", f"FETCH_HEAD:{backend_cl_rel}").stdout)
            except Exception:
                return {"ok": False, "error": "HANDOFF_BACKEND_CL_READ_FAILED"}
            if not isinstance(bg, dict) or bg.get("scope") != "backend_execution" or bg.get("task_id") != task_id:
                return {"ok": False, "error": "HANDOFF_BACKEND_CL_INVALID"}
            owner_task_id, owner_control_epoch = _worker_pool_identity(bg, task_id)
            current = bg.get("dispatch")
            if not isinstance(current, dict):
                return {"ok": False, "error": "HANDOFF_DISPATCH_MISSING"}

            current_generation = int(current.get("generation") or 0)
            recovered_from = current.get("recovered_from")
            if current_generation > old_generation:
                already = (
                    isinstance(recovered_from, dict)
                    and int(recovered_from.get("generation") or 0) == old_generation
                    and str(recovered_from.get("dispatch_id") or "") == old_dispatch_id
                    and str(recovered_from.get("reason") or "") == "worker_handoff"
                )
                wake = _handoff_wake_payload(
                    project_id=project_id,
                    owner_task_id=owner_task_id,
                    owner_control_epoch=owner_control_epoch,
                    task_id=task_id,
                    backend_cl_rel=backend_cl_rel,
                    lane_id=lane_id,
                    worker_project_key=worker_project_key,
                    dispatch=current,
                    result_ref=packet_ref,
                )
                wake["handoff_packet_ref"] = packet_ref
                return {
                    "ok": True,
                    "already_recovered": already,
                    "dispatch": current,
                    "backend_cl": backend_cl_rel,
                    "wake": wake,
                }

            exact = (
                str(current.get("dispatch_id") or "") == old_dispatch_id
                and current_generation == old_generation
                and str(current.get("fence_token") or "") == old_fence
            )
            if not exact:
                return {"ok": False, "error": "HANDOFF_STALE_DISPATCH"}
            if str(current.get("state") or "") not in {"RUNNING", "HANDOFF"}:
                return {"ok": False, "error": f"HANDOFF_DISPATCH_STATE_{str(current.get('state') or 'UNKNOWN')}"}

            lane_record = None
            for item in lanes.get("lanes") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("lane_id") or "") == lane_id and str(item.get("project_key") or "") == worker_project_key:
                    lane_record = item
                    break
            if lane_record is None:
                return {"ok": False, "error": "HANDOFF_LANE_NOT_REGISTERED"}
            pools = lane_record.get("task_pools")
            owner_pool = pools.get(_task_pool_key(owner_task_id, owner_control_epoch)) if isinstance(pools, dict) else None
            if not isinstance(owner_pool, dict):
                return {"ok": False, "error": "HANDOFF_TASK_POOL_MISSING"}

            rollover = _matching_rollover_request(
                lanes,
                lane_id=lane_id,
                worker_project_key=worker_project_key,
                owner_task_id=owner_task_id,
                owner_control_epoch=owner_control_epoch,
                outgoing_worker_ref=outgoing_worker_ref,
                packet_ref=packet_ref,
            )
            if rollover is None:
                return {"ok": False, "error": "HANDOFF_ROLLOVER_REQUEST_MISSING"}

            worktree = store.runtime / f"handoff-complete-{task_id}-{old_generation}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")

                bg_path = worktree / backend_cl_rel
                current_bg = json.loads(bg_path.read_text(encoding="utf-8"))
                current_dispatch = current_bg.get("dispatch")
                if not isinstance(current_dispatch, dict):
                    return {"ok": False, "error": "HANDOFF_DISPATCH_CHANGED"}
                if not (
                    str(current_dispatch.get("dispatch_id") or "") == old_dispatch_id
                    and int(current_dispatch.get("generation") or 0) == old_generation
                    and str(current_dispatch.get("fence_token") or "") == old_fence
                ):
                    return {"ok": False, "error": "HANDOFF_DISPATCH_CHANGED"}

                new_dispatch, old_record = _fresh_handoff_dispatch(
                    task_id=task_id,
                    current=current_dispatch,
                    worker_ref=successor_worker_ref,
                    continuation_ref=packet_ref,
                )
                current_bg["dispatch"] = new_dispatch
                current_bg["overall"] = "READY"
                current_bg["updated_at"] = new_dispatch["requested_at"]
                current_bg["handoff_packet_ref"] = packet_ref
                current_bg["wait_ref"] = None
                current_bg["error"] = None
                bg_path.write_text(json.dumps(current_bg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                wake = _handoff_wake_payload(
                    project_id=project_id,
                    owner_task_id=owner_task_id,
                    owner_control_epoch=owner_control_epoch,
                    task_id=task_id,
                    backend_cl_rel=backend_cl_rel,
                    lane_id=lane_id,
                    worker_project_key=worker_project_key,
                    dispatch=new_dispatch,
                    result_ref=packet_ref,
                )
                wake["handoff_packet_ref"] = packet_ref
                wake_rel = f"requests/worker-wake/{new_dispatch['wake_id']}.json"
                wake_path = worktree / wake_rel
                wake_path.parent.mkdir(parents=True, exist_ok=True)
                wake_path.write_text(json.dumps(wake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                _run(worktree, "add", backend_cl_rel, wake_rel)
                _run(worktree, "commit", "-m", f"Resume {task_id} after Worker handoff")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt < 2:
                        continue
                    return {"ok": False, "error": "GIT_HANDOFF_COMPLETE_PUSH_FAILED"}
                return {
                    "ok": True,
                    "already_recovered": False,
                    "task_id": task_id,
                    "backend_cl": backend_cl_rel,
                    "dispatch": new_dispatch,
                    "recovered_from": old_record,
                    "wake_ref": wake_rel,
                    "wake": wake,
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)

    return {"ok": False, "error": "HANDOFF_COMPLETE_RETRY_EXHAUSTED"}


def _single_lane_branch_result_ref(
    store: Any,
    *,
    task: dict[str, Any],
    branch_head: str,
) -> str | None:
    """Resolve one task-owned single-lane branch_result from the branch delta.

    Legacy single_lane_semantic_branch tasks do not carry output_root/result_ref
    as a machine field. Bound discovery is therefore limited to files changed
    from the task's pinned source_commit to the task work-branch head, then
    narrowed to branch_result.json whose task_id exactly matches this task.
    """
    task_id = str(task.get("task_id") or "").strip()
    source_commit = str(task.get("source_commit") or "").strip()
    if not task_id or not source_commit:
        return None
    try:
        store._git("cat-file", "-e", f"{source_commit}^{{commit}}")
        changed = store._git("diff", "--name-only", source_commit, branch_head, "--")
    except Exception:
        return None

    matches: list[str] = []
    for raw in changed.stdout.splitlines():
        rel = str(raw or "").replace("\\", "/").strip()
        if not rel.endswith("/branch_result.json"):
            continue
        try:
            rel = _safe_rel(rel, "branch_result_ref")
            value = json.loads(store._git("show", f"{branch_head}:{rel}").stdout)
        except Exception:
            continue
        if isinstance(value, dict) and str(value.get("task_id") or "").strip() == task_id:
            matches.append(rel)

    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    return None


def _project_single_lane_parent_condition(
    *,
    task: dict[str, Any],
    foreground: dict[str, Any],
    backend_cl_rel: str,
    result: dict[str, Any],
    evidence_ref: str,
) -> None:
    """Project one exact parent condition already bound to this backend CL."""
    if str(task.get("kind") or "") != "single_lane_semantic_branch":
        return
    matches = [
        item
        for item in foreground.get("conditions") or []
        if isinstance(item, dict)
        and str(item.get("evidence_ref") or "").replace("\\", "/").strip() == backend_cl_rel
    ]
    if len(matches) > 1:
        raise ParallelBranchFinalizeError("multiple parent conditions reference single-lane backend")
    if not matches:
        return
    status = str(result.get("status") or "")
    state = {"PASS": "GREEN", "BLOCKED": "BLOCKED", "ERROR": "ERROR"}.get(status)
    if not state:
        raise ParallelBranchFinalizeError("single-lane branch_result status is not terminal")
    item = matches[0]
    item["state"] = state
    item["detail"] = str(result.get("summary") or f"single-lane branch {status}")
    item["evidence_ref"] = evidence_ref


def _try_finalize_parallel_branch(
    store: Any,
    *,
    task_id: str,
    backend_cl_rel: str,
    canonical_sha: str,
    attempt: int,
) -> dict[str, Any] | None:
    task_rel = f"tasks/{task_id}.json"
    try:
        task = json.loads(store._git("show", f"{canonical_sha}:{task_rel}").stdout)
    except Exception:
        return None
    if not isinstance(task, dict):
        return None
    task_kind = str(task.get("kind") or "")
    if task_kind not in {"parallel_semantic_branch", "single_lane_semantic_branch"}:
        return None

    work_branch = str(task.get("work_branch") or "").strip()
    foreground_cl_rel = str(task.get("foreground_cl") or "").replace("\\", "/").strip()
    if not work_branch or not foreground_cl_rel:
        return None

    try:
        bg = json.loads(store._git("show", f"{canonical_sha}:{backend_cl_rel}").stdout)
        fg = json.loads(store._git("show", f"{canonical_sha}:{foreground_cl_rel}").stdout)
        store._git("fetch", "--quiet", "--no-tags", store.git_remote, work_branch)
        branch_head = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
        if task_kind == "parallel_semantic_branch":
            result_rel = branch_result_ref(task)
        else:
            result_rel = _single_lane_branch_result_ref(
                store,
                task=task,
                branch_head=branch_head,
            )
            if not result_rel:
                return None
        shown = store._git("show", f"{branch_head}:{result_rel}")
        result = json.loads(shown.stdout)
    except Exception:
        return None

    if not isinstance(bg, dict) or not isinstance(fg, dict) or not isinstance(result, dict):
        return None

    def artifact_exists(rel: str) -> bool:
        safe = _safe_rel(rel, "artifact_ref")
        try:
            store._git("cat-file", "-e", f"{branch_head}:{safe}")
            return True
        except Exception:
            return False

    try:
        immutable_kwargs: dict[str, Any] = {}
        identity_builder = getattr(parallel_branch_finalize, "build_evidence_identity", None)
        if callable(identity_builder):
            evidence_identity = identity_builder(
                store.repo_root,
                task=task,
                branch_head=branch_head,
                result_ref=result_rel,
                result=result,
            )
            immutable_kwargs = {
                "branch_head": branch_head,
                "evidence_identity": evidence_identity,
            }
        applied = apply_branch_result(
            task=task,
            backend=bg,
            foreground=fg,
            result=result,
            result_ref=result_rel,
            artifact_exists=artifact_exists,
            **immutable_kwargs,
        )
        _project_single_lane_parent_condition(
            task=task,
            foreground=fg,
            backend_cl_rel=backend_cl_rel,
            result=result,
            evidence_ref=str(applied.get("evidence_ref") or result_rel),
        )
    except ParallelBranchFinalizeError as exc:
        message = str(exc)
        if message.startswith("stale branch_result"):
            return {"finalized": False, "reason": "stale_branch_result", "detail": message}
        return {"finalized": False, "reason": "branch_result_invalid", "detail": message}

    if applied.get("already_finalized"):
        return {
            "finalized": True,
            "already_finalized": True,
            "outcome": applied.get("outcome"),
            "evidence_ref": applied.get("evidence_ref"),
        }

    worktree = store.runtime / f"branch-finalize-{task_id}-{attempt}"
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    try:
        store._git("worktree", "add", "--force", "--detach", str(worktree), canonical_sha)
        _run(worktree, "config", "user.name", "gah-local-bridge")
        _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
        bg_path = worktree / backend_cl_rel
        fg_path = worktree / foreground_cl_rel
        bg_path.write_text(json.dumps(bg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fg_path.write_text(json.dumps(fg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _run(worktree, "add", backend_cl_rel, foreground_cl_rel)
        label = "parallel" if task_kind == "parallel_semantic_branch" else "single-lane"
        _run(worktree, "commit", "-m", f"Finalize {label} semantic branch {task_id}")
        commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
        try:
            _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
        except subprocess.SubprocessError:
            return {"finalized": False, "reason": "push_race", "retry": True}
        return {
            "finalized": True,
            "already_finalized": False,
            "outcome": applied.get("outcome"),
            "evidence_ref": applied.get("evidence_ref"),
            "commit_sha": commit_sha,
        }
    finally:
        try:
            store._git("worktree", "remove", "--force", str(worktree))
        except Exception:
            shutil.rmtree(worktree, ignore_errors=True)


def _try_finalize_planner_worker_child(
    store: Any,
    *,
    task_id: str,
    backend_cl_rel: str,
    canonical_sha: str,
    attempt: int,
) -> dict[str, Any] | None:
    """Finalize a Planner-owned generic child from its exact declared result artifact."""
    task_rel = f"tasks/{task_id}.json"
    try:
        task = json.loads(store._git("show", f"{canonical_sha}:{task_rel}").stdout)
    except Exception:
        return None
    if not isinstance(task, dict) or str(task.get("kind") or "") != "planner_worker_child":
        return None

    declared_backend = str(task.get("backend_cl") or "").replace("\\", "/").strip()
    if declared_backend != backend_cl_rel:
        return {"finalized": False, "reason": "planner_child_backend_contract_mismatch"}
    try:
        result_rel = _safe_rel(task.get("expected_result_ref"), "expected_result_ref")
    except ValueError:
        return {"finalized": False, "reason": "planner_child_result_contract_missing"}
    contract = task.get("result_contract")
    if not isinstance(contract, dict) or not contract:
        return {"finalized": False, "reason": "planner_child_result_contract_missing"}
    try:
        child_reply_rel = _safe_rel(task.get("child_reply_ref"), "child_reply_ref")
        worker_reply_entry_rel = _safe_rel(task.get("worker_reply_entry_ref"), "worker_reply_entry_ref")
    except ValueError:
        return {"finalized": False, "reason": "planner_child_reply_contract_missing"}

    try:
        bg = json.loads(store._git("show", f"{canonical_sha}:{backend_cl_rel}").stdout)
        result = json.loads(store._git("show", f"{canonical_sha}:{result_rel}").stdout)
        worker_reply_entry = store._git("show", f"{canonical_sha}:{worker_reply_entry_rel}").stdout
    except Exception:
        return None
    if not isinstance(bg, dict) or not isinstance(result, dict):
        return {"finalized": False, "reason": "planner_child_result_invalid"}

    mismatches = [key for key, expected in contract.items() if result.get(key) != expected]
    if mismatches:
        return {
            "finalized": False,
            "reason": "planner_child_result_contract_mismatch",
            "detail": ",".join(sorted(mismatches)),
        }

    current = bg.get("dispatch") if isinstance(bg.get("dispatch"), dict) else {}

    status = str(result.get("status") or "")
    if status in {"PASS", "CONTINUE"} and not worker_reply_has_write(worker_reply_entry, str(current["wake_id"])):
        return {"finalized": False, "reason": "worker_reply_missing"}
    terminal_state = {"PASS": "DONE", "CONTINUE": "DONE", "BLOCKED": "BLOCKED", "ERROR": "ERROR"}.get(status)
    condition_state = {"PASS": "GREEN", "CONTINUE": "GREEN", "BLOCKED": "BLOCKED", "ERROR": "ERROR"}.get(status)
    if terminal_state is None or condition_state is None:
        return {"finalized": False, "reason": "planner_child_result_status_invalid"}

    worktree = store.runtime / f"planner-child-finalize-{task_id}-{attempt}"
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    try:
        store._git("worktree", "add", "--force", "--detach", str(worktree), canonical_sha)
        _run(worktree, "config", "user.name", "gah-local-bridge")
        _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
        bg_path = worktree / backend_cl_rel
        current_bg = json.loads(bg_path.read_text(encoding="utf-8"))
        dispatch = current_bg.get("dispatch") if isinstance(current_bg.get("dispatch"), dict) else {}
        dispatch["state"] = terminal_state
        current_bg["dispatch"] = dispatch
        current_bg["overall"] = terminal_state
        current_bg["result_ref"] = result_rel
        current_bg["updated_at"] = utc_now()
        current_bg["error"] = None if status in {"PASS", "CONTINUE"} else (
            result.get("blocker") if isinstance(result.get("blocker"), dict)
            else {"kind": f"PLANNER_CHILD_{status}", "detail": str(result.get("summary") or status)}
        )
        for cid in ("semantic_work", "branch_output"):
            item = _condition(current_bg, cid)
            if item is not None:
                item["state"] = condition_state
                item["detail"] = str(result.get("summary") or f"Planner child {status}")
                item["evidence_ref"] = result_rel
        bg_path.write_text(json.dumps(current_bg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        child_reply_path = worktree / child_reply_rel
        child_reply_path.parent.mkdir(parents=True, exist_ok=True)
        with child_reply_path.open("a", encoding="utf-8") as handle:
            handle.write(worker_reply_entry)
            if worker_reply_entry and not worker_reply_entry.endswith("\n"):
                handle.write("\n")
        _run(worktree, "add", backend_cl_rel, child_reply_rel)
        _run(worktree, "commit", "-m", f"Finalize Planner Worker child {task_id}")
        commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
        try:
            _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
        except subprocess.SubprocessError:
            return {"finalized": False, "reason": "push_race", "retry": True}
        return {
            "finalized": True,
            "already_finalized": False,
            "outcome": status,
            "evidence_ref": result_rel,
            "child_reply_ref": child_reply_rel,
            "commit_sha": commit_sha,
        }
    finally:
        try:
            store._git("worktree", "remove", "--force", str(worktree))
        except Exception:
            pass
        shutil.rmtree(worktree, ignore_errors=True)


def _try_finalize_nonparallel_semantic(
    store: Any,
    *,
    task_id: str,
    backend_cl_rel: str,
    canonical_sha: str,
    attempt: int,
) -> dict[str, Any] | None:
    """Accept an exact task-declared semantic artifact before any liveness redrive.

    The semantic finalizer performs task/dispatch/generation/fence and prerequisite
    validation. A stale/malformed artifact is non-mutating and does not suppress
    a legitimate fresh-generation recovery.
    """
    task_rel = f"tasks/{task_id}.json"
    try:
        task = json.loads(store._git("show", f"{canonical_sha}:{task_rel}").stdout)
    except Exception:
        return None
    if not isinstance(task, dict) or str(task.get("kind") or "") == "parallel_semantic_branch":
        return None

    contract = task.get("execution_contract") if isinstance(task.get("execution_contract"), dict) else {}
    semantic = task.get("semantic_reduce") if isinstance(task.get("semantic_reduce"), dict) else {}
    analysis_rel = str(contract.get("final_analysis") or semantic.get("analysis_path") or "").replace("\\", "/").strip()
    foreground_cl_rel = str(contract.get("foreground_cl") or task.get("foreground_cl") or "").replace("\\", "/").strip()
    declared_backend = str(contract.get("backend_cl") or task.get("backend_cl") or "").replace("\\", "/").strip()
    if not analysis_rel or not foreground_cl_rel:
        return None
    if declared_backend and declared_backend != backend_cl_rel:
        return {"finalized": False, "reason": "semantic_backend_contract_mismatch"}

    try:
        store._git("cat-file", "-e", f"{canonical_sha}:{analysis_rel}")
    except Exception:
        return None

    worktree = store.runtime / f"semantic-finalize-{task_id}-{attempt}"
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    try:
        store._git("worktree", "add", "--force", "--detach", str(worktree), canonical_sha)
        analysis_path = worktree / analysis_rel
        try:
            applied = finalize_semantic_result(analysis_path, root=worktree)
        except (SystemExit, OSError, json.JSONDecodeError, ValueError) as exc:
            return {
                "finalized": False,
                "reason": "semantic_result_invalid",
                "detail": str(exc),
            }

        if str(applied.get("outcome") or "") == "REJECTED":
            return {
                "finalized": False,
                "reason": "semantic_result_rejected",
                "detail": "; ".join(str(x) for x in applied.get("errors") or []),
            }
        if not applied.get("projected"):
            return {
                "finalized": True,
                "already_finalized": bool(applied.get("idempotent")),
                "outcome": applied.get("outcome"),
                "evidence_ref": analysis_rel,
            }

        _run(worktree, "config", "user.name", "gah-local-bridge")
        _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
        state_rel = "state/chatgpt.json"
        _run(worktree, "add", backend_cl_rel, foreground_cl_rel, state_rel)
        _run(worktree, "commit", "-m", f"Finalize semantic result {task_id}")
        commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
        try:
            _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
        except subprocess.SubprocessError:
            # Remote outcome may be unknown; caller re-fetches canonical state
            # and recomputes before retrying.
            return {"finalized": False, "reason": "push_race", "retry": True}
        return {
            "finalized": True,
            "already_finalized": False,
            "outcome": applied.get("outcome"),
            "evidence_ref": analysis_rel,
            "commit_sha": commit_sha,
        }
    finally:
        try:
            store._git("worktree", "remove", "--force", str(worktree))
        except Exception:
            shutil.rmtree(worktree, ignore_errors=True)



FOREGROUND_TERMINAL_LEASE_SECONDS = 120
FOREGROUND_TERMINAL_STATES = {"PENDING", "CLAIMED", "DELIVERED", "CONSUMED"}
FOREGROUND_TERMINAL_STATUSES = {"PASS", "ERROR", "BLOCKED"}


def _terminal_now(now_fn: Any = None) -> datetime:
    value = now_fn() if callable(now_fn) else datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError("terminal clock must return datetime")
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _terminal_event_view(
    store: Any,
    *,
    canonical_sha: str,
    foreground_cl_rel: str,
    foreground: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if foreground.get("scope") != "foreground_supervision":
        return None, "FOREGROUND_TERMINAL_CL_SCOPE"
    event = foreground.get("terminal_event")
    if not isinstance(event, dict):
        return None, None
    if int(event.get("v") or 0) != 1 or str(event.get("kind") or "") != "executor_terminal":
        return None, "FOREGROUND_TERMINAL_EVENT_INVALID"

    event_id = str(event.get("event_id") or "").strip()
    task_id = str(event.get("task_id") or "").strip()
    action_id = str(event.get("action_id") or "").strip()
    result_id = str(event.get("result_id") or "").strip()
    action_ref = _safe_rel(event.get("action_ref"), "action_ref")
    result_ref = _safe_rel(event.get("result_ref"), "result_ref")
    if not event_id.startswith("terminal-") or not task_id or not action_id or not result_id:
        return None, "FOREGROUND_TERMINAL_IDENTITY_INVALID"
    if str(foreground.get("task_id") or "") != task_id:
        return None, "FOREGROUND_TERMINAL_TASK_MISMATCH"
    if str(event.get("foreground_cl_ref") or "") != foreground_cl_rel:
        return None, "FOREGROUND_TERMINAL_CL_REF_MISMATCH"
    try:
        action = json.loads(store._git("show", f"{canonical_sha}:{action_ref}").stdout)
        result = json.loads(store._git("show", f"{canonical_sha}:{result_ref}").stdout)
    except Exception:
        return None, "FOREGROUND_TERMINAL_REFERENT_MISSING"
    if (
        str(action.get("task_id") or "") != task_id
        or str(action.get("action_id") or "") != action_id
        or str(result.get("task_id") or "") != task_id
        or str(result.get("action_id") or "") != action_id
        or str(result.get("result_id") or "") != result_id
    ):
        return None, "FOREGROUND_TERMINAL_REFERENT_IDENTITY"
    if bool(action.get("worker_continuation")):
        return None, "FOREGROUND_TERMINAL_CONTINUATION_FORBIDDEN"
    result_status = str(result.get("status") or "").upper()
    if result_status not in FOREGROUND_TERMINAL_STATUSES:
        return None, "FOREGROUND_TERMINAL_RESULT_STATUS_INVALID"
    expected = result_status
    if str(event.get("terminal_status") or "") != expected:
        return None, "FOREGROUND_TERMINAL_STATUS_MISMATCH"

    delivery = event.get("delivery")
    watchdog = event.get("watchdog")
    if not isinstance(delivery, dict) or not isinstance(watchdog, dict):
        return None, "FOREGROUND_TERMINAL_DELIVERY_INVALID"
    if str(delivery.get("state") or "") not in FOREGROUND_TERMINAL_STATES:
        return None, "FOREGROUND_TERMINAL_DELIVERY_STATE"

    dispatch_event = event.get("dispatch")
    if dispatch_event is not None:
        if not isinstance(dispatch_event, dict):
            return None, "FOREGROUND_TERMINAL_DISPATCH_INVALID"
        backend_rel = _safe_rel(event.get("backend_cl_ref"), "backend_cl_ref")
        try:
            backend = json.loads(store._git("show", f"{canonical_sha}:{backend_rel}").stdout)
        except Exception:
            return None, "FOREGROUND_TERMINAL_BACKEND_MISSING"
        current = backend.get("dispatch")
        if not isinstance(current, dict):
            return None, "FOREGROUND_TERMINAL_DISPATCH_MISSING"
        exact = (
            str(current.get("dispatch_id") or "") == str(dispatch_event.get("dispatch_id") or "")
            and int(current.get("generation") or 0) == int(dispatch_event.get("generation") or 0)
            and str(current.get("fence_token") or "") == str(dispatch_event.get("fence_token") or "")
        )
        if not exact:
            return None, "FOREGROUND_TERMINAL_STALE_FENCE"
    return event, None


def _foreground_terminal_paths(store: Any, canonical_sha: str) -> list[str]:
    try:
        shown = store._git("ls-tree", "-r", "--name-only", canonical_sha, "cl")
    except Exception:
        return []
    return sorted(
        line.strip()
        for line in shown.stdout.splitlines()
        if line.strip().startswith("cl/") and line.strip().endswith(".foreground.json")
    )


def _terminal_public_view(foreground_cl_rel: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "foreground_cl": foreground_cl_rel,
        "event_id": event.get("event_id"),
        "task_id": event.get("task_id"),
        "control_epoch": event.get("control_epoch"),
        "action_id": event.get("action_id"),
        "action_ref": event.get("action_ref"),
        "result_id": event.get("result_id"),
        "result_ref": event.get("result_ref"),
        "terminal_status": event.get("terminal_status"),
        "published_at": event.get("published_at"),
        "delivery": dict(event.get("delivery") or {}),
        "watchdog": dict(event.get("watchdog") or {}),
        "dispatch": dict(event.get("dispatch") or {}) if isinstance(event.get("dispatch"), dict) else None,
    }


def _mutate_foreground_terminal(
    store: Any,
    *,
    foreground_cl_rel: str,
    event_id: str,
    mutate: Any,
    commit_message: str,
) -> dict[str, Any]:
    for attempt in range(3):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                canonical_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
                foreground = json.loads(store._git("show", f"{canonical_sha}:{foreground_cl_rel}").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_FOREGROUND_TERMINAL_STATE_FAILED"}
            event, error = _terminal_event_view(
                store,
                canonical_sha=canonical_sha,
                foreground_cl_rel=foreground_cl_rel,
                foreground=foreground,
            )
            if error:
                return {"ok": False, "error": error}
            if not isinstance(event, dict) or str(event.get("event_id") or "") != event_id:
                return {"ok": False, "error": "FOREGROUND_TERMINAL_STALE_EVENT"}

            outcome = mutate(event, canonical_sha)
            if isinstance(outcome, dict) and outcome.get("noop"):
                return {
                    "ok": True,
                    "duplicate": True,
                    "event": _terminal_public_view(foreground_cl_rel, event),
                    **{k: v for k, v in outcome.items() if k != "noop"},
                }
            if isinstance(outcome, dict) and outcome.get("error"):
                return {"ok": False, **outcome}

            worktree = store.runtime / f"fg-terminal-{event_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), canonical_sha)
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                fg_path = worktree / foreground_cl_rel
                current_fg = json.loads(fg_path.read_text(encoding="utf-8"))
                current_event = current_fg.get("terminal_event")
                if not isinstance(current_event, dict) or str(current_event.get("event_id") or "") != event_id:
                    return {"ok": False, "error": "FOREGROUND_TERMINAL_CHANGED"}
                outcome2 = mutate(current_event, canonical_sha)
                if isinstance(outcome2, dict) and outcome2.get("error"):
                    return {"ok": False, **outcome2}
                current_fg["terminal_event"] = current_event
                current_fg["updated_at"] = utc_now()
                fg_path.write_text(json.dumps(current_fg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                _run(worktree, "add", foreground_cl_rel)
                _run(worktree, "commit", "-m", commit_message)
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt < 2:
                        continue
                    return {"ok": False, "error": "GIT_FOREGROUND_TERMINAL_PUSH_FAILED"}
                return {
                    "ok": True,
                    "duplicate": False,
                    "event": _terminal_public_view(foreground_cl_rel, current_event),
                    "commit_sha": commit_sha,
                    **(outcome2 if isinstance(outcome2, dict) else {}),
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)
    return {"ok": False, "error": "FOREGROUND_TERMINAL_RETRY_EXHAUSTED"}


def reconcile_foreground_terminal(store: Any, req: dict[str, Any], *, now_fn: Any = None) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")
    op = str(req.get("terminal_op") or "status").strip()
    now_value = _terminal_now(now_fn)
    now_text = now_value.isoformat(timespec="seconds")

    if op == "status":
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                canonical_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            except Exception:
                return {"ok": False, "error": "GIT_FOREGROUND_TERMINAL_FETCH_FAILED"}
            pending: list[tuple[str, dict[str, Any]]] = []
            for rel in _foreground_terminal_paths(store, canonical_sha):
                try:
                    fg = json.loads(store._git("show", f"{canonical_sha}:{rel}").stdout)
                except Exception:
                    continue
                event, error = _terminal_event_view(
                    store,
                    canonical_sha=canonical_sha,
                    foreground_cl_rel=rel,
                    foreground=fg,
                )
                if error or not isinstance(event, dict):
                    continue
                state = str((event.get("delivery") or {}).get("state") or "")
                if state == "CONSUMED":
                    continue
                if state == "PENDING":
                    pending.append((rel, event))
                    continue

        if pending:
            rel, selected = pending[0]
            event_id = str(selected.get("event_id") or "")
            def claim(event: dict[str, Any], canonical_sha: str) -> dict[str, Any]:
                delivery = event["delivery"]
                state = str(delivery.get("state") or "")
                if state != "PENDING":
                    return {"error": "FOREGROUND_TERMINAL_ALREADY_CLAIMED"}
                claim_id = "claim-" + hashlib.sha256(f"{event_id}|{canonical_sha}".encode("utf-8")).hexdigest()[:24]
                delivery["state"] = "CLAIMED"
                delivery["claim_id"] = claim_id
                delivery["claimed_at"] = now_text
                return {"claim_id": claim_id}
            return _mutate_foreground_terminal(
                store,
                foreground_cl_rel=rel,
                event_id=event_id,
                mutate=claim,
                commit_message=f"Claim foreground terminal {event_id}",
            )

        return {"ok": True, "event": None, "lease_seconds": FOREGROUND_TERMINAL_LEASE_SECONDS}

    foreground_cl_rel = _safe_rel(req.get("foreground_cl"), "foreground_cl")
    event_id = _safe_id(req.get("event_id"), "event_id")
    claim_id = str(req.get("claim_id") or "").strip()

    if op in {"delivered", "consumed"}:
        if not claim_id.startswith("claim-"):
            raise ValueError("claim_id required")
        def update_delivery(event: dict[str, Any], _canonical_sha: str) -> dict[str, Any]:
            delivery = event["delivery"]
            if str(delivery.get("claim_id") or "") != claim_id:
                return {"error": "FOREGROUND_TERMINAL_CLAIM_MISMATCH"}
            state = str(delivery.get("state") or "")
            if op == "delivered":
                if state in {"DELIVERED", "CONSUMED"}:
                    return {"noop": True, "claim_id": claim_id}
                if state != "CLAIMED":
                    return {"error": "FOREGROUND_TERMINAL_DELIVERY_STATE"}
                delivery["state"] = "DELIVERED"
                delivery["delivered_at"] = now_text
                return {"claim_id": claim_id}
            if state == "CONSUMED":
                return {"noop": True, "claim_id": claim_id}
            if state != "DELIVERED":
                return {"error": "FOREGROUND_TERMINAL_CONSUME_STATE"}
            delivery["state"] = "CONSUMED"
            delivery["consumed_at"] = now_text
            return {"claim_id": claim_id}
        return _mutate_foreground_terminal(
            store,
            foreground_cl_rel=foreground_cl_rel,
            event_id=event_id,
            mutate=update_delivery,
            commit_message=f"{op.capitalize()} foreground terminal {event_id}",
        )

    raise ValueError("unsupported foreground terminal operation")




def reconcile_dispatch_liveness(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Observe/renew one exact dispatch; Worker watchdog escalation is handled separately."""
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    task_id = _safe_id(req.get("task_id"), "task_id")
    backend_cl_rel = _safe_rel(req.get("backend_cl"), "backend_cl")
    dispatch_id = _safe_id(req.get("dispatch_id"), "dispatch_id")
    generation = int(req.get("dispatch_generation") or 0)
    fence_token = str(req.get("fence_token") or "")
    worker_ref = _safe_id(req.get("worker_ref"), "worker_ref")
    lane_id = str(req.get("lane_id") or "").strip()
    worker_project_key = str(req.get("worker_project_key") or "").strip()
    response_running = bool(req.get("response_running"))
    explicit_response_end = bool(req.get("response_ended"))
    if not project_id or generation < 1 or not 8 <= len(fence_token) <= 256:
        raise ValueError("invalid dispatch liveness request")
    if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
        raise ValueError("lane identity required")

    for attempt in range(3):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                canonical_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
                bg = json.loads(store._git("show", f"{canonical_sha}:{backend_cl_rel}").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_LIVENESS_STATE_FAILED"}
            try:
                canonical_state = json.loads(store._git("show", f"{canonical_sha}:state/chatgpt.json").stdout)
            except Exception:
                canonical_state = {}
            try:
                canonical_lanes = json.loads(store._git("show", f"{canonical_sha}:state/lanes.json").stdout)
            except Exception:
                canonical_lanes = {}
            if not isinstance(bg, dict) or bg.get("scope") != "backend_execution" or bg.get("task_id") != task_id:
                return {"ok": False, "error": "LIVENESS_BACKEND_CL_INVALID"}
            owner_task_id, owner_control_epoch = _worker_pool_identity(bg, task_id)
            canonical_owner, owner_conflict, owner_source = canonical_worker_owner(
                canonical_lanes,
                lane_id=lane_id,
                worker_project_key=worker_project_key,
                owner_task_id=owner_task_id,
                owner_control_epoch=owner_control_epoch,
            )
            if owner_source != "task_pool_owner" or canonical_owner != worker_ref:
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "reason": "canonical_worker_owner_mismatch",
                    "canonical_owner": canonical_owner or None,
                    "owner_source": owner_source,
                    "owner_mirror_conflict": owner_conflict,
                }
            current = bg.get("dispatch")
            if not isinstance(current, dict):
                return {"ok": False, "error": "LIVENESS_DISPATCH_MISSING"}
            exact = (
                str(current.get("dispatch_id") or "") == dispatch_id
                and int(current.get("generation") or 0) == generation
                and str(current.get("fence_token") or "") == fence_token
            )
            if not exact:
                return {
                    "ok": True,
                    "matched": False,
                    "recovered": False,
                    "reason": "stale_dispatch",
                    "state": str(current.get("state") or "") or None,
                    "current_dispatch_id": current.get("dispatch_id"),
                    "current_generation": current.get("generation"),
                }

            state = str(current.get("state") or "")
            if state != "RUNNING":
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "reason": "dispatch_not_running",
                    "state": state,
                }
            owner = str(current.get("acked_by_worker_ref") or "")
            if owner and owner != worker_ref:
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "reason": "worker_owner_mismatch",
                    "state": state,
                    "acked_by_worker_ref": owner,
                }
            if response_running:
                now_dt = datetime.now(timezone.utc)
                lease = _parse_time(current.get("lease_expires_at"))
                if lease is None:
                    acked = _parse_time(current.get("acked_at"))
                    lease = (
                        acked + timedelta(seconds=SEMANTIC_LEASE_SECONDS)
                        if acked is not None
                        else now_dt
                    )
                renew_before = now_dt + timedelta(seconds=SEMANTIC_LEASE_RENEW_MARGIN_SECONDS)
                if lease <= renew_before:
                    worktree = store.runtime / f"liveness-renew-{task_id}-{generation}-{attempt}"
                    if worktree.exists():
                        shutil.rmtree(worktree, ignore_errors=True)
                    try:
                        store._git("worktree", "add", "--force", "--detach", str(worktree), canonical_sha)
                        _run(worktree, "config", "user.name", "gah-local-bridge")
                        _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                        bg_path = worktree / backend_cl_rel
                        current_bg = json.loads(bg_path.read_text(encoding="utf-8"))
                        current_dispatch = current_bg.get("dispatch")
                        if not isinstance(current_dispatch, dict) or not (
                            str(current_dispatch.get("dispatch_id") or "") == dispatch_id
                            and int(current_dispatch.get("generation") or 0) == generation
                            and str(current_dispatch.get("fence_token") or "") == fence_token
                            and str(current_dispatch.get("state") or "") == "RUNNING"
                            and str(current_dispatch.get("acked_by_worker_ref") or "") == worker_ref
                        ):
                            return {"ok": False, "error": "LIVENESS_RENEW_DISPATCH_CHANGED"}
                        renewed_at = utc_now()
                        renewed_until = semantic_lease_expires_at()
                        current_dispatch["lease_expires_at"] = renewed_until
                        current_bg["dispatch"] = current_dispatch
                        current_bg["updated_at"] = renewed_at
                        bg_path.write_text(
                            json.dumps(current_bg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        _run(worktree, "add", backend_cl_rel)
                        _run(
                            worktree,
                            "commit",
                            "-m",
                            f"Renew active semantic lease for {task_id} [skip ci]",
                        )
                        commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                        try:
                            _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                        except subprocess.SubprocessError:
                            if attempt < 2:
                                continue
                            return {"ok": False, "error": "GIT_LIVENESS_RENEW_PUSH_FAILED"}
                        return {
                            "ok": True,
                            "matched": True,
                            "recovered": False,
                            "reason": "response_still_running_lease_renewed",
                            "state": state,
                            "lease_expires_at": renewed_until,
                            "commit_sha": commit_sha,
                        }
                    finally:
                        try:
                            store._git("worktree", "remove", "--force", str(worktree))
                        except Exception:
                            shutil.rmtree(worktree, ignore_errors=True)
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "reason": "response_still_running",
                    "state": state,
                    "lease_expires_at": current.get("lease_expires_at"),
                }

            lease = _parse_time(current.get("lease_expires_at"))
            now_dt = datetime.now(timezone.utc)
            if lease is None:
                acked = _parse_time(current.get("acked_at"))
                lease = acked + timedelta(seconds=SEMANTIC_LEASE_SECONDS) if acked is not None else now_dt
            lease_expired = lease <= now_dt
            if not explicit_response_end and not lease_expired:
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "reason": "semantic_lease_active",
                    "state": state,
                    "lease_expires_at": current.get("lease_expires_at"),
                }
            recovery_reason = "semantic_response_ended" if explicit_response_end else "semantic_lease_expired"

            terminal = _try_finalize_planner_worker_child(
                store,
                task_id=task_id,
                backend_cl_rel=backend_cl_rel,
                canonical_sha=canonical_sha,
                attempt=attempt,
            )
            terminal_kind = "planner_worker_child"
            if terminal is None:
                terminal = _try_finalize_parallel_branch(
                    store,
                    task_id=task_id,
                    backend_cl_rel=backend_cl_rel,
                    canonical_sha=canonical_sha,
                    attempt=attempt,
                )
                terminal_kind = "parallel_branch"
            if terminal is None:
                terminal = _try_finalize_nonparallel_semantic(
                    store,
                    task_id=task_id,
                    backend_cl_rel=backend_cl_rel,
                    canonical_sha=canonical_sha,
                    attempt=attempt,
                )
                terminal_kind = "semantic_result"
            if terminal and terminal.get("retry"):
                continue
            if terminal and terminal.get("finalized"):
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "reason": f"{terminal_kind}_terminal_finalized",
                    "terminal": terminal,
                }

            task_rel = f"tasks/{task_id}.json"
            try:
                task_value = json.loads(store._git("show", f"{canonical_sha}:{task_rel}").stdout)
            except Exception:
                task_value = None
            if (
                isinstance(task_value, dict)
                and str(task_value.get("kind") or "") == "planner_worker_child"
            ):
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": False,
                    "escalated": False,
                    "reason": "managed_worker_liveness_wait_worker_watchdog",
                    "liveness_reason": recovery_reason,
                    "task_id": task_id,
                    "backend_cl": backend_cl_rel,
                    "dispatch_id": dispatch_id,
                    "dispatch_generation": generation,
                    "fence_token": fence_token,
                }

            return {
                "ok": True,
                "matched": True,
                "recovered": False,
                "escalated": False,
                "reason": "liveness_fault_no_automatic_redrive",
                "liveness_reason": recovery_reason,
                "task_id": task_id,
                "backend_cl": backend_cl_rel,
                "dispatch_id": dispatch_id,
                "dispatch_generation": generation,
                "fence_token": fence_token,
            }
