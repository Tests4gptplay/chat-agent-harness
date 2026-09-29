#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from .topology import _run, utc_now
except ImportError:
    from topology import _run, utc_now


ROLES = {"planner", "helper"}


def _normalized_worktree_path(value: Any) -> str:
    return str(value).replace("\\", "/").rstrip("/").casefold()


def _worktree_registered(store: Any, worktree) -> bool:
    target = _normalized_worktree_path(worktree)
    listed = store._git("worktree", "list", "--porcelain").stdout
    for line in listed.splitlines():
        if line.startswith("worktree ") and _normalized_worktree_path(line[9:]) == target:
            return True
    return False


def _drop_exact_worktree(store: Any, worktree) -> None:
    """Remove only one CAH-owned transactional worktree and stale registration."""
    try:
        store._git("worktree", "remove", "--force", str(worktree))
    except subprocess.CalledProcessError:
        # A half-created or locked initializing worktree may have registry state
        # even after its directory vanished. Unlock/remove only this exact path.
        try:
            store._git("worktree", "unlock", str(worktree))
        except subprocess.CalledProcessError:
            pass
        try:
            store._git("worktree", "remove", "--force", str(worktree))
        except subprocess.CalledProcessError:
            pass
    store._git("worktree", "prune", "--expire", "now")
    if _worktree_registered(store, worktree):
        raise RuntimeError(f"TASK_CELL_WORKTREE_REGISTRATION_STALE: {worktree}")
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    if worktree.exists():
        raise RuntimeError(f"TASK_CELL_WORKTREE_CLEANUP_FAILED: {worktree}")


def _prepare_state_worktree(store: Any, worktree) -> None:
    """Create a detached transaction that materializes only state/**.

    Role completion mutates two tiny state files. A normal worktree checkout can
    reset/materialize the entire repository and exceed the bridge request lease.
    --no-checkout avoids that full-tree reset; sparse checkout then materializes
    only the canonical state subtree needed by this transaction.
    """
    _drop_exact_worktree(store, worktree)
    store._git(
        "worktree", "add", "--force", "--no-checkout", "--detach",
        str(worktree), "FETCH_HEAD",
    )
    _run(worktree, "sparse-checkout", "init", "--cone")
    _run(worktree, "sparse-checkout", "set", "state")
    # Apply the sparse patterns to the detached HEAD. This checkout is bounded to
    # state/** rather than resetting/materializing the whole repository.
    _run(worktree, "checkout", "--detach", "HEAD")


