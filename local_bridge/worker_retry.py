"""Exact same-generation physical Worker retry transactions.

This module is intentionally separate from the HTTP bridge and semantic Worker
handoff code. It requeues one already-authorized wake without changing
generation/dispatch/fence/wake/Worker ownership.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.git_process import run_git


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _worker_retry_identity(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    identity = {
        "project_id": project_id,
        "request_id": store._safe_id(req.get("request_id"), "request_id"),
        "helper_request_id": store._safe_id(req.get("helper_request_id"), "helper_request_id"),
        "task_id": store._safe_id(req.get("task_id"), "task_id"),
        "child_task_id": store._safe_id(req.get("child_task_id"), "child_task_id"),
        "backend_cl": store._safe_repo_rel(req.get("backend_cl"), "backend_cl"),
        "dispatch_id": store._safe_id(req.get("dispatch_id"), "dispatch_id"),
        "generation": int(req.get("dispatch_generation") or 0),
        "fence_token": str(req.get("fence_token") or ""),
        "wake_id": store._safe_id(req.get("wake_id"), "wake_id"),
        "worker_ref": store._safe_id(req.get("worker_ref"), "worker_ref"),
        "old_conversation_id": store._safe_id(
            req.get("old_conversation_id"),
            "old_conversation_id",
        ),
    }
    if (
        not project_id
        or identity["generation"] < 1
        or not 8 <= len(identity["fence_token"]) <= 256
    ):
        raise ValueError("invalid same-generation retry identity")
    return identity

def _worker_retry_backend_matches(backend: Any, identity: dict[str, Any]) -> bool:
    dispatch = (
        backend.get("dispatch")
        if isinstance(backend, dict) and isinstance(backend.get("dispatch"), dict)
        else {}
    )
    return (
        isinstance(backend, dict)
        and str(backend.get("task_id") or "") == identity["child_task_id"]
        and str(backend.get("overall") or "") == "RUNNING"
        and str(dispatch.get("state") or "") == "RUNNING"
        and str(dispatch.get("dispatch_id") or "") == identity["dispatch_id"]
        and int(dispatch.get("generation") or 0) == identity["generation"]
        and str(dispatch.get("fence_token") or "") == identity["fence_token"]
        and str(dispatch.get("wake_id") or "") == identity["wake_id"]
        and str(dispatch.get("acked_by_worker_ref") or "") == identity["worker_ref"]
    )

def begin_same_generation_retry(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Requeue one already-consumed Worker wake with the exact same scheduler identity."""
    identity = _worker_retry_identity(store, req)
    request_id = identity["request_id"]
    helper_request_id = identity["helper_request_id"]
    task_id = identity["task_id"]
    child_task_id = identity["child_task_id"]
    backend_cl = identity["backend_cl"]
    dispatch_id = identity["dispatch_id"]
    generation = identity["generation"]
    fence_token = identity["fence_token"]
    wake_id = identity["wake_id"]
    worker_ref = identity["worker_ref"]
    old_conversation_id = identity["old_conversation_id"]
    lane_id = str(req.get("lane_id") or "").strip()
    worker_project_key = str(req.get("worker_project_key") or "").strip()
    if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
        raise ValueError("invalid same-generation retry lane identity")

    with store.git_lock:
        try:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = json.loads(store._git("show", f"{base_sha}:state/chatgpt.json").stdout)
            cell = json.loads(store._git("show", f"{base_sha}:state/task_cells/{task_id}.json").stdout)
            backend = json.loads(store._git("show", f"{base_sha}:{backend_cl}").stdout)
            wake = json.loads(store._git("show", f"{base_sha}:requests/worker-wake/{wake_id}.json").stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return {"ok": False, "error": "WORKER_RETRY_CANONICAL_READ_FAILED"}

    control = state.get("control_request") if isinstance(state, dict) else None
    if not isinstance(control, dict) or str(control.get("request_id") or "") != request_id:
        return {"ok": False, "error": "WORKER_RETRY_CONTROL_MISMATCH"}
    if str(control.get("kind") or "") != "worker_same_generation_retry":
        return {"ok": False, "error": "WORKER_RETRY_CONTROL_KIND_MISMATCH"}
    if str(control.get("status") or "") == "DONE":
        result = control.get("result") if isinstance(control.get("result"), dict) else {}
        return {"ok": True, "duplicate": True, "completed": True, **result}
    if str(control.get("status") or "") not in {"PENDING", "APPLYING"}:
        return {"ok": False, "error": "WORKER_RETRY_CONTROL_STATE_INVALID"}
    exact_control = (
        str(control.get("helper_request_id") or "") == helper_request_id
        and str(control.get("child_task_id") or "") == child_task_id
        and str(control.get("backend_cl") or "") == backend_cl
        and str(control.get("dispatch_id") or "") == dispatch_id
        and int(control.get("dispatch_generation") or 0) == generation
        and str(control.get("fence_token") or "") == fence_token
        and str(control.get("wake_id") or "") == wake_id
        and str(control.get("worker_ref") or "") == worker_ref
        and str(control.get("control_lane_id") or "") == lane_id
        and str(control.get("worker_project_key") or "") == worker_project_key
        and str(control.get("old_conversation_id") or "") == old_conversation_id
        and control.get("exact_id_only") is True
        and str(control.get("retry_mode") or "") == "same_generation"
    )
    if not exact_control:
        return {"ok": False, "error": "WORKER_RETRY_CONTROL_IDENTITY_MISMATCH"}

    if not _worker_retry_backend_matches(backend, identity):
        return {"ok": False, "error": "WORKER_RETRY_BACKEND_IDENTITY_MISMATCH"}

    exact_wake = (
        str(wake.get("task_id") or "") == child_task_id
        and str(wake.get("backend_cl") or "") == backend_cl
        and str(wake.get("dispatch_id") or "") == dispatch_id
        and int(wake.get("dispatch_generation") or 0) == generation
        and str(wake.get("fence_token") or "") == fence_token
        and str(wake.get("wake_id") or "") == wake_id
        and str(wake.get("owner_task_id") or "") == task_id
        and str(wake.get("lane_id") or "") == lane_id
        and str(wake.get("worker_project_key") or "") == worker_project_key
    )
    if not exact_wake:
        return {"ok": False, "error": "WORKER_RETRY_WAKE_IDENTITY_MISMATCH"}

    planner_control = cell.get("planner_control") if isinstance(cell, dict) else None
    runtime = planner_control.get("runtime") if isinstance(planner_control, dict) else None
    pending_outputs = runtime.get("pending_role_outputs") if isinstance(runtime, dict) else None
    helper_output = next((
        row for row in (pending_outputs or [])
        if isinstance(row, dict)
        and str(row.get("request_id") or "") == helper_request_id
        and str(row.get("kind") or "") == "worker_helper_result"
        and str(row.get("state") or "") == "RETRY_STAGED"
        and str(row.get("retry_request_id") or "") == request_id
    ), None)
    if helper_output is None:
        return {"ok": False, "error": "WORKER_RETRY_HELPER_AUTHORITY_MISSING"}

    retry_meta = {
        "request_id": request_id,
        "helper_request_id": helper_request_id,
        "old_conversation_id": old_conversation_id,
        "worker_ref": worker_ref,
        "attempted_at": utc_now(),
    }
    requeued = {**wake, "replace_conversation": True, "_same_generation_retry": retry_meta}

    # Commit APPLYING before publishing runnable transport. If publication fails,
    # the same APPLYING control is re-entered by Host and repairs the local inbox.
    if str(control.get("status") or "") == "PENDING":
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                latest_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
                latest_state = json.loads(store._git("show", f"{latest_sha}:state/chatgpt.json").stdout)
                latest_control = latest_state.get("control_request")
                if not isinstance(latest_control, dict) or str(latest_control.get("request_id") or "") != request_id:
                    return {"ok": False, "error": "WORKER_RETRY_CONTROL_CHANGED"}
                if str(latest_control.get("status") or "") == "PENDING":
                    worktree = store.runtime / f"worker-retry-begin-{request_id}"
                    if worktree.exists():
                        shutil.rmtree(worktree, ignore_errors=True)
                    try:
                        store._git("worktree", "add", "--force", "--detach", str(worktree), latest_sha)
                        _state_path = worktree / "state" / "chatgpt.json"
                        next_state = json.loads(_state_path.read_text(encoding="utf-8"))
                        next_control = next_state.get("control_request")
                        next_control["status"] = "APPLYING"
                        next_control["applying_at"] = utc_now()
                        next_control["result"] = {
                            "requeued": True,
                            "wake_id": wake_id,
                            "dispatch_id": dispatch_id,
                            "dispatch_generation": generation,
                            "fence_token": fence_token,
                            "worker_ref": worker_ref,
                            "old_conversation_id": old_conversation_id,
                        }
                        next_state["control_request"] = next_control
                        next_state["updated"] = utc_now()[:10]
                        _state_path.write_text(json.dumps(next_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        run_git(worktree, "config", "user.name", "gah-local-bridge", timeout=20)
                        run_git(worktree, "config", "user.email", "gah-local-bridge@example.invalid", timeout=20)
                        run_git(worktree, "add", "state/chatgpt.json", timeout=20)
                        run_git(worktree, "commit", "-m", f"Begin same-generation Worker retry {request_id} [skip ci]", timeout=20)
                        commit_sha = run_git(worktree, "rev-parse", "HEAD", timeout=20).stdout.strip()
                        run_git(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}", timeout=30)
                    finally:
                        try:
                            store._git("worktree", "remove", "--force", str(worktree))
                        except Exception:
                            shutil.rmtree(worktree, ignore_errors=True)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return {"ok": False, "error": "WORKER_RETRY_BEGIN_PUSH_FAILED"}

    with store.lock:
        inbox_path = store._path(store.inbox, wake_id)
        claimed_path = store._path(store.claimed, wake_id)
        consumed_path = store._path(store.consumed, wake_id)
        existing = None
        for path in (inbox_path, claimed_path):
            if not path.exists():
                continue
            try:
                value = read_json(path)
                candidate = value.get("wake") if isinstance(value, dict) and isinstance(value.get("wake"), dict) else value
                meta = candidate.get("_same_generation_retry") if isinstance(candidate, dict) else None
                if isinstance(meta, dict) and str(meta.get("request_id") or "") == request_id:
                    existing = candidate
                    break
            except Exception:
                pass
        if existing is None:
            inbox_path.unlink(missing_ok=True)
            claimed_path.unlink(missing_ok=True)
            consumed_path.unlink(missing_ok=True)
            atomic_json(inbox_path, requeued)

    return {
        "ok": True,
        "duplicate": existing is not None,
        "requeued": True,
        "request_id": request_id,
        "wake_id": wake_id,
        "generation": generation,
        "worker_ref": worker_ref,
    }

def complete_same_generation_retry(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Durably confirm that the replacement physical Worker response-started."""
    identity = _worker_retry_identity(store, req)
    request_id = identity["request_id"]
    helper_request_id = identity["helper_request_id"]
    task_id = identity["task_id"]
    child_task_id = identity["child_task_id"]
    backend_cl = identity["backend_cl"]
    dispatch_id = identity["dispatch_id"]
    generation = identity["generation"]
    fence_token = identity["fence_token"]
    wake_id = identity["wake_id"]
    worker_ref = identity["worker_ref"]
    old_conversation_id = identity["old_conversation_id"]
    new_conversation_id = store._safe_id(req.get("new_conversation_id"), "new_conversation_id")
    if old_conversation_id == new_conversation_id:
        return {"ok": False, "error": "WORKER_RETRY_COMPLETE_IDENTITY_INVALID"}

    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
                state = json.loads(store._git("show", f"{base_sha}:state/chatgpt.json").stdout)
                cell = json.loads(store._git("show", f"{base_sha}:state/task_cells/{task_id}.json").stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_READ_FAILED"}

            control = state.get("control_request") if isinstance(state, dict) else None
            if not isinstance(control, dict) or str(control.get("request_id") or "") != request_id:
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_CONTROL_MISMATCH"}
            if str(control.get("kind") or "") != "worker_same_generation_retry":
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_CONTROL_KIND_MISMATCH"}
            if str(control.get("status") or "") == "DONE":
                result = control.get("result") if isinstance(control.get("result"), dict) else {}
                if str(result.get("new_conversation_id") or "") == new_conversation_id:
                    return {"ok": True, "duplicate": True, "completed": True, **result}
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_CONVERSATION_CONFLICT"}
            if str(control.get("status") or "") != "APPLYING":
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_CONTROL_NOT_APPLYING"}

            # The business dispatch may already be terminal or have advanced.
            # Complete the original receipt against its durable begin identity;
            # never rewrite the backend or manufacture another generation.
            exact = all(str(control.get(key) or "") == str(identity[key]) for key in (
                "task_id", "helper_request_id", "child_task_id", "backend_cl",
                "dispatch_id", "fence_token", "wake_id", "worker_ref", "old_conversation_id",
            )) and int(control.get("dispatch_generation") or 0) == generation
            if not exact or int(cell.get("control_epoch") or 0) != int(control.get("control_epoch") or 0):
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_IDENTITY_MISMATCH"}

            next_state = json.loads(json.dumps(state))
            next_control = next_state["control_request"]
            completed_at = utc_now()
            next_control["status"] = "DONE"
            next_control["completed_at"] = completed_at
            next_control["result"] = {
                **(next_control.get("result") if isinstance(next_control.get("result"), dict) else {}),
                "requeued": True,
                "response_started": True,
                "wake_id": wake_id,
                "dispatch_id": dispatch_id,
                "dispatch_generation": generation,
                "fence_token": fence_token,
                "worker_ref": worker_ref,
                "old_conversation_id": old_conversation_id,
                "new_conversation_id": new_conversation_id,
                "same_generation": True,
            }
            next_state["control_request"] = next_control
            next_state["updated"] = completed_at[:10]

            next_cell = json.loads(json.dumps(cell))
            planner_control = next_cell.get("planner_control") if isinstance(next_cell.get("planner_control"), dict) else {}
            runtime = planner_control.get("runtime") if isinstance(planner_control.get("runtime"), dict) else {}
            outputs = list(runtime.get("pending_role_outputs") or [])
            matched = False
            for index, row in enumerate(outputs):
                if not isinstance(row, dict):
                    continue
                if (
                    str(row.get("request_id") or "") == helper_request_id
                    and str(row.get("kind") or "") == "worker_helper_result"
                    and str(row.get("retry_request_id") or "") == request_id
                ):
                    outputs[index] = {
                        **row,
                        "state": "WORKER_STARTED",
                        "return_mode": "WORKER_SAME_GENERATION",
                        "retry_request_id": request_id,
                        "old_conversation_id": old_conversation_id,
                        "retry_conversation_id": new_conversation_id,
                        "next_role_started_at": completed_at,
                    }
                    matched = True
                    break
            if not matched:
                return {"ok": False, "error": "WORKER_RETRY_COMPLETE_HELPER_OUTPUT_MISSING"}
            runtime["pending_role_outputs"] = outputs
            planner_control["runtime"] = runtime
            next_cell["planner_control"] = planner_control
            next_cell["updated_at"] = completed_at

            worktree = store.runtime / f"worker-retry-complete-{request_id}-{attempt}"
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            try:
                store._git("worktree", "add", "--force", "--detach", str(worktree), base_sha)
                for rel, value in (
                    ("state/chatgpt.json", next_state),
                    (f"state/task_cells/{task_id}.json", next_cell),
                ):
                    path = worktree / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                run_git(worktree, "config", "user.name", "gah-local-bridge", timeout=20)
                run_git(worktree, "config", "user.email", "gah-local-bridge@example.invalid", timeout=20)
                run_git(worktree, "add", "state/chatgpt.json", f"state/task_cells/{task_id}.json", timeout=20)
                run_git(worktree, "commit", "-m", f"Complete same-generation Worker retry {request_id} [skip ci]", timeout=20)
                commit_sha = run_git(worktree, "rev-parse", "HEAD", timeout=20).stdout.strip()
                try:
                    run_git(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}", timeout=30)
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "WORKER_RETRY_COMPLETE_PUSH_FAILED"}
            finally:
                try:
                    store._git("worktree", "remove", "--force", str(worktree))
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)
            return {
                "ok": True,
                "completed": True,
                "request_id": request_id,
                "old_conversation_id": old_conversation_id,
                "new_conversation_id": new_conversation_id,
                "wake_id": wake_id,
                "generation": generation,
                "commit_sha": commit_sha,
            }

    return {"ok": False, "error": "WORKER_RETRY_COMPLETE_RETRY_EXHAUSTED"}


