#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.git_process import run_git  # noqa: E402
from harness.wake import make_wake  # noqa: E402

LANE_RE = re.compile(r"^lane-[0-9]{2,}$")
KEY_RE = re.compile(r"^g-p-[A-Za-z0-9]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_lane(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("lane must be an object")
    lane_id = str(raw.get("lane_id") or "")
    display_name = str(raw.get("display_name") or "").strip()
    project_key = str(raw.get("project_key") or "")
    root = str(raw.get("project_root_url") or "").strip()
    if not LANE_RE.fullmatch(lane_id):
        raise ValueError("invalid lane_id")
    if not 1 <= len(display_name) <= 80:
        raise ValueError("invalid display_name")
    if not KEY_RE.fullmatch(project_key):
        raise ValueError("invalid project_key")
    expected_prefix = f"https://chatgpt.com/g/{project_key}"
    if not root.startswith(expected_prefix) or not root.rstrip("/").endswith("/project"):
        raise ValueError("project_root_url does not match project_key")
    return {
        "lane_id": lane_id,
        "display_name": display_name,
        "project_key": project_key,
        "project_root_url": root.rstrip("/"),
        "enabled": bool(raw.get("enabled", True)),
    }


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run_git(cwd, *args, timeout=30)


def _topology_matches_request(request: dict[str, Any], lanes: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(request, dict) or not isinstance(lanes, dict):
        return False, "request_or_lanes_invalid"
    request_id = str(request.get("request_id") or "")
    if str(lanes.get("source_request_id") or "") != request_id:
        return False, "source_request_id_mismatch"
    if int(lanes.get("topology_version") or 0) != int(request.get("desired_version") or 0):
        return False, "topology_version_mismatch"
    if int(lanes.get("registered_count") or -1) != int(request.get("registered_count") or -2):
        return False, "registered_count_mismatch"
    if int(lanes.get("enabled_count") or -1) != int(request.get("enabled_count") or -2):
        return False, "enabled_count_mismatch"

    desired = {
        str(item.get("lane_id") or ""): item
        for item in request.get("lanes") or []
        if isinstance(item, dict)
    }
    actual = {
        str(item.get("lane_id") or ""): item
        for item in lanes.get("lanes") or []
        if isinstance(item, dict)
    }
    if set(desired) != set(actual):
        return False, "lane_id_set_mismatch"
    for lane_id, want in desired.items():
        got = actual[lane_id]
        for key in ("display_name", "project_key", "project_root_url", "enabled"):
            if got.get(key) != want.get(key):
                return False, f"lane_{lane_id}_{key}_mismatch"
    return True, "matched"


def finalize_topology_request(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Deterministically finalize a topology control transaction after Worker reconcile.

    The AI Worker owns semantic reconciliation of state/lanes.json. The bridge owns
    the mechanical transaction close once the canonical topology proves that the
    exact request was applied.
    """
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")

    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
                request = json.loads(store._git("show", "FETCH_HEAD:state/topology_request.json").stdout)
                lanes = json.loads(store._git("show", "FETCH_HEAD:state/lanes.json").stdout)
            except Exception:
                return {"ok": False, "finalized": False, "error": "GIT_TOPOLOGY_FINALIZE_READ_FAILED"}

            control = state.get("control_request") if isinstance(state, dict) else None
            if not isinstance(control, dict) or control.get("kind") != "topology_reconcile":
                return {"ok": True, "finalized": False, "idle": "no_topology_control_request"}
            if str(control.get("status") or "") == "DONE":
                return {
                    "ok": True,
                    "finalized": False,
                    "idle": "already_done",
                    "request_id": str(control.get("request_id") or ""),
                }
            if str(control.get("status") or "") not in {"PENDING", "APPLYING"}:
                return {
                    "ok": True,
                    "finalized": False,
                    "idle": f"control_status_{control.get('status')}",
                    "request_id": str(control.get("request_id") or ""),
                }

            request_id = str(request.get("request_id") or "")
            if request_id != str(control.get("request_id") or ""):
                return {"ok": True, "finalized": False, "idle": "request_id_mismatch"}

            matched, reason = _topology_matches_request(request, lanes)
            if not matched:
                return {
                    "ok": True,
                    "finalized": False,
                    "idle": "topology_not_yet_applied",
                    "reason": reason,
                    "request_id": request_id,
                }

            worktree = store.runtime / f"topology-finalize-{request_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")

                state_path = worktree / "state" / "chatgpt.json"
                current_state = json.loads(state_path.read_text(encoding="utf-8"))
                current_control = current_state.get("control_request")
                if not isinstance(current_control, dict) or str(current_control.get("request_id") or "") != request_id:
                    return {"ok": True, "finalized": False, "idle": "control_changed_during_finalize"}

                resume = request.get("resume") if isinstance(request.get("resume"), dict) else {}
                current_state["control_request"] = {
                    **current_control,
                    "status": "DONE",
                }
                if resume.get("phase") is not None:
                    current_state["phase"] = resume.get("phase")
                current_state["next_action"] = resume.get("next_action")
                current_state["next_reads"] = resume.get("next_reads") if isinstance(resume.get("next_reads"), list) else []
                current_state["updated"] = utc_now()[:10]
                current_state["writeback_reason"] = (
                    f"Topology control request {request_id} finalized deterministically after "
                    "canonical state/lanes.json matched the requested topology."
                )
                state_path.write_text(
                    json.dumps(current_state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                _run(worktree, "add", "state/chatgpt.json")
                _run(worktree, "commit", "-m", f"Finalize topology request {request_id} [skip ci]")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "finalized": False, "error": "GIT_TOPOLOGY_FINALIZE_PUSH_FAILED"}
                return {
                    "ok": True,
                    "finalized": True,
                    "request_id": request_id,
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)

    return {"ok": False, "finalized": False, "error": "TOPOLOGY_FINALIZE_RETRY_EXHAUSTED"}


def stage_topology_request(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")
    lanes_raw = req.get("lanes")
    if not isinstance(lanes_raw, list) or len(lanes_raw) > 16:
        raise ValueError("lanes must be an array with at most 16 entries")
    lanes = [_safe_lane(item) for item in lanes_raw]
    keys = [lane["project_key"] for lane in lanes]
    ids = [lane["lane_id"] for lane in lanes]
    if len(set(keys)) != len(keys) or len(set(ids)) != len(ids):
        raise ValueError("duplicate lane_id or project_key")

    desired_version = int(req.get("desired_version") or 1)
    if desired_version < 1:
        raise ValueError("desired_version must be >=1")

    control_lane_id = str(req.get("control_lane_id") or "")
    worker_project_key = str(req.get("worker_project_key") or "")
    if not LANE_RE.fullmatch(control_lane_id) or not KEY_RE.fullmatch(worker_project_key):
        raise ValueError("control lane identity required")

    request_id = str(req.get("request_id") or f"topo-{uuid.uuid4()}")
    request_id = store._safe_id(request_id, "request_id")
    created_at = utc_now()
    request = {
        "v": 1,
        "request_id": request_id,
        "kind": "topology_reconcile",
        "desired_version": desired_version,
        "created_at": created_at,
        "control_lane_id": control_lane_id,
        "worker_project_key": worker_project_key,
        "registered_count": len(lanes),
        "enabled_count": sum(1 for lane in lanes if lane["enabled"]),
        "lanes": lanes,
    }

    with store.git_lock:
        store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
        worktree = store.runtime / f"topology-{request_id}"
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        try:
            store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
            _run(worktree, "config", "user.name", "gah-local-bridge")
            _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")

            state_dir = worktree / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            state_path = state_dir / "chatgpt.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            request["resume"] = {
                "phase": state.get("phase"),
                "next_action": state.get("next_action"),
                "next_reads": state.get("next_reads") if isinstance(state.get("next_reads"), list) else [],
            }
            (state_dir / "topology_request.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            state["control_request"] = {
                "request_id": request_id,
                "kind": "topology_reconcile",
                "ref": "state/topology_request.json",
                "status": "PENDING",
                "created_at": created_at,
                "control_lane_id": control_lane_id,
                "worker_project_key": worker_project_key,
            }
            state["phase"] = "NEED_AGENT"
            state["next_reads"] = ["state/topology_request.json", "state/lanes.json"]
            state["next_action"] = (
                "CONTROL PRIORITY: reconcile state/topology_request.json into canonical state/lanes.json exactly. "
                "Preserve matching lane takeover/rollover fields, update counts/version/source_request_id, mark control_request DONE or ERROR, "
                "then restore the prior phase/next_action/next_reads recorded under topology_request.resume and checkpoint before continuing."
            )
            state["updated"] = created_at[:10]
            state["writeback_reason"] = "Staged backend lane topology reconciliation request from the browser lane registry."
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            _run(worktree, "add", "state/topology_request.json", "state/chatgpt.json")
            _run(worktree, "commit", "-m", f"Stage topology request {request_id} [skip ci]")
            pushed = _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
            commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
        finally:
            try:
                store._git("worktree", "remove", "--force", str(worktree))
            except Exception:
                shutil.rmtree(worktree, ignore_errors=True)

    wake = make_wake(
        project_id,
        state="NEED_AGENT",
        repo="example-owner/cah-private",
        result_ref="state/topology_request.json",
        lane_id=control_lane_id,
        worker_project_key=worker_project_key,
        kind="topology_reconcile",
    )
    emitted = store.emit(wake)
    return {
        "ok": True,
        "request_id": request_id,
        "desired_version": desired_version,
        "commit_sha": commit_sha,
        "wake_id": wake["wake_id"],
        "control_lane_id": control_lane_id,
        "worker_project_key": worker_project_key,
        "duplicate_wake": bool(emitted.get("duplicate")),
    }


def topology_status(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")

    finalize = finalize_topology_request(store, req)

    with store.git_lock:
        try:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            lanes = json.loads(store._git("show", "FETCH_HEAD:state/lanes.json").stdout)
        except Exception:
            lanes = None
        try:
            state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
        except Exception:
            state = {}

    return {
        "ok": True,
        "lanes": lanes,
        "control_request": state.get("control_request") if isinstance(state, dict) else None,
        "finalize": finalize,
    }
