#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .topology import _run, utc_now
except ImportError:
    from topology import _run, utc_now


def control_status(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")

    with store.git_lock:
        try:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
        except Exception:
            return {"ok": False, "error": "GIT_CONTROL_STATE_FAILED"}

    control = state.get("control_request") if isinstance(state, dict) else None
    return {
        "ok": True,
        "control_request": control if isinstance(control, dict) else None,
        "state_updated": str(state.get("updated") or "") if isinstance(state, dict) else None,
    }



def begin_lane_clear(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    request_id = str(req.get("request_id") or "").strip()
    lane_id = str(req.get("lane_id") or "").strip()
    project_key = str(req.get("worker_project_key") or "").strip()
    if not project_id:
        raise ValueError("project_id required")
    store._safe_id(request_id, "request_id")
    if not lane_id.startswith("lane-") or not project_key.startswith("g-p-"):
        raise ValueError("lane identity required")

    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
            control = state.get("control_request") if isinstance(state, dict) else None
            if not isinstance(control, dict):
                return {"ok": False, "error": "CONTROL_REQUEST_MISSING"}
            if str(control.get("request_id") or "") != request_id:
                return {"ok": False, "error": "CONTROL_REQUEST_ID_MISMATCH"}
            if str(control.get("kind") or "") != "lane_clear":
                return {"ok": False, "error": "CONTROL_REQUEST_KIND_MISMATCH"}
            if str(control.get("control_lane_id") or "") != lane_id or str(control.get("worker_project_key") or "") != project_key:
                return {"ok": False, "error": "CONTROL_TARGET_MISMATCH"}
            status = str(control.get("status") or "")
            if status == "APPLYING":
                return {"ok": True, "applying": False, "idle": "already_applying"}
            if status != "PENDING":
                return {"ok": False, "error": f"CONTROL_STATUS_{status or 'UNKNOWN'}"}

            worktree = store.runtime / f"lane-clear-begin-{request_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                state_path = worktree / "state" / "chatgpt.json"
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current_control = current.get("control_request")
                current["control_request"] = {**current_control, "status": "APPLYING"}
                current["updated"] = utc_now()[:10]
                current["writeback_reason"] = f"lane_clear {request_id} is being executed by the browser control runtime."
                state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                _run(worktree, "add", "state/chatgpt.json")
                _run(worktree, "commit", "-m", f"Begin lane clear {request_id} [skip ci]")
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "GIT_LANE_CLEAR_BEGIN_PUSH_FAILED"}
                return {"ok": True, "applying": True, "request_id": request_id}
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)
    return {"ok": False, "error": "LANE_CLEAR_BEGIN_RETRY_EXHAUSTED"}

