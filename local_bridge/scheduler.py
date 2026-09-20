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
except ImportError:
    from topology import _run, utc_now


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


def canonical_worker_owner(
    lanes: dict[str, Any],
    state: dict[str, Any],
    *,
    lane_id: str,
    worker_project_key: str,
) -> tuple[str, bool, str]:
    """Resolve canonical Worker ownership without claimant-dependent fallback.

    A nonempty lane-local owner is authoritative. The top-level
    state/chatgpt.json mirror is used only for the explicitly supported lane-00
    legacy migration case where the lane owner is genuinely absent.
    """
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

    lane_owner = str(lane_record.get("last_pool_takeover_id") or "").strip()
    top_owner = str(state.get("last_pool_takeover_id") or "").strip() if isinstance(state, dict) else ""
    if lane_owner:
        return lane_owner, bool(top_owner and top_owner != lane_owner), "lane_owner"
    if lane_id == "lane-00" and top_owner:
        return top_owner, False, "legacy_lane00_mirror_fallback"
    return "", False, "owner_missing"


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
            canonical_owner, owner_conflict, owner_source = canonical_worker_owner(
                canonical_lanes,
                canonical_state,
                lane_id=lane_id,
                worker_project_key=worker_project_key,
            )
            if owner_source == "lane_project_mismatch":
                return {
                    "ok": False,
                    "error": "ACTION_SUBMIT_WORKER_MISMATCH",
                    "canonical_owner": None,
                    "owner_source": owner_source,
                    "owner_mirror_conflict": owner_conflict,
                }
            if canonical_owner and canonical_owner != worker_ref:
                return {
                    "ok": False,
                    "error": "ACTION_SUBMIT_WORKER_MISMATCH",
                    "canonical_owner": canonical_owner,
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


def _recovery_identity(
    task_id: str,
    generation: int,
    reason: str,
    source_dispatch_id: str,
    worker_ref: str,
) -> tuple[str, str, str]:
    seed = f"{task_id}|recover|{generation}|{reason}|{source_dispatch_id}|{worker_ref}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return (
        f"dispatch-{digest[:24]}",
        f"fence-{digest[24:56]}",
        f"wake-{digest[:32]}",
    )