def _fields(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    request_id = str(req.get("request_id") or "").strip()
    task_id = str(req.get("task_id") or "").strip()
    role = str(req.get("role") or "").strip().lower()
    project_key = str(req.get("task_cell_project_key") or "").strip()
    conversation_id = str(req.get("conversation_id") or "").strip()
    conversation_url = str(req.get("conversation_url") or "").strip()
    challenge = str(req.get("challenge") or "").strip()
    epoch = int(req.get("control_epoch") or 0)
    response_started = bool(req.get("response_started"))
    if not project_id:
        raise ValueError("project_id required")
    store._safe_id(request_id, "request_id")
    store._safe_id(task_id, "task_id")
    store._safe_id(challenge, "challenge")
    if role not in ROLES:
        raise ValueError("invalid task cell role")
    if epoch < 1:
        raise ValueError("control_epoch required")
    if not project_key.startswith("g-p-"):
        raise ValueError("task_cell_project_key required")
    if not conversation_id or len(conversation_id) > 128:
        raise ValueError("conversation_id required")
    if conversation_url:
        if not conversation_url.startswith("https://chatgpt.com/") or conversation_id not in conversation_url:
            raise ValueError("conversation_url mismatch")
    if not response_started:
        raise ValueError("TASK_CELL_ROLE_RESPONSE_NOT_STARTED")
    return {
        "project_id": project_id,
        "request_id": request_id,
        "task_id": task_id,
        "role": role,
        "project_key": project_key,
        "conversation_id": conversation_id,
        "conversation_url": conversation_url,
        "challenge": challenge,
        "epoch": epoch,
    }


def _matching_control(state: dict[str, Any], x: dict[str, Any], kind: str) -> tuple[bool, str]:
    control = state.get("control_request") if isinstance(state, dict) else None
    if not isinstance(control, dict):
        return False, "CONTROL_REQUEST_MISSING"
    checks = {
        "request_id": x["request_id"],
        "kind": kind,
        "task_id": x["task_id"],
        "task_cell_project_key": x["project_key"],
        "role": x["role"],
        "challenge": x["challenge"],
    }
    for key, value in checks.items():
        if str(control.get(key) or "") != str(value):
            return False, f"CONTROL_{key.upper()}_MISMATCH"
    if int(control.get("control_epoch") or 0) != x["epoch"]:
        return False, "CONTROL_EPOCH_MISMATCH"
    return True, str(control.get("status") or "")


def complete_task_cell_role_prompt(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    x = _fields(store, {**req, "conversation_url": req.get("conversation_url") or f"https://chatgpt.com/c/{req.get('conversation_id') or ''}"})
    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                state = json.loads(store._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
            except Exception:
                return {"ok": False, "error": "GIT_CONTROL_STATE_FAILED"}
            current_control = state.get("control_request") if isinstance(state, dict) else None
            control_kind = str(current_control.get("kind") or "") if isinstance(current_control, dict) else ""
            if control_kind not in {"task_cell_role_prompt", "task_cell_worker_helper_prompt"}:
                return {"ok": False, "error": "CONTROL_KIND_MISMATCH"}
            matched, status = _matching_control(state, x, control_kind)
            if not matched:
                return {"ok": False, "error": status}
            if status == "DONE":
                return {"ok": True, "completed": False, "idle": "already_done", "request_id": x["request_id"]}
            if status != "PENDING":
                return {"ok": False, "error": f"CONTROL_STATUS_{status or 'UNKNOWN'}"}
            worktree = store.runtime / f"task-cell-role-prompt-{x['request_id']}-{attempt}"
            try:
                _prepare_state_worktree(store, worktree)
                _run(worktree, "config", "user.name", "gah-local-bridge")
                _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
                state_path = worktree / "state" / "chatgpt.json"
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current_control = current.get("control_request")
                if not isinstance(current_control, dict) or str(current_control.get("request_id") or "") != x["request_id"]:
                    return {"ok": False, "error": "CONTROL_CHANGED_DURING_ROLE_PROMPT"}
                required_output_ref = str(current_control.get("required_output_ref") or "").strip()
                semantic_timeout_seconds = int(current_control.get("semantic_output_timeout_seconds") or 300)
                semantic_deadline = (
                    datetime.now(timezone.utc) + timedelta(seconds=max(1, semantic_timeout_seconds))
                    if required_output_ref
                    else None
                )
                semantic_deadline_at = semantic_deadline.isoformat() if semantic_deadline is not None else None
                current["control_request"] = {
                    **current_control,
                    "status": "DONE",
                    "completed_at": utc_now(),
                    "result": {
                        "task_id": x["task_id"],
                        "control_epoch": x["epoch"],
                        "role": x["role"],
                        "task_cell_project_key": x["project_key"],
                        "conversation_id": x["conversation_id"],
                        "challenge": x["challenge"],
                        "response_started": True,
                        "transport_status": "RESPONSE_STARTED",
                        "semantic_output_state": "WAITING" if required_output_ref else "NOT_DECLARED",
                        "semantic_output_ref": required_output_ref or None,
                        "semantic_output_deadline_at": semantic_deadline_at,
                    },
                }

                # Planner liveness/rotation accounting is canonical and task-scoped.
                # A transport response-start is not semantic completion: when the
                # runtime declares required_output_ref we retain a durable deadline
                # until that artifact appears. Harness uses that deadline for
                # mechanical Planner liveness without Foreground polling.
                cell_rel = f"state/task_cells/{x['task_id']}.json"
                cell_path = worktree / cell_rel
                if x["role"] == "helper" and cell_path.exists():
                    cell = json.loads(cell_path.read_text(encoding="utf-8"))
                    cell.setdefault("roles", {})["helper"] = {
                        "conversation_id": x["conversation_id"], "request_id": x["request_id"],
                        "challenge": x["challenge"], "project_key": x["project_key"],
                    }
                    cell_path.write_text(json.dumps(cell, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if x["role"] == "planner" and cell_path.exists():
                    cell = json.loads(cell_path.read_text(encoding="utf-8"))
                    planner_control = cell.get("planner_control")
                    authority = planner_control.get("authority") if isinstance(planner_control, dict) else None
                    if isinstance(planner_control, dict) and isinstance(authority, dict):
                        if int(cell.get("control_epoch") or 0) != x["epoch"]:
                            return {"ok": False, "error": "PLANNER_PROMPT_EPOCH_MISMATCH"}
                        if str(authority.get("conversation_id") or "") != x["conversation_id"]:
                            return {"ok": False, "error": "PLANNER_PROMPT_STALE_CONVERSATION"}
                        runtime = planner_control.setdefault("runtime", {})
                        if not isinstance(runtime, dict):
                            return {"ok": False, "error": "PLANNER_RUNTIME_STATE_INVALID"}
                        runtime["prompt_count"] = int(runtime.get("prompt_count") or 0) + 1
                        runtime["last_response_started_at"] = utc_now()
                        if required_output_ref:
                            runtime_timeout_seconds = int(runtime.get("semantic_output_timeout_seconds") or 300)
                            if runtime_timeout_seconds != semantic_timeout_seconds:
                                semantic_deadline = datetime.now(timezone.utc) + timedelta(seconds=max(1, runtime_timeout_seconds))
                                semantic_deadline_at = semantic_deadline.isoformat()
                                current["control_request"]["result"]["semantic_output_deadline_at"] = semantic_deadline_at
                            runtime["pending_output"] = {
                                "kind": str(current_control.get("semantic_output_kind") or "PLANNER_REQUIRED_OUTPUT"),
                                "ref": required_output_ref,
                                "request_id": x["request_id"],
                                "conversation_id": x["conversation_id"],
                                "status": "WAITING",
                                "started_at": utc_now(),
                                "deadline_at": semantic_deadline_at,
                                "wake_prompt": str(current_control.get("prompt") or ""),
                            }
                        planner_control["runtime"] = runtime
                        cell["planner_control"] = planner_control
                        cell["updated_at"] = utc_now()
                        cell_path.write_text(json.dumps(cell, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                current["updated"] = utc_now()[:10]
                current["writeback_reason"] = (
                    f"Delivered exact Task Cell role prompt {x['request_id']} to {x['role']}; "
                    "transport observed response_started. Semantic output, when declared, is tracked independently."
                )
                state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                add_paths = ["state/chatgpt.json"]
                task_cell_rel = f"state/task_cells/{x['task_id']}.json"
                if (worktree / task_cell_rel).exists():
                    add_paths.append(task_cell_rel)
                _run(worktree, "add", *add_paths)
                _run(worktree, "commit", "-m", f"Complete Task Cell role prompt {x['request_id']} [skip ci]")
                commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "GIT_TASK_CELL_ROLE_PROMPT_PUSH_FAILED"}
                return {"ok": True, "completed": True, "request_id": x["request_id"], "commit_sha": commit_sha}
            finally:
                try:
                    _drop_exact_worktree(store, worktree)
                except Exception:
                    shutil.rmtree(worktree, ignore_errors=True)
    return {"ok": False, "error": "TASK_CELL_ROLE_PROMPT_RETRY_EXHAUSTED"}