def complete_lane_clear(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    request_id = str(req.get("request_id") or "").strip()
    lane_id = str(req.get("lane_id") or "").strip()
    project_key = str(req.get("worker_project_key") or "").strip()
    deleted_count = int(req.get("deleted_count") or 0)
    remaining_count = int(req.get("remaining_count") or 0)
    if not project_id:
        raise ValueError("project_id required")
    store._safe_id(request_id, "request_id")
    if not lane_id.startswith("lane-"):
        raise ValueError("lane_id required")
    if not project_key.startswith("g-p-"):
        raise ValueError("worker_project_key required")
    if deleted_count < 0 or remaining_count < 0:
        raise ValueError("invalid counts")
    if remaining_count != 0:
        return {"ok": False, "error": "LANE_CLEAR_NOT_EMPTY", "remaining_count": remaining_count}

    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_CONTROL_STATE_FAILED"}

            control = state.get("control_request") if isinstance(state, dict) else None
            if not isinstance(control, dict):
                return {"ok": False, "error": "CONTROL_REQUEST_MISSING"}
            if str(control.get("request_id") or "") != request_id:
                return {"ok": False, "error": "CONTROL_REQUEST_ID_MISMATCH"}
            if str(control.get("kind") or "") != "lane_clear":
                return {"ok": False, "error": "CONTROL_REQUEST_KIND_MISMATCH"}
            if str(control.get("control_lane_id") or "") != lane_id:
                return {"ok": False, "error": "CONTROL_LANE_MISMATCH"}
            if str(control.get("worker_project_key") or "") != project_key:
                return {"ok": False, "error": "CONTROL_PROJECT_MISMATCH"}
            if str(control.get("scope") or "") != "all_project_conversations":
                return {"ok": False, "error": "CONTROL_SCOPE_MISMATCH"}
            status = str(control.get("status") or "")
            if status == "DONE":
                return {"ok": True, "completed": False, "idle": "already_done", "request_id": request_id}
            if status not in {"PENDING", "APPLYING"}:
                return {"ok": False, "error": f"CONTROL_STATUS_{status or 'UNKNOWN'}"}

            worktree = store.runtime / f"lane-clear-finalize-{request_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                state_path = worktree / "state" / "chatgpt.json"
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current_control = current.get("control_request")
                if not isinstance(current_control, dict) or str(current_control.get("request_id") or "") != request_id:
                    return {"ok": False, "error": "CONTROL_CHANGED_DURING_CLEAR"}

                current["control_request"] = {
                    **current_control,
                    "status": "DONE",
                    "completed_at": utc_now(),
                    "result": {
                        "deleted_count": deleted_count,
                        "remaining_count": 0,
                        "lane_id": lane_id,
                        "project_key": project_key,
                    },
                }
                current["updated"] = utc_now()[:10]
                current["writeback_reason"] = (
                    f"Explicit lane_clear {request_id} completed for {lane_id}; "
                    f"deleted {deleted_count} Project conversations and preserved the lane/Project registration."
                )
                state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                _run(worktree, "add", "state/chatgpt.json")
                _run(worktree, "commit", "-m", f"Complete lane clear {request_id} [skip ci]")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "GIT_LANE_CLEAR_FINALIZE_PUSH_FAILED"}
                return {
                    "ok": True,
                    "completed": True,
                    "request_id": request_id,
                    "deleted_count": deleted_count,
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)

    return {"ok": False, "error": "LANE_CLEAR_FINALIZE_RETRY_EXHAUSTED"}


def complete_task_cell_prompt(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    request_id = str(req.get("request_id") or "").strip()
    task_cell_project_key = str(req.get("task_cell_project_key") or "").strip()
    response_started = bool(req.get("response_started"))
    if not project_id:
        raise ValueError("project_id required")
    store._safe_id(request_id, "request_id")
    if not task_cell_project_key.startswith("g-p-"):
        raise ValueError("task_cell_project_key required")
    if not response_started:
        return {"ok": False, "error": "TASK_CELL_RESPONSE_NOT_STARTED"}

    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_CONTROL_STATE_FAILED"}

            control = state.get("control_request") if isinstance(state, dict) else None
            if not isinstance(control, dict):
                return {"ok": False, "error": "CONTROL_REQUEST_MISSING"}
            if str(control.get("request_id") or "") != request_id:
                return {"ok": False, "error": "CONTROL_REQUEST_ID_MISMATCH"}
            if str(control.get("kind") or "") != "task_cell_prompt":
                return {"ok": False, "error": "CONTROL_REQUEST_KIND_MISMATCH"}
            if str(control.get("task_cell_project_key") or "") != task_cell_project_key:
                return {"ok": False, "error": "CONTROL_PROJECT_MISMATCH"}
            status = str(control.get("status") or "")
            if status == "DONE":
                return {"ok": True, "completed": False, "idle": "already_done", "request_id": request_id}
            if status != "PENDING":
                return {"ok": False, "error": f"CONTROL_STATUS_{status or 'UNKNOWN'}"}

            worktree = store.runtime / f"task-cell-prompt-{request_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                state_path = worktree / "state" / "chatgpt.json"
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current_control = current.get("control_request")
                if not isinstance(current_control, dict) or str(current_control.get("request_id") or "") != request_id:
                    return {"ok": False, "error": "CONTROL_CHANGED_DURING_TASK_CELL_PROMPT"}

                current["control_request"] = {
                    **current_control,
                    "status": "DONE",
                    "completed_at": utc_now(),
                    "result": {
                        "task_cell_project_key": task_cell_project_key,
                        "response_started": True,
                    },
                }
                current["updated"] = utc_now()[:10]
                current["writeback_reason"] = (
                    f"Task Cell control prompt {request_id} was delivered and assistant response start was observed."
                )
                state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                _run(worktree, "add", "state/chatgpt.json")
                _run(worktree, "commit", "-m", f"Complete Task Cell prompt {request_id} [skip ci]")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "GIT_TASK_CELL_PROMPT_PUSH_FAILED"}
                return {
                    "ok": True,
                    "completed": True,
                    "request_id": request_id,
                    "commit_sha": commit_sha,
                }
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)
    return {"ok": False, "error": "TASK_CELL_PROMPT_RETRY_EXHAUSTED"}