def _fresh_recovery_dispatch(
    *,
    task_id: str,
    current: dict[str, Any],
    reason: str,
    worker_ref: str,
    continuation_ref: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generation = int(current.get("generation") or 0) + 1
    source_dispatch_id = str(current.get("dispatch_id") or "")
    dispatch_id, fence_token, wake_id = _recovery_identity(
        task_id,
        generation,
        reason,
        source_dispatch_id,
        worker_ref,
    )
    now = utc_now()
    recovered_from = {
        "dispatch_id": source_dispatch_id,
        "generation": int(current.get("generation") or 0),
        "fence_token": str(current.get("fence_token") or ""),
        "state": str(current.get("state") or ""),
        "acked_at": current.get("acked_at"),
        "acked_by_worker_ref": current.get("acked_by_worker_ref"),
        "reason": reason,
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
        "continuation_ref": continuation_ref or current.get("continuation_ref"),
        "wait_ref": None,
        "ack_source": None,
        "recovered_from": recovered_from,
    }
    return dispatch, recovered_from


def _wake_payload(
    *,
    project_id: str,
    task_id: str,
    backend_cl_rel: str,
    lane_id: str,
    worker_project_key: str,
    dispatch: dict[str, Any],
    result_ref: str | None,
    reason: str,
) -> dict[str, Any]:
    wake = {
        "v": 1,
        "wake_id": dispatch["wake_id"],
        "project_id": project_id,
        "state": "NEED_AGENT",
        "created_at": dispatch["requested_at"],
        "repo": "example-owner/cah-private",
        "run_id": f"{task_id}-recovery-g{dispatch['generation']}-{reason}",
        "lane_id": lane_id,
        "worker_project_key": worker_project_key,
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
    state: dict[str, Any],
    lanes: dict[str, Any],
    lane_id: str,
    outgoing_worker_ref: str,
    packet_ref: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    top = state.get("worker_rollover_request")
    if isinstance(top, dict):
        candidates.append(top)
    for lane in lanes.get("lanes") or []:
        if not isinstance(lane, dict) or str(lane.get("lane_id") or "") != lane_id:
            continue
        req = lane.get("worker_rollover_request")
        if isinstance(req, dict):
            candidates.append(req)
        break
    for item in candidates:
        request_handoff = str(item.get("handoff_id") or item.get("outgoing_pool_id") or "")
        if request_handoff != outgoing_worker_ref:
            continue
        if str(item.get("handoff_packet_ref") or "") != packet_ref:
            continue
        if str(item.get("reason") or "") not in {"context_compacted", "semantic_stall"}:
            continue
        return item
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
                return {
                    "ok": True,
                    "already_recovered": already,
                    "dispatch": current,
                    "backend_cl": backend_cl_rel,
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
            if str(lane_record.get("last_pool_takeover_id") or "") != successor_worker_ref:
                return {"ok": False, "error": "HANDOFF_SUCCESSOR_NOT_CANONICAL"}

            rollover = _matching_rollover_request(
                state, lanes, lane_id, outgoing_worker_ref, packet_ref
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
                state_path = worktree / "state/chatgpt.json"
                lanes_path = worktree / "state/lanes.json"
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

                new_dispatch, old_record = _fresh_recovery_dispatch(
                    task_id=task_id,
                    current=current_dispatch,
                    reason="worker_handoff",
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

                current_state = json.loads(state_path.read_text(encoding="utf-8"))
                top_req = current_state.get("worker_rollover_request")
                if isinstance(top_req, dict):
                    top_outgoing = str(top_req.get("handoff_id") or top_req.get("outgoing_pool_id") or "")
                    if top_outgoing == outgoing_worker_ref:
                        current_state["worker_rollover_request"] = None
                if lane_id == "lane-00":
                    current_state["last_pool_takeover_id"] = successor_worker_ref
                    current_state["phase"] = "RUNNING"
                    current_state["active_task"] = task_id
                    current_state["active_dispatch_ref"] = backend_cl_rel
                    current_state["handoff_packet_ref"] = packet_ref
                    current_state["next_reads"] = [backend_cl_rel, packet_ref]
                    current_state["next_action"] = (
                        f"Resume {task_id} from the verified handoff packet under the fresh fenced dispatch. "
                        "Continue from the durable frontier; do not repeat completed work."
                    )
                    current_state["fault_boundary"] = "none"
                state_path.write_text(json.dumps(current_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                current_lanes = json.loads(lanes_path.read_text(encoding="utf-8"))
                for item in current_lanes.get("lanes") or []:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("lane_id") or "") != lane_id or str(item.get("project_key") or "") != worker_project_key:
                        continue
                    item["last_pool_takeover_id"] = successor_worker_ref
                    req0 = item.get("worker_rollover_request")
                    if isinstance(req0, dict):
                        item_outgoing = str(req0.get("handoff_id") or req0.get("outgoing_pool_id") or "")
                        if item_outgoing == outgoing_worker_ref:
                            item["worker_rollover_request"] = None
                    break
                current_lanes["updated_at"] = new_dispatch["requested_at"]
                lanes_path.write_text(json.dumps(current_lanes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                wake = _wake_payload(
                    project_id=project_id,
                    task_id=task_id,
                    backend_cl_rel=backend_cl_rel,
                    lane_id=lane_id,
                    worker_project_key=worker_project_key,
                    dispatch=new_dispatch,
                    result_ref=packet_ref,
                    reason="worker-handoff",
                )
                wake_rel = f"requests/worker-wake/{new_dispatch['wake_id']}.json"
                wake_path = worktree / wake_rel
                wake_path.parent.mkdir(parents=True, exist_ok=True)
                wake_path.write_text(json.dumps(wake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                _run(worktree, "add", backend_cl_rel, "state/chatgpt.json", "state/lanes.json", wake_rel)
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
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)

    return {"ok": False, "error": "HANDOFF_COMPLETE_RETRY_EXHAUSTED"}


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
    if not isinstance(task, dict) or str(task.get("kind") or "") != "parallel_semantic_branch":
        return None

    work_branch = str(task.get("work_branch") or "").strip()
    foreground_cl_rel = str(task.get("foreground_cl") or "").replace("\\", "/").strip()
    if not work_branch or not foreground_cl_rel:
        return None

    try:
        result_rel = branch_result_ref(task)
        bg = json.loads(store._git("show", f"{canonical_sha}:{backend_cl_rel}").stdout)
        fg = json.loads(store._git("show", f"{canonical_sha}:{foreground_cl_rel}").stdout)
        store._git("fetch", "--quiet", "--no-tags", store.git_remote, work_branch)
        branch_head = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
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
        _run(worktree, "commit", "-m", f"Finalize parallel branch {task_id}")
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


def reconcile_dispatch_liveness(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Recover an orphaned RUNNING semantic dispatch on a fresh generation/fence."""
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
            canonical_owner, owner_conflict, owner_source = canonical_worker_owner(
                canonical_lanes,
                canonical_state,
                lane_id=lane_id,
                worker_project_key=worker_project_key,
            )
            if owner_source == "lane_project_mismatch" or (canonical_owner and canonical_owner != worker_ref):
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

            worktree = store.runtime / f"liveness-recover-{task_id}-{generation}-{attempt}"
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
                ):
                    return {"ok": False, "error": "LIVENESS_DISPATCH_CHANGED"}

                new_dispatch, old_record = _fresh_recovery_dispatch(
                    task_id=task_id,
                    current=current_dispatch,
                    reason=recovery_reason,
                    worker_ref=worker_ref,
                    continuation_ref=str(current_dispatch.get("continuation_ref") or bg.get("result_ref") or "") or None,
                )
                current_bg["dispatch"] = new_dispatch
                current_bg["overall"] = "READY"
                current_bg["updated_at"] = new_dispatch["requested_at"]
                current_bg["wait_ref"] = None
                current_bg["error"] = None
                bg_path.write_text(json.dumps(current_bg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                wake = _wake_payload(
                    project_id=project_id,
                    task_id=task_id,
                    backend_cl_rel=backend_cl_rel,
                    lane_id=lane_id,
                    worker_project_key=worker_project_key,
                    dispatch=new_dispatch,
                    result_ref=str(new_dispatch.get("continuation_ref") or "") or None,
                    reason=recovery_reason.replace("_", "-"),
                )
                wake_rel = f"requests/worker-wake/{new_dispatch['wake_id']}.json"
                wake_path = worktree / wake_rel
                wake_path.parent.mkdir(parents=True, exist_ok=True)
                wake_path.write_text(json.dumps(wake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                _run(worktree, "add", backend_cl_rel, wake_rel)
                _run(worktree, "commit", "-m", f"Recover stalled semantic dispatch for {task_id}")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt < 2:
                        continue
                    return {"ok": False, "error": "GIT_LIVENESS_RECOVERY_PUSH_FAILED"}
                return {
                    "ok": True,
                    "matched": True,
                    "recovered": True,
                    "reason": recovery_reason,
                    "task_id": task_id,
                    "backend_cl": backend_cl_rel,
                    "dispatch": new_dispatch,
                    "recovered_from": old_record,
                    "wake_ref": wake_rel,
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)

    return {"ok": False, "error": "LIVENESS_RECOVERY_RETRY_EXHAUSTED"}