def _complete_simple_control(
    store: Any,
    req: dict[str, Any],
    *,
    expected_kind: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    request_id = str(req.get("request_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")
    store._safe_id(request_id, "request_id")

    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_CONTROL_STATE_FAILED"}
            control = state.get("control_request") if isinstance(state, dict) else None
            if not isinstance(control, dict):
                return {"ok": False, "error": "CONTROL_REQUEST_MISSING"}
            if str(control.get("request_id") or "") != request_id:
                return {"ok": False, "error": "CONTROL_REQUEST_ID_MISMATCH"}
            if str(control.get("kind") or "") != expected_kind:
                return {"ok": False, "error": "CONTROL_REQUEST_KIND_MISMATCH"}
            status = str(control.get("status") or "")
            if status == "DONE":
                return {"ok": True, "completed": False, "idle": "already_done", "request_id": request_id}
            if status != "PENDING":
                return {"ok": False, "error": f"CONTROL_STATUS_{status or 'UNKNOWN'}"}

            worktree = store.runtime / f"simple-control-{request_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                state_path = worktree / "state" / "chatgpt.json"
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current_control = current.get("control_request")
                if not isinstance(current_control, dict) or str(current_control.get("request_id") or "") != request_id:
                    return {"ok": False, "error": "CONTROL_CHANGED_DURING_COMPLETION"}
                current["control_request"] = {
                    **current_control,
                    "status": "DONE",
                    "completed_at": utc_now(),
                    "result": result,
                }
                current["updated"] = utc_now()[:10]
                current["writeback_reason"] = f"Completed control request {request_id} ({expected_kind})."
                state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                _run(worktree, "add", "state/chatgpt.json")
                _run(worktree, "commit", "-m", f"Complete control {request_id} [skip ci]")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "GIT_CONTROL_COMPLETION_PUSH_FAILED"}
                return {"ok": True, "completed": True, "request_id": request_id, "commit_sha": commit_sha}
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)
    return {"ok": False, "error": "CONTROL_COMPLETION_RETRY_EXHAUSTED"}


def complete_lane_pool_reset(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    lane_id = str(req.get("lane_id") or "").strip()
    project_key = str(req.get("worker_project_key") or "").strip()
    if not lane_id.startswith("lane-") or not project_key.startswith("g-p-"):
        raise ValueError("lane identity required")
    return _complete_simple_control(
        store,
        req,
        expected_kind="lane_pool_reset",
        result={"lane_id": lane_id, "project_key": project_key, "local_pool_reset": True},
    )


def complete_task_cell_clear(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    project_key = str(req.get("task_cell_project_key") or "").strip()
    target_request_id = str(req.get("target_request_id") or "").strip()
    deleted_conversation_id = str(req.get("deleted_conversation_id") or "").strip()
    if not project_key.startswith("g-p-") or not target_request_id or not deleted_conversation_id:
        raise ValueError("task cell cleanup identity required")
    return _complete_simple_control(
        store,
        req,
        expected_kind="task_cell_clear",
        result={
            "task_cell_project_key": project_key,
            "target_request_id": target_request_id,
            "deleted_conversation_id": deleted_conversation_id,
        },
    )
