#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from .topology import _run, utc_now
    from .planner_control import (
        PlannerControlError,
        bind_planner_successor,
        claim_planner_events,
        complete_planner_turn,
        insert_planner_event,
        make_planner_event,
        migrate_flat_planner_binding,
        stage_planner_successor,
    )
    from .planner_memory import (
        PlannerMemoryError,
        checkpoint_planner_current,
        collapse_terminal_projection,
        make_planner_current,
        memory_blob_sha,
        planner_current_path,
        planner_generation_path,
        planner_handoff_path,
        prepare_planner_handoff,
        promote_planner_authority_with_memory,
        record_predecessor_retirement_result,
        record_successor_binding,
            seal_planner_generation,
        terminalize_planner_memory,
        validate_predecessor_retirement_eligibility,
    )
    from .task_cell_ledgers import (
        SLOT_COUNT,
        make_planner_turn_outcome,
        make_planner_turn_slot,
        planner_plan_note_path,
        planner_semantic_memory_path,
        planner_slot_has_write,
        planner_turn_is_committed,
        planner_turn_memory_entry_header,
        planner_turn_memory_entry_path,
        planner_turn_outcome_path,
        planner_turn_slot_path,
        render_child_reply_entry,
        worker_child_reply_path,
        worker_turn_reply_entry_header,
        worker_turn_reply_entry_path,
    )
except ImportError:
    from topology import _run, utc_now
    from planner_control import (
        PlannerControlError,
        bind_planner_successor,
        claim_planner_events,
        complete_planner_turn,
        insert_planner_event,
        make_planner_event,
        migrate_flat_planner_binding,
        stage_planner_successor,
    )
    from planner_memory import (
        PlannerMemoryError,
        checkpoint_planner_current,
        collapse_terminal_projection,
        make_planner_current,
        memory_blob_sha,
        planner_current_path,
        planner_generation_path,
        planner_handoff_path,
        prepare_planner_handoff,
        promote_planner_authority_with_memory,
        record_predecessor_retirement_result,
        record_successor_binding,
            seal_planner_generation,
        terminalize_planner_memory,
        validate_predecessor_retirement_eligibility,
    )
    from task_cell_ledgers import (
        SLOT_COUNT,
        make_planner_turn_outcome,
        make_planner_turn_slot,
        planner_plan_note_path,
        planner_semantic_memory_path,
        planner_slot_has_write,
        planner_turn_is_committed,
        planner_turn_memory_entry_header,
        planner_turn_memory_entry_path,
        planner_turn_outcome_path,
        planner_turn_slot_path,
        render_child_reply_entry,
        worker_child_reply_path,
        worker_turn_reply_entry_header,
        worker_turn_reply_entry_path,
    )


DEFAULT_MAX_PROMPTS_PER_GENERATION = 10
DEFAULT_MAX_GENERATION_AGE_SECONDS = 2 * 60 * 60
DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS = 8 * 60
WORKER_REVIEW_INTERVAL = 5
TASK_CELL_ROOT = "state/task_cells"
ROLE_OUTPUT_ROOT = "evidence"


class PlannerRuntimeError(ValueError):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


def _safe_task_id(value: Any) -> str:
    task_id = str(value or "").strip()
    if not task_id or len(task_id) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in task_id):
        raise PlannerRuntimeError("PLANNER_RUNTIME_TASK_ID_INVALID")
    return task_id


def _safe_id(value: Any, code: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 192:
        raise PlannerRuntimeError(code)
    return text


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso_after(seconds: int) -> str:
    return (_now_dt() + timedelta(seconds=max(1, int(seconds)))).isoformat()


def _stable_token(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:24]}"


def _task_cell_path(task_id: str) -> str:
    return f"{TASK_CELL_ROOT}/{_safe_task_id(task_id)}.json"


def _takeover_ref(task_id: str, handoff_id: str) -> str:
    return f"evidence/{task_id}/roles/planner/successor-takeover-{handoff_id}.json"





def _read_json(store: Any, ref: str, path: str) -> dict[str, Any]:
    try:
        value = json.loads(store._git("show", f"{ref}:{path}").stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PlannerRuntimeError("PLANNER_RUNTIME_READ_FAILED", path) from exc
    if not isinstance(value, dict):
        raise PlannerRuntimeError("PLANNER_RUNTIME_JSON_INVALID", path)
    return value


def _try_read_json(store: Any, ref: str, path: str) -> dict[str, Any] | None:
    try:
        value = json.loads(store._git("show", f"{ref}:{path}").stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _drop_worktree(store: Any, worktree: Path) -> None:
    try:
        store._git("worktree", "remove", "--force", str(worktree))
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
    try:
        store._git("worktree", "prune", "--expire", "now")
    except Exception:
        pass


def _prepare_worktree(store: Any, base_sha: str, name: str) -> Path:
    worktree = store.runtime / name
    _drop_worktree(store, worktree)
    store._git("worktree", "add", "--force", "--detach", str(worktree), base_sha)
    _run(worktree, "config", "user.name", "gah-local-bridge")
    _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
    return worktree


def _write_json(worktree: Path, rel: str, value: dict[str, Any]) -> None:
    path = worktree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(worktree: Path, rel: str, value: str) -> None:
    path = worktree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _append_text(worktree: Path, rel: str, value: str, *, header: str | None = None) -> None:
    path = worktree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() and header is not None:
        path.write_text(header, encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(value)
        if value and not value.endswith("\n"):
            handle.write("\n")


def _commit_push(store: Any, worktree: Path, paths: list[str], message: str) -> str:
    _run(worktree, "add", *paths)
    _run(worktree, "commit", "-m", message)
    commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
    _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
    return commit_sha


def planner_git_cli_fallback(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    epoch = int(req.get("control_epoch") or 0)
    generation = int(req.get("planner_generation") or 0)
    fence = str(req.get("planner_fence_token") or "")
    conversation_id = str(req.get("conversation_id") or "")
    observed_conversation_id = str(req.get("observed_conversation_id") or "")
    output_ref = str(req.get("output_ref") or "").replace("\\", "/").strip()
    artifact = req.get("artifact")
    if epoch < 1 or generation < 1 or len(fence) < 8 or not conversation_id:
        return {"ok": False, "error": "PLANNER_GIT_CLI_IDENTITY_INVALID"}
    if observed_conversation_id != conversation_id:
        return {"ok": False, "error": "PLANNER_GIT_CLI_SENDER_MISMATCH"}
    if not isinstance(artifact, dict):
        return {"ok": False, "error": "PLANNER_GIT_CLI_ARTIFACT_INVALID"}
    path = Path(output_ref)
    if (
        not output_ref.startswith(f"evidence/{task_id}/roles/planner/")
        or path.is_absolute()
        or ".." in path.parts
    ):
        return {"ok": False, "error": "PLANNER_GIT_CLI_SCOPE_MISMATCH"}

    with store.git_lock:
        try:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            cell = _read_json(store, base_sha, _task_cell_path(task_id))
        except (OSError, subprocess.SubprocessError, PlannerRuntimeError):
            return {"ok": False, "error": "PLANNER_GIT_CLI_CANONICAL_READ_FAILED"}

        if int(cell.get("control_epoch") or 0) != epoch:
            return {"ok": False, "error": "PLANNER_GIT_CLI_EPOCH_MISMATCH"}
        control = cell.get("planner_control")
        runtime = control.get("runtime") if isinstance(control, dict) else None
        pending = runtime.get("pending_output") if isinstance(runtime, dict) else None
        if not isinstance(pending, dict) or str(pending.get("status") or "") != "WAITING":
            return {"ok": False, "error": "PLANNER_GIT_CLI_OUTPUT_NOT_WAITING"}
        if str(pending.get("ref") or "") != output_ref:
            return {"ok": False, "error": "PLANNER_GIT_CLI_OUTPUT_REF_MISMATCH"}
        pending_conversation = str(pending.get("conversation_id") or "")
        if pending_conversation and pending_conversation != conversation_id:
            return {"ok": False, "error": "PLANNER_GIT_CLI_CONVERSATION_MISMATCH"}

        authority = control.get("authority") if isinstance(control, dict) else None
        authority_ok = isinstance(authority, dict) and (
            int(authority.get("planner_generation") or 0) == generation
            and str(authority.get("planner_fence_token") or "") == fence
            and str(authority.get("conversation_id") or "") == conversation_id
        )
        if not authority_ok:
            return {"ok": False, "error": "PLANNER_GIT_CLI_AUTHORITY_MISMATCH"}

        if _artifact_exists(store, base_sha, output_ref):
            try:
                existing = _read_json(store, base_sha, output_ref)
            except PlannerRuntimeError:
                return {"ok": False, "error": "PLANNER_GIT_CLI_OUTPUT_CONFLICT"}
            if existing == artifact:
                return {"ok": True, "duplicate": True, "output_ref": output_ref, "commit_sha": base_sha}
            return {"ok": False, "error": "PLANNER_GIT_CLI_OUTPUT_CONFLICT"}

        worktree = _prepare_worktree(store, base_sha, f"planner-git-cli-{task_id}")
        try:
            _write_json(worktree, output_ref, artifact)
            try:
                commit_sha = _commit_push(
                    store,
                    worktree,
                    [output_ref],
                    f"Planner durable output for {task_id} [skip ci]",
                )
            except subprocess.SubprocessError:
                return {"ok": False, "error": "PLANNER_GIT_CLI_PUSH_FAILED"}
        finally:
            _drop_worktree(store, worktree)
        return {"ok": True, "duplicate": False, "output_ref": output_ref, "commit_sha": commit_sha}


def _task_contract_ref(task_id: str) -> str:
    return f"tasks/{_safe_task_id(task_id)}.json"


def _canonical_break_glass_allowed(task: dict[str, Any]) -> bool:
    architecture = task.get("architecture") if isinstance(task, dict) else None
    exception = architecture.get("foreground_bootstrap_exception") if isinstance(architecture, dict) else None
    if not isinstance(exception, dict) or str(exception.get("mode") or "") != "BUILD_AND_REPAIR_BREAK_GLASS":
        return False
    status = str(task.get("status") or "").upper()
    if status in {"DONE", "COMPLETE", "COMPLETE_ACCEPTED", "CLEANUP_COMPLETE", "CANCELLED"}:
        return False
    if task.get("installed_live_acceptance_complete") is True or task.get("foreground_bootstrap_exception_retired") is True:
        return False
    applies = exception.get("applies_when")
    return isinstance(applies, list) and bool(applies)


def _role_binding(cell: dict[str, Any], role: str) -> dict[str, Any]:
    roles = cell.get("roles")
    record = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(record, dict):
        raise PlannerRuntimeError("PLANNER_RUNTIME_ROLE_BINDING_MISSING", role)
    return record


def _authority(cell: dict[str, Any]) -> dict[str, Any]:
    control = cell.get("planner_control")
    authority = control.get("authority") if isinstance(control, dict) else None
    if not isinstance(authority, dict):
        raise PlannerRuntimeError("PLANNER_RUNTIME_AUTHORITY_MISSING")
    return authority


def _runtime(control: dict[str, Any]) -> dict[str, Any]:
    runtime = control.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        raise PlannerRuntimeError("PLANNER_RUNTIME_STATE_INVALID")
    runtime.setdefault("foreground_runtime_dependency", False)
    runtime.setdefault("authority_mode", "PLANNER")
    runtime.setdefault("prompt_count", 0)
    runtime.setdefault("generation_started_at", utc_now())
    runtime.setdefault("last_response_started_at", None)
    runtime.setdefault("pending_output", None)
    runtime.setdefault("turn_result", None)
    runtime.setdefault("turn_close", None)
    runtime.setdefault("completed_semantic_turns", 0)
    runtime.setdefault("pending_role_requests", [])
    runtime.setdefault("browser_promotion_complete", False)
    runtime.setdefault("max_prompts_per_generation", DEFAULT_MAX_PROMPTS_PER_GENERATION)
    runtime.setdefault("max_generation_age_seconds", DEFAULT_MAX_GENERATION_AGE_SECONDS)
    runtime.setdefault("semantic_output_timeout_seconds", DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS)
    return runtime


def _pending_helper_request(runtime: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    for index, request in enumerate(list(runtime.get("pending_role_requests") or [])):
        if (
            isinstance(request, dict)
            and str(request.get("role") or "") == "helper"
            and str(request.get("state") or "") == "REQUEST_PENDING"
        ):
            return index, request
    return None


def _stage_pending_helper_request(
    state: dict[str, Any],
    cell: dict[str, Any],
    runtime: dict[str, Any],
    git_branch: str = "main",
) -> tuple[dict[str, Any], bool]:
    found = _pending_helper_request(runtime)
    if found is None:
        return runtime, False
    index, role_request = found
    result = copy.deepcopy(runtime)
    pending_requests = list(result.get("pending_role_requests") or [])
    role_kind = str(role_request.get("kind") or "helper_result")
    worker_helper = role_kind == "worker_helper_result"
    control_kind = "task_cell_worker_helper_prompt" if worker_helper else "task_cell_role_prompt"
    semantic_output_kind = "WORKER_HELPER_RESULT" if worker_helper else "HELPER_RESULT"
    helper_prompt = (
        _worker_helper_prompt(
            str(role_request.get("prompt") or ""),
            str(role_request.get("artifact_ref") or ""),
            git_branch,
        )
        if worker_helper
        else _planner_helper_prompt(
            str(role_request.get("prompt") or ""),
            str(role_request.get("artifact_ref") or ""),
            git_branch,
        )
    )
    _set_control_request(
        state,
        kind=control_kind,
        request_id=str(role_request.get("request_id") or ""),
        task_id=str(cell.get("task_id") or ""),
        epoch=int(cell.get("control_epoch") or 0),
        role="helper",
        challenge=str(role_request.get("challenge") or ""),
        project_key=str(cell.get("task_cell_project_key") or ""),
        prompt=helper_prompt,
        extra={
            "required_output_ref": str(role_request.get("artifact_ref") or ""),
            "planner_decision_ref": str(role_request.get("decision_ref") or ""),
            "semantic_output_kind": semantic_output_kind,
            "helper_source": "worker_watchdog" if worker_helper else "planner",
        },
    )
    pending_requests[index] = {
        **role_request,
        "state": "REQUESTED",
        "requested_at": utc_now(),
    }
    result["pending_role_requests"] = pending_requests
    pending_outputs = list(result.get("pending_role_outputs") or [])
    if not any(
        isinstance(item, dict)
        and str(item.get("request_id") or "") == str(role_request.get("request_id") or "")
        for item in pending_outputs
    ):
        pending_outputs.append({
            "role": "helper",
            "kind": role_kind,
            "request_id": str(role_request.get("request_id") or ""),
            "challenge": str(role_request.get("challenge") or ""),
            "artifact_ref": str(role_request.get("artifact_ref") or ""),
            "state": "WAITING",
            "decision_ref": str(role_request.get("decision_ref") or ""),
            "helper_source": "worker_watchdog" if worker_helper else "planner",
        })
    result["pending_role_outputs"] = pending_outputs
    return result, True


def stage_worker_watchdog_helper(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Stage one Worker-specific Helper incident after the 30-minute Playwright watchdog expires."""
    parent_task_id = _safe_task_id(req.get("parent_task_id"))
    child_task_id = _safe_task_id(req.get("child_task_id"))
    parent_control_epoch = int(req.get("parent_control_epoch") or 0)
    backend_cl = str(req.get("backend_cl") or "").strip()
    dispatch_id = _safe_id(req.get("dispatch_id"), "WORKER_WATCHDOG_DISPATCH_ID_INVALID")
    generation = int(req.get("dispatch_generation") or 0)
    fence_token = _safe_id(req.get("fence_token"), "WORKER_WATCHDOG_FENCE_INVALID")
    worker_ref = _safe_id(req.get("worker_ref"), "WORKER_WATCHDOG_WORKER_REF_INVALID")
    lane_id = _safe_id(req.get("lane_id"), "WORKER_WATCHDOG_LANE_INVALID")
    worker_project_key = _safe_id(req.get("worker_project_key"), "WORKER_WATCHDOG_PROJECT_INVALID")
    reason = str(req.get("reason") or "worker_watchdog_30m").strip()
    observed_at = str(req.get("observed_at") or utc_now()).strip()
    if parent_control_epoch < 1 or generation < 1 or not backend_cl:
        raise PlannerRuntimeError("WORKER_WATCHDOG_INCIDENT_INVALID")

    identity = {
        "parent_task_id": parent_task_id,
        "child_task_id": child_task_id,
        "backend_cl": backend_cl,
        "dispatch_id": dispatch_id,
        "dispatch_generation": generation,
        "fence_token": fence_token,
        "worker_ref": worker_ref,
        "reason": reason,
    }
    evidence_ref = (
        f"evidence/{parent_task_id}/recovery/"
        f"{_stable_token('worker-watchdog', identity)}.json"
    )
    helper_request_id = _stable_token("worker-helper", {
        "task_id": parent_task_id,
        "child_task_id": child_task_id,
        "incident_ref": evidence_ref,
    })
    helper_output_ref = f"evidence/{parent_task_id}/roles/helper/{helper_request_id}.json"
    cell_ref = _task_cell_path(parent_task_id)

    for attempt in range(2):
        with store.git_lock:
            try:
                store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
                base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
                cell = _read_json(store, base_sha, cell_ref)
                state = _read_json(store, base_sha, "state/chatgpt.json")
            except Exception:
                return {"ok": False, "error": "WORKER_WATCHDOG_HELPER_STATE_READ_FAILED"}

            if int(cell.get("control_epoch") or 0) != parent_control_epoch:
                return {"ok": False, "error": "WORKER_WATCHDOG_PARENT_EPOCH_MISMATCH"}

            status = str(cell.get("status") or "").upper()
            if status in {"DONE", "COMPLETE", "COMPLETE_ACCEPTED", "CLEANUP", "CLEANUP_COMPLETE", "ERROR", "CANCELLED", "STOPPED"}:
                return {"ok": False, "error": "WORKER_WATCHDOG_PARENT_TERMINAL"}

            child_backend = _try_read_json(store, base_sha, backend_cl)
            child_dispatch = (
                child_backend.get("dispatch")
                if isinstance(child_backend, dict) and isinstance(child_backend.get("dispatch"), dict)
                else {}
            )
            if not (
                isinstance(child_backend, dict)
                and str(child_backend.get("overall") or "") == "RUNNING"
                and str(child_dispatch.get("state") or "") == "RUNNING"
                and str(child_dispatch.get("dispatch_id") or "") == dispatch_id
                and int(child_dispatch.get("generation") or 0) == generation
                and str(child_dispatch.get("fence_token") or "") == fence_token
                and str(child_dispatch.get("acked_by_worker_ref") or "") == worker_ref
            ):
                return {
                    "ok": True,
                    "staged": False,
                    "idle": "worker_watchdog_target_not_running",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                }

            control = copy.deepcopy(cell.get("planner_control") or {})
            runtime = _runtime(control)
            owned = list(runtime.get("owned_children") or [])
            owned_match = next((
                item for item in owned
                if isinstance(item, dict)
                and str(item.get("child_task_id") or "") == child_task_id
                and str(item.get("backend_cl") or "") == backend_cl
                and str(item.get("dispatch_id") or "") == dispatch_id
                and int(item.get("dispatch_generation") or 0) == generation
                and str(item.get("fence_token") or "") == fence_token
                and str(item.get("event_state") or "") == "WAITING"
            ), None)
            if owned_match is None:
                return {"ok": False, "error": "WORKER_WATCHDOG_CHILD_OWNERSHIP_MISMATCH"}

            pending_requests = list(runtime.get("pending_role_requests") or [])
            existing = next((
                item for item in pending_requests
                if isinstance(item, dict)
                and str(item.get("request_id") or "") == helper_request_id
            ), None)
            if existing is not None:
                return {
                    "ok": True,
                    "staged": False,
                    "duplicate": True,
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "helper_request_id": helper_request_id,
                    "helper_output_ref": helper_output_ref,
                    "evidence_ref": evidence_ref,
                    "request_state": existing.get("state"),
                }

            previous_wait = copy.deepcopy(control.get("wait"))
            recovery = {
                "kind": "WORKER_WATCHDOG",
                "source_role": "worker_watchdog",
                "parent_task_id": parent_task_id,
                "parent_control_epoch": parent_control_epoch,
                "child_task_id": child_task_id,
                "backend_cl": backend_cl,
                "dispatch_id": dispatch_id,
                "dispatch_generation": generation,
                "fence_token": fence_token,
                "worker_ref": worker_ref,
                "lane_id": lane_id,
                "worker_project_key": worker_project_key,
                "reason": reason,
                "observed_at": observed_at,
                "previous_parent_wait": previous_wait,
            }
            prompt = (
                f"WORKER HELPER incident: the Playwright 30-minute Worker watchdog expired for Child {child_task_id}. "
                f"Inspect exact backend {backend_cl}, dispatch {dispatch_id} generation {generation}, "
                f"fence {fence_token}, Worker owner {worker_ref}, lane {lane_id}, and the bound Worker browser conversation. "
                f"Trigger reason: {reason}. This is Worker-watchdog recovery, not Planner-invoked Helper work. Establish the real page/runtime state and perform the smallest authorized recovery. "
                "Do not invent or directly create a replacement Worker generation, dispatch, fence, wake, Child, or new semantic direction. "
                "If an already-authorized Worker continuation exists and only mechanical delivery/reconciliation is stalled, restore that exact continuation. "
                "Otherwise return the incident result to the same authoritative Planner so Planner can decide new semantic strategy. "
                "Preserve exact task/epoch/dispatch ownership and any useful Worker frontier/evidence."
            )
            pending_requests.append({
                "role": "helper",
                "kind": "worker_helper_result",
                "request_id": helper_request_id,
                "challenge": _stable_token(
                    "helper-challenge",
                    {"incident": evidence_ref, "task_id": parent_task_id, "control_epoch": parent_control_epoch},
                ),
                "prompt": prompt,
                "artifact_ref": helper_output_ref,
                "state": "REQUEST_PENDING",
                "decision_ref": evidence_ref,
                "recovery": recovery,
            })
            runtime["pending_role_requests"] = pending_requests
            control["wait"] = {
                "kind": "WAIT_WORKER_HELPER",
                "selector": {"output_ref": helper_output_ref},
                "refs": [evidence_ref, backend_cl],
            }
            control["activity"] = "PARKED_WAIT_EVENT"
            control["runtime"] = runtime
            new_cell = copy.deepcopy(cell)
            new_cell["planner_control"] = control
            new_cell["updated_at"] = utc_now()

            helper_delivery_staged = False
            if _control_slot_available(state):
                staged_runtime, helper_delivery_staged = _stage_pending_helper_request(
                    state, new_cell, runtime, store.git_branch
                )
                control["runtime"] = staged_runtime
                new_cell["planner_control"] = control

            evidence = {
                "v": 1,
                "code": "WORKER_WATCHDOG_HELPER_ESCALATION",
                "source_role": "worker_watchdog",
                "task_id": parent_task_id,
                "control_epoch": parent_control_epoch,
                "child_task_id": child_task_id,
                "observed_at": observed_at,
                "source_commit": base_sha,
                "recovery": recovery,
                "helper_request_id": helper_request_id,
                "helper_output_ref": helper_output_ref,
                "helper_delivery_staged": helper_delivery_staged,
            }

            worktree = _prepare_worktree(
                store, base_sha, f"worker-watchdog-helper-{child_task_id}-{generation}-{attempt}"
            )
            try:
                _write_json(worktree, evidence_ref, evidence)
                _write_json(worktree, cell_ref, new_cell)
                _write_json(worktree, "state/chatgpt.json", state)
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        [evidence_ref, cell_ref, "state/chatgpt.json"],
                        f"Wake WORKER_HELPER for Worker watchdog {child_task_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "WORKER_WATCHDOG_HELPER_PUSH_FAILED"}
            finally:
                _drop_worktree(store, worktree)

            return {
                "ok": True,
                "staged": True,
                "duplicate": False,
                "parent_task_id": parent_task_id,
                "child_task_id": child_task_id,
                "helper_request_id": helper_request_id,
                "helper_output_ref": helper_output_ref,
                "evidence_ref": evidence_ref,
                "helper_delivery_staged": helper_delivery_staged,
                "commit_sha": commit_sha,
            }

    return {"ok": False, "error": "WORKER_WATCHDOG_HELPER_RETRY_EXHAUSTED"}


def _control_slot_available(state: dict[str, Any]) -> bool:
    control = state.get("control_request")
    if not isinstance(control, dict):
        return True
    return str(control.get("status") or "") not in {"PENDING", "APPLYING"}


def _blob_sha(store: Any, ref: str, path: str) -> str:
    try:
        return store._git("rev-parse", f"{ref}:{path}").stdout.strip()
    except subprocess.SubprocessError as exc:
        raise PlannerRuntimeError("PLANNER_RUNTIME_BLOB_MISSING", path) from exc


def _planner_helper_identity(task_id: str, decision_ref: str) -> tuple[str, str]:
    request_id = _stable_token("planner-helper", {
        "task_id": task_id,
        "decision_ref": decision_ref,
    })
    return request_id, f"evidence/{task_id}/roles/helper/{request_id}.json"


def _planner_helper_prompt(semantic_prompt: str, output_ref: str, git_branch: str = "main") -> str:
    return (
        f"Act as HELPER. Use GitHub connector CAH_OWNER/CAH_OPERATIONAL_REPOSITORY branch {git_branch} for ALL reads/writes. "
        "ACTIVE RECOVERY REQUIRED: you are the bounded operational recovery owner, not an observer or advisor. "
        "This wake assigns executable incident work. Diagnosis is preparation for action, not completion. "
        "PLANNER AUTHORITY: within this managed Task Cell, Planner is the highest autonomous task-level semantic authority below Foreground/user intent. If this incident wake contains an explicit bounded Planner operational instruction, you MUST execute it after verifying exact target/ownership/safety; do not treat yourself as a second approval layer and do not refuse merely because you personally judge the target healthy or cleanup unnecessary. If the requested end state already exists, reconcile any remaining canonical state so Planner's intended lifecycle becomes true. Only user/Foreground intent, Task Contract, ownership isolation or safety conflict can block that instruction. "
        "TWO MANDATORY MISSIONS: (1) restore the affected Harness/Task Cell/Runner lifecycle to its normal autonomous self-running state, with no stale waits/events/control/output/continuation pointers that would require later manual pushes; and (2) move the current task past this operational fault boundary to the next valid lifecycle point so the next semantic role is mechanically runnable/wakeable from the preserved frontier. "
        "If manual canonical repair is needed, make it closure-complete: update every materially coupled wait, event, control request, pending role output/request, Child continuation/ownership, dispatch/result/wait reference and resource ownership affected by the transition, while preserving task/epoch authority and machine-owned identities. Verify that the next Harness reconciliation can proceed without another manual correction. "
        f"Incident: {semantic_prompt.strip()} "
        "Read docs/task-cell/README.md, docs/task-cell/HELPER.md and the supplied incident evidence. "
        "Establish the fault boundary and exact ownership/safety facts needed to act. Once the stated incident is independently confirmed and ownership is sufficient, STOP repeated diagnosis and switch to mutation. "
        "Do not spend the incident proving an already-established fault twice; further read-only inspection must answer a specific unresolved ownership, safety, or verification question. "
        "Identify the exact operational effect that should already have happened, execute the smallest authorized action that can produce it, and verify the real resulting state. "
        "If a preferred connector/API/Tool is unavailable or fails, that alone is NOT a blocker: switch to another existing authorized surface that can safely produce the same bounded effect. "
        "Use the practical fallback ladder: existing lifecycle action -> alternate authorized browser/GitHub action -> exact task-owned host/Runner operation -> smallest canonical Harness/runtime correction -> concrete blocker. "
        "Do not broaden scope or invent new semantic strategy while following that ladder. Planner owns new semantic strategy; Helper owns restoring the existing strategy to a runnable state. "
        "Do not interrupt or resend while an AI is still genuinely generating; preserve explicit task pause/cancel. "
        "Helper MUST NOT request exit while an authorized recovery mutation remains unattempted, while coupled canonical pointers remain stale, or before the correct next semantic role has actually started. "
        f"TWO-PHASE HELPER RESULT: after recovery is materially complete, first write exactly {output_ref} with non-empty diagnosis and repair_result but OMIT turn_signal. This is the durable result-ready handoff payload, not Helper completion. Harness may route this payload while this Helper remains alive. "
        "For a missed Planner/Worker input, restore the same retained control request/event to its runnable state so Harness re-delivers it mechanically; do not rewrite the prompt or fabricate replacement identity. "
        "RETURN CONTROL: if a valid pre-existing Worker continuation already exists and only needs mechanical restoration, restore that exact continuation; otherwise, especially when Planner invoked this Helper or semantic review is required, route the result back to the same authoritative Planner. Never invent new Worker semantic direction, dispatch/generation/fence, Child, or replacement Planner merely to force progress. "
        "Planner wake verification requires the helper_result event to be claimed by a Planner doorbell, state/chatgpt control_request role=planner for the same task/epoch, a real Harness-generated 'GAH_WAKE v=1 id=<planner-request-id> project=<task-cell-project-id>', and response_started=true. "
        "Worker wake verification requires the existing legal canonical continuation, a real Harness-generated 'GAH_WAKE v=1 id=<wake-id> project=<worker-project-id>' plus matching 'GAH_DISPATCH task_id=<child-task-id> backend_cl=<backend-cl> dispatch_id=<dispatch-id> generation=<n> fence_token=<fence-token>', then normal ACK/RUNNING admission. Do NOT fake these markers. "
        "ONLY AFTER the correct next semantic role has actually started, update the SAME bound Helper result, preserving diagnosis and repair_result and adding turn_signal=done. That update must be this Helper incident's FINAL Git write. "
        "Durable turn_signal=done means only 'this single-use Helper has finished and may be removed'; it is NOT a chat ending and not a Planner/Worker routing instruction. "
        "After that final Git write, emit exactly one final syscall block and stop: "
        "GAH_SYSCALL_BEGIN\n{\"v\":1,\"kind\":\"semantic_sync\",\"call_id\":\"semantic-sync-001\"}\nGAH_SYSCALL_END. "
        "Do not append the legacy one-word ending after the block. Do not start another incident, become standby, or delete your own chat. "
        "On accepted Helper semantic_sync, Harness validates the exact binding/output, records done_observed_at, immediately exact-deletes this Helper conversation, and clears its Helper binding only after deletion succeeds. Harness does NOT wait for Helper response-end. "
        "This is a single-use Helper conversation. Harness owns identity, routing, and Helper cleanup."
    )


def _worker_helper_prompt(semantic_prompt: str, output_ref: str, git_branch: str = "main") -> str:
    base = _planner_helper_prompt(semantic_prompt, output_ref, git_branch)
    base = base.replace(
        "Planner wake verification requires the helper_result event",
        "If new semantic strategy is required, Planner wake verification requires the worker_helper_result event",
    )
    return (
        "WORKER_HELPER SOURCE=PLAYWRIGHT_WATCHDOG. "
        "This Helper was mechanically invoked for one exact Worker after its 30-minute watchdog deadline; "
        "it is NOT a Planner-requested Helper turn and the timeout alone is not proof of a semantic fault. "
        "WORKER_HELPER RESULT ROUTING: when writing the phase-1 diagnosis + repair_result payload, also write return_target='worker' when the same-generation Worker path has been restored/kept, or return_target='planner' only when new semantic strategy/review is required. "
        "WORKER_HELPER RECOVERY ORDER: (1) inspect the exact Worker page, canonical Child/backend state, Runner/external work and durable progress; "
        "(2) if a concrete operational fault is found and is safely repairable, repair it and push the SAME Worker forward; "
        "(3) if no concrete repairable fault is found and the exact Worker remains abnormally unresolved, exact-delete that physical Worker conversation through the authorized Playwright/host path, then append a HELPER INCIDENT breadcrumb to the Worker Child Reply stating that the generation was killed after the 30-minute watchdog and will be retried; "
        "(4) restore/re-deliver the ORIGINAL SAME-GENERATION wake/binding through Harness and verify the replacement physical Worker response-start/admission. "
        "Do not synthesize G+1, a new dispatch, a new fence, a new wake identity, or new Worker semantics during this watchdog recovery. "
        "Only if same-generation operational recovery is not legal or new semantic strategy is actually required should control return to the authoritative Planner. "
        "Keep the Worker/Child/dispatch identity explicit in all recovery decisions. "
        + base
    )


def _enabled_worker_lanes(store: Any, base_sha: str) -> list[dict[str, str]]:
    lanes_state = _read_json(store, base_sha, "state/lanes.json")
    lanes = []
    for lane in lanes_state.get("lanes") or []:
        if not isinstance(lane, dict) or lane.get("enabled") is not True:
            continue
        lane_id = str(lane.get("lane_id") or "").strip()
        project_key = str(lane.get("project_key") or "").strip()
        if lane_id.startswith("lane-") and project_key.startswith("g-p-"):
            lanes.append({"lane_id": lane_id, "project_key": project_key})
    if not lanes:
        raise PlannerRuntimeError("PLANNER_CHILD_LANE_UNKNOWN")
    return lanes


def _planner_turn_prompt(
    *,
    task_id: str,
    doorbell: dict[str, Any],
    control: dict[str, Any],
    plan_ref: str,
    memory_ref: str,
    memory_entry_ref: str,
    slots: list[dict[str, Any]],
    outcome_ref: str,
    git_branch: str = "main",
) -> str:
    event_ids = [str(x) for x in doorbell.get("event_ids") or []]
    refs: list[str] = []
    event_view: list[dict[str, Any]] = []
    inbox = control.get("inbox") if isinstance(control, dict) else None
    events = inbox.get("events") if isinstance(inbox, dict) else None
    if isinstance(events, dict):
        for event_id in event_ids:
            event = events.get(event_id)
            if isinstance(event, dict):
                event_refs = [str(x) for x in event.get("refs") or [] if str(x)]
                refs.extend(event_refs)
                event_view.append({
                    "event_id": event_id,
                    "kind": str(event.get("kind") or ""),
                    "source_role": str(event.get("source_role") or ""),
                    "source_identity": copy.deepcopy(event.get("source_identity") or {}),
                    "refs": event_refs,
                })
    plan_note_ref = planner_plan_note_path(task_id)
    slot_view = [
        {
            "lane_id": row.get("lane_id"),
            "slot_ref": row["slot_ref"],
            "bound_child_task_id": row.get("bound_child_task_id"),
            "bound_child_reply_ref": row.get("bound_child_reply_ref"),
        }
        for row in slots
    ]
    return (
        f"Act as PLANNER for managed task {task_id}. Use connected GitHub/CAH access to "
        f"CAH_OWNER/CAH_OPERATIONAL_REPOSITORY branch {git_branch} for ALL reads/writes; never default to another ref. Read AGENTS.md and docs/task-cell/PLANNER.md. "
        f"Foreground requirements: tasks/{task_id}.json (also open any original requirements/material refs it names). Read these first, then {plan_ref}, {memory_ref}, and {plan_note_ref}; Memory and Plan Note do not replace the goal. "
        f"Use {plan_note_ref} freely as Planner's mutable decomposition/backlog workspace and load-shedding surface. A substantial parent task must not force you to fully decompose or reason through too much future work in one response: over-concentrating planning into one turn can cause an excessively long response, semantic/context collapse, response timeout, or a stalled Task Cell. When you judge that continuing decomposition in the current turn creates a meaningful risk of those failure modes, proactively use Plan Note instead of trying to finish the whole decomposition in that response. Stop at a useful frontier, preserve the remaining backlog in Plan Note, dispatch already-bounded work, and resume the remaining decomposition on later review turns. Treat this as an active recommendation: use Plan Note early enough to avoid an oversized risky turn, not merely as recovery after the turn has already become unwieldy. You may stage a coarse whole-task outline or partially decompose it. Do not impose a fixed syntax, tree depth, batch size, Child count, elapsed-time threshold, or token threshold. Inspect its remaining frontier on planning/review turns so deferred work is not forgotten, and distinguish remaining work from work already dispatched, accepted, superseded, or no longer needed. "
        f"If this turn actually consults Plan Note for planning/review or changes it, then in the same turn record that use in {memory_entry_ref}: capture the meaningful note delta or decision and the remaining frontier for successor continuity. If changing Plan Note, write {plan_note_ref} before the Memory entry. Do not copy the whole note into Memory. Plan Note never overrides Task Contract, formal Plan, Child Reply/Result evidence, or accepted artifacts. "
        f"Current events={json.dumps(event_view, ensure_ascii=False)}. "
        f"Read relevant new refs={json.dumps(list(dict.fromkeys(refs)))}. "
        f"Append this turn's task-level reasoning after the existing header in {memory_entry_ref}. "
        f"If the formal future strategy changes, update {plan_ref}. "
        f"All available lane work positions are pre-bound by Harness: {json.dumps(slot_view, ensure_ascii=False)}. "
        "For a bound Child, read its Child Reply and, only if needed, set that slot's entry_type and semantic fields "
        "to REVIEW, REVIEW_DIRECTION, DIRECTION, ACCEPT, REJECT, or RETIRE. "
        "For an empty slot, use DIRECTION with the complete semantic child work definition when a new Child is needed. "
        "Leave unused slot files unchanged. Child semantic content may include capability_hints from skills/index.json, "
        "harness/tools/registry.json, or existing workflow/toolbox lists; those hints are suggestions, not restrictions. "
        f"Write {outcome_ref} LAST, preserving its pre-bound machine fields. Put any needed semantic payload there "
        "(final_result_ref for final acceptance, wait for an external dependency, question for user input, or error details) "
        "and set turn_signal to exactly one of done, rework, complete, handoff, wait, need_user, or error. "
        "Do not fill the Harness-owned internal outcome field. After that durable write succeeds, actively kick local Harness sync "
        "by emitting exactly one final syscall block and stop: "
        "GAH_SYSCALL_BEGIN\n{\"v\":1,\"kind\":\"semantic_sync\",\"call_id\":\"semantic-sync-001\"}\nGAH_SYSCALL_END. "
        "Do not append the legacy one-word ending after the block. Do not repeat work summaries, file lists or metadata in chat. "
        "Harness derives routing and counts; the same lane-write detection applies to both initial work and adding Workers later."
    )


def _lane_turn_surfaces(store: Any, base_sha: str, *, task_id: str, epoch: int,
                        doorbell_id: str, generation: int, fence: str,
                        owned: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One machine-bound writable position per registered lane, on every turn."""
    by_lane = {row.get("lane_id"): row for row in owned if isinstance(row, dict)}
    rows, bindings = [], []
    for index, lane in enumerate(_enabled_worker_lanes(store, base_sha), 1):
        child = by_lane.get(lane["lane_id"]) or {}
        row = make_planner_turn_slot(
            task_id=task_id, control_epoch=epoch, doorbell_id=doorbell_id,
            planner_generation=generation, planner_fence_token=fence, slot_index=index,
            bound_child_task_id=child.get("child_task_id"),
            bound_child_reply_ref=child.get("child_reply_ref"),
        )
        row.update(lane)
        rows.append(row)
        bindings.append({key: row.get(key) for key in (
            "slot_index", "slot_ref", "bound_child_task_id", "bound_child_reply_ref", "lane_id", "project_key",
        )})
    return rows, bindings


def _consume_planner_turn(
    store: Any,
    *,
    base_sha: str,
    cell_ref: str,
    cell: dict[str, Any],
    state: dict[str, Any],
    project_id: str,
    repo: str,
) -> dict[str, Any] | None:
    control = cell.get("planner_control")
    if not isinstance(control, dict):
        return None
    runtime = _runtime(control)
    active_turn = runtime.get("active_turn")
    if not isinstance(active_turn, dict):
        return None
    outcome_ref = str(active_turn.get("outcome_ref") or "")
    if not outcome_ref or not _artifact_exists(store, base_sha, outcome_ref):
        return None
    outcome = _read_json(store, base_sha, outcome_ref)
    if not planner_turn_is_committed(outcome):
        return None

    task_id = str(cell.get("task_id") or "")
    outcome_kind = str(outcome.get("outcome") or "")
    semantic = outcome.get("semantic") if isinstance(outcome.get("semantic"), dict) else {}
    if outcome_kind == "COMPLETE" and any(
        item.get("state") == "WAITING" and not item.get("done_observed_at")
        for item in runtime.get("pending_role_outputs", []) if isinstance(item, dict)
    ):
        # A recovering Planner may finish before Helper writes its incident result.
        # Do not let terminal cleanup delete that still-working Helper.
        return {"ok": True, "idle": "helper_result_pending", "task_id": task_id}

    if outcome_kind == "WAIT":
        helper_prompt = str(semantic.get("helper_prompt") or "").strip()
        wait = semantic.get("wait")
        wait_ok = (
            isinstance(wait, dict)
            and bool(str(wait.get("kind") or "").strip())
            and wait.get("selector") not in (None, "", {})
        )
        if not helper_prompt and not wait_ok:
            return {
                "ok": False,
                "error": "PLANNER_WAIT_CONDITION_REQUIRED",
                "task_id": task_id,
            }

    if outcome_kind == "NEED_USER" and not str(semantic.get("question") or "").strip():
        return {
            "ok": False,
            "error": "PLANNER_NEED_USER_QUESTION_REQUIRED",
            "task_id": task_id,
        }

    doorbell_id = str(active_turn.get("doorbell_id") or "")
    memory_entry_ref = str(active_turn.get("memory_entry_ref") or "")
    memory_entry = store._git("show", f"{base_sha}:{memory_entry_ref}").stdout
    slot_bindings = list(active_turn.get("slots") or [])
    slots = []
    for binding in slot_bindings:
        if not isinstance(binding, dict):
            continue
        slot_ref = str(binding.get("slot_ref") or "")
        slot = _try_read_json(store, base_sha, slot_ref)
        if isinstance(slot, dict):
            slots.append((binding, slot))

    enabled_lanes: list[dict[str, str]] | None = None
    if outcome_kind == "CONTINUE":
        dispatch_slots = [
            (binding, slot)
            for binding, slot in slots
            if planner_slot_has_write(slot)
            and str(slot.get("entry_type") or "") in {"DIRECTION", "REVIEW_DIRECTION", "REJECT"}
        ]
        if dispatch_slots:
            try:
                enabled_lanes = _enabled_worker_lanes(store, base_sha)
            except PlannerRuntimeError as exc:
                if exc.code == "PLANNER_CHILD_LANE_UNKNOWN":
                    return {
                        "ok": True,
                        "idle": "worker_lane_unavailable",
                        "task_id": task_id,
                        "outcome_ref": outcome_ref,
                    }
                raise
            enabled_lane_ids = {str(row.get("lane_id") or "") for row in enabled_lanes}
            owned_now = list(runtime.get("owned_children") or [])
            for binding, _slot in dispatch_slots:
                bound_child_task_id = str(binding.get("bound_child_task_id") or "")
                if not bound_child_task_id:
                    continue
                child = next(
                    (
                        row for row in owned_now
                        if isinstance(row, dict)
                        and str(row.get("child_task_id") or "") == bound_child_task_id
                    ),
                    None,
                )
                if not isinstance(child, dict):
                    return {
                        "ok": False,
                        "error": "PLANNER_CHILD_BINDING_MISSING",
                        "task_id": task_id,
                    }
                if str(child.get("lane_id") or "") not in enabled_lane_ids:
                    return {
                        "ok": True,
                        "idle": "worker_lane_unavailable",
                        "task_id": task_id,
                        "child_task_id": bound_child_task_id,
                        "outcome_ref": outcome_ref,
                    }

    try:
        new_control = complete_planner_turn(
            control,
            doorbell_id=doorbell_id,
            consumed_at=utc_now(),
        )
    except PlannerControlError as exc:
        return {"ok": False, "error": exc.code, "task_id": task_id}

    rt = _runtime(new_control)
    rt["pending_output"] = None
    rt["active_turn"] = None
    rt["turn_result"] = None
    rt["turn_close"] = None
    rt["completed_semantic_turns"] = int(rt.get("completed_semantic_turns") or 0) + 1
    new_control["last_decision"] = {"decision_ref": outcome_ref, "outcome": outcome_kind}

    write_objects: dict[str, dict[str, Any]] = {}
    write_texts: dict[str, str] = {}
    append_texts: dict[str, str] = {}
    owned = list(rt.get("owned_children") or [])
    settled_count = len(slot_bindings)
    dispatched_count = 0
    for binding, slot in slots:
        if not planner_slot_has_write(slot):
            continue
        slot_index = int(binding.get("slot_index") or 0)
        entry_type = str(slot.get("entry_type") or "")
        slot_semantic = copy.deepcopy(slot.get("semantic"))
        bound_child_task_id = str(binding.get("bound_child_task_id") or "")
        entry_id = _stable_token(
            "planner-child-entry",
            {"task": task_id, "doorbell": doorbell_id, "slot": slot_index, "type": entry_type},
        )

        if bound_child_task_id:
            reply_ref = str(binding.get("bound_child_reply_ref") or worker_child_reply_path(bound_child_task_id))
            append_texts[reply_ref] = append_texts.get(reply_ref, "") + render_child_reply_entry(
                entry_id=entry_id,
                writer_role="planner",
                entry_type=entry_type,
                semantic=slot_semantic,
            )
            child_index = next(
                (
                    i for i, row in enumerate(owned)
                    if isinstance(row, dict)
                    and str(row.get("child_task_id") or "") == bound_child_task_id
                ),
                None,
            )
            if (
                outcome_kind == "CONTINUE"
                and entry_type in {"DIRECTION", "REVIEW_DIRECTION", "REJECT"}
                and child_index is not None
            ):
                continuation = _continue_worker_child(
                    store,
                    base_sha,
                    parent_task_id=task_id,
                    parent_control_epoch=int(cell.get("control_epoch") or 0),
                    child=owned[child_index],
                    direction_id=entry_id,
                    project_id=project_id,
                    repo=repo,
                )
                write_objects[continuation["task_ref"]] = continuation["task"]
                write_objects[continuation["backend_cl"]] = continuation["cl"]
                write_objects[continuation["wake_ref"]] = continuation["wake"]
                write_objects[continuation["task"]["expected_result_ref"]] = {**continuation["task"]["result_contract"], "summary": ""}
                write_texts[continuation["worker_reply_entry_ref"]] = continuation["worker_reply_entry_initial"]
                owned[child_index] = {
                    **owned[child_index],
                    "dispatch_id": continuation["dispatch"]["dispatch_id"],
                    "dispatch_generation": continuation["dispatch"]["generation"],
                    "fence_token": continuation["dispatch"]["fence_token"],
                    "worker_reply_entry_ref": continuation["worker_reply_entry_ref"],
                    "event_state": "WAITING",
                    "round_doorbell_id": doorbell_id,
                }
                settled_count -= 1
                dispatched_count += 1
            elif entry_type in {"ACCEPT", "RETIRE"}:
                owned = [
                    row for row in owned
                    if not isinstance(row, dict)
                    or str(row.get("child_task_id") or "") != bound_child_task_id
                ]
            continue

        if outcome_kind != "CONTINUE" or entry_type != "DIRECTION":
            continue
        if enabled_lanes is None:
            enabled_lanes = _enabled_worker_lanes(store, base_sha)
        lane = next((lane for lane in enabled_lanes if lane["lane_id"] == binding.get("lane_id")), None)
        if lane is None:
            return {"ok": False, "error": "PLANNER_CHILD_LANE_BINDING_MISSING", "task_id": task_id}
        child_task_id = _stable_token(
            "child",
            {"parent": task_id, "doorbell": doorbell_id, "slot": slot_index},
        )
        task_payload = slot_semantic if isinstance(slot_semantic, dict) else {"goal": str(slot_semantic)}
        child = _build_worker_child(
            parent_task_id=task_id,
            parent_control_epoch=int(cell.get("control_epoch") or 0),
            decision_ref=outcome_ref,
            action={
                "kind": "DISPATCH_WORKER_CHILD",
                "child_task_id": child_task_id,
                "lane_id": lane["lane_id"],
                "worker_project_key": lane["project_key"],
                "task_payload": task_payload,
                "wait": {"kind": "WAIT_ADMISSION", "selector": {"child_task_id": child_task_id}},
            },
            project_id=project_id,
            repo=repo,
        )
        write_objects[child["task_ref"]] = child["task"]
        write_objects[child["backend_cl"]] = child["cl"]
        write_objects[child["wake_ref"]] = child["wake"]
        write_objects[child["task"]["expected_result_ref"]] = {**child["task"]["result_contract"], "summary": ""}
        write_texts[child["child_reply_ref"]] = child["child_reply_initial"]
        write_texts[child["worker_reply_entry_ref"]] = child["worker_reply_entry_initial"]
        owned.append({
            "child_task_id": child["child_task_id"],
            "task_ref": child["task_ref"],
            "backend_cl": child["backend_cl"],
            "lane_id": child["lane_id"],
            "worker_project_key": child["worker_project_key"],
            "owner_task_id": child["owner_task_id"],
            "owner_control_epoch": child["owner_control_epoch"],
            "dispatch_id": child["dispatch"]["dispatch_id"],
            "dispatch_generation": child["dispatch"]["generation"],
            "fence_token": child["dispatch"]["fence_token"],
            "child_reply_ref": child["child_reply_ref"],
            "worker_reply_entry_ref": child["worker_reply_entry_ref"],
            "slot_index": slot_index,
            "round_doorbell_id": doorbell_id,
            "event_state": "WAITING",
        })
        settled_count -= 1
        dispatched_count += 1

    # A checkpoint approval releases an existing continuation even if Planner
    # leaves its Task/DIRECTION unchanged. It is not detected by a text hash.
    if outcome_kind == "CONTINUE":
        for index, child in enumerate(owned):
            if not isinstance(child, dict) or child.get("event_state") != "REVIEW_PENDING":
                continue
            continuation = _continue_worker_child(
                store, base_sha, parent_task_id=task_id,
                parent_control_epoch=int(cell["control_epoch"]), child=child,
                direction_id=doorbell_id, project_id=project_id, repo=repo,
            )
            for key in ("task_ref", "backend_cl", "wake_ref"):
                write_objects[continuation[key]] = continuation[{"task_ref": "task", "backend_cl": "cl", "wake_ref": "wake"}[key]]
            write_objects[continuation["task"]["expected_result_ref"]] = {**continuation["task"]["result_contract"], "summary": ""}
            write_texts[continuation["worker_reply_entry_ref"]] = continuation["worker_reply_entry_initial"]
            owned[index] = {**child, "event_state": "WAITING", "dispatch_id": continuation["dispatch"]["dispatch_id"],
                            "dispatch_generation": continuation["dispatch"]["generation"],
                            "fence_token": continuation["dispatch"]["fence_token"],
                            "worker_reply_entry_ref": continuation["worker_reply_entry_ref"]}
            dispatched_count += 1
    rt["owned_children"] = owned
    rt["worker_round"] = None
    new_control["runtime"] = rt

    if outcome_kind == "CONTINUE":
        if any(isinstance(child, dict) and child.get("event_state") == "WAITING" for child in owned):
            new_control["activity"] = "PARKED_WAIT_EVENT"
            new_control["wait"] = {
                "kind": "WAIT_RESULT",
                "selector": {"task_id": task_id},
                "refs": [],
            }
            new_control["runtime"] = rt
        else:
            new_control["activity"] = "PARKED_WAIT_EVENT"
            new_control["wait"] = None
            new_control["runtime"] = rt
    elif outcome_kind == "WAIT":
        helper_prompt = str(semantic.get("helper_prompt") or "").strip()
        if helper_prompt:
            helper_request_id, helper_output_ref = _planner_helper_identity(task_id, outcome_ref)
            helper_challenge = _stable_token(
                "planner-helper-challenge",
                {"task_id": task_id, "control_epoch": int(cell.get("control_epoch") or 0)},
            )
            pending_requests = list(rt.get("pending_role_requests") or [])
            pending_requests.append({
                "role": "helper",
                "kind": "helper_result",
                "request_id": helper_request_id,
                "challenge": helper_challenge,
                "prompt": helper_prompt,
                "artifact_ref": helper_output_ref,
                "state": "REQUEST_PENDING",
                "decision_ref": outcome_ref,
            })
            rt["pending_role_requests"] = pending_requests
            new_control["wait"] = {
                "kind": "WAIT_HELPER",
                "selector": {"output_ref": helper_output_ref},
                "refs": [],
            }
        else:
            new_control["wait"] = copy.deepcopy(semantic["wait"])
        new_control["activity"] = "PARKED_WAIT_EVENT"
        new_control["runtime"] = rt
    elif outcome_kind == "NEED_USER":
        new_control["wait"] = {
            "kind": "NEED_USER",
            "selector": {"event_kind": "foreground_intent"},
            "refs": [outcome_ref],
        }
        new_control["activity"] = "WAIT_USER"
        rt["final_delivery"] = {
            "v": 1,
            "kind": "planner_user_query",
            "event_id": _stable_token("planner-user-query", {"task": task_id, "turn": outcome_ref}),
            "task_id": task_id,
            "control_epoch": int(cell.get("control_epoch") or 0),
            "decision_ref": outcome_ref,
            "result_ref": outcome_ref,
            "result_blob_sha": _blob_sha(store, base_sha, outcome_ref),
            "question": str(semantic.get("question") or "").strip(),
            "state": "PENDING",
            "claim_id": None,
            "claimed_at": None,
            "delivered_at": None,
            "consumed_at": None,
            "published_at": utc_now(),
            "terminal_status": None,
        }
        new_control["runtime"] = rt
    elif outcome_kind == "ERROR":
        error_code = str(semantic.get("code") or "PLANNER_ERROR")
        new_control["activity"] = "ERROR"
        new_control["semantic_authority_closed"] = True
        new_control["error"] = {
            "code": error_code,
            "refs": [str(x) for x in semantic.get("refs") or []],
            "turn_ref": outcome_ref,
        }
        rt["pending_output"] = None
        rt["active_turn"] = None
        rt["worker_round"] = None
        rt["final_delivery"] = {
            "v": 1,
            "kind": "planner_final_delivery",
            "event_id": _stable_token("terminal-planner", {"task": task_id, "turn": outcome_ref}),
            "task_id": task_id,
            "control_epoch": int(cell.get("control_epoch") or 0),
            "decision_ref": outcome_ref,
            "result_ref": outcome_ref,
            "result_blob_sha": _blob_sha(store, base_sha, outcome_ref),
            "state": "PENDING",
            "claim_id": None,
            "claimed_at": None,
            "delivered_at": None,
            "consumed_at": None,
            "published_at": utc_now(),
            "terminal_status": "ERROR",
            "error_code": error_code,
        }
        new_control["runtime"] = rt

    current = _read_json(store, base_sha, planner_current_path(task_id))
    plan_ref = str(current.get("plan_ref") or f"tasks/{task_id}.plan.json")
    plan_sha = _blob_sha(store, base_sha, plan_ref)
    memory_ref = planner_semantic_memory_path(task_id)

    if outcome_kind == "COMPLETE":
        final_result_ref = str(semantic.get("final_result_ref") or "").replace("\\", "/").strip()
        if not final_result_ref or not _artifact_exists(store, base_sha, final_result_ref):
            return {"ok": False, "error": "PLANNER_COMPLETE_FINAL_RESULT_REQUIRED", "task_id": task_id}
        final_result_blob_sha = _blob_sha(store, base_sha, final_result_ref)
        final_refs = {
            "terminal_decision_ref": outcome_ref,
            "terminal_decision_blob_sha": _blob_sha(store, base_sha, outcome_ref),
            "final_result_ref": final_result_ref,
            "final_result_blob_sha": final_result_blob_sha,
        }
        new_current = terminalize_planner_memory(
            current,
            expected_memory_version=int(current["memory_version"]),
            final_refs=final_refs,
            written_at=utc_now(),
        )
        new_control = collapse_terminal_projection(
            new_control,
            terminal_refs=final_refs,
        )
        new_control["activity"] = "CLEANING"
        rt = _runtime(new_control)
        rt["pending_output"] = None
        rt["active_turn"] = None
        rt["worker_round"] = None
        rt["final_delivery"] = {
            "v": 1,
            "kind": "planner_final_delivery",
            "event_id": _stable_token("terminal-planner", {"task": task_id, "turn": outcome_ref}),
            "task_id": task_id,
            "control_epoch": int(cell.get("control_epoch") or 0),
            "decision_ref": outcome_ref,
            "result_ref": final_result_ref,
            "result_blob_sha": final_result_blob_sha,
            "state": "PENDING",
            "claim_id": None,
            "claimed_at": None,
            "delivered_at": None,
            "consumed_at": None,
            "published_at": utc_now(),
            "terminal_status": "PASS",
        }
        new_control["runtime"] = rt
    elif outcome_kind == "ERROR":
        final_refs = {
            "terminal_decision_ref": outcome_ref,
            "terminal_decision_blob_sha": _blob_sha(store, base_sha, outcome_ref),
            "final_result_ref": outcome_ref,
            "final_result_blob_sha": _blob_sha(store, base_sha, outcome_ref),
        }
        new_current = terminalize_planner_memory(
            current,
            expected_memory_version=int(current["memory_version"]),
            final_refs=final_refs,
            written_at=utc_now(),
        )
        new_control["terminal_refs"] = copy.deepcopy(final_refs)
    else:
        if str(current.get("plan_blob_sha") or "") != plan_sha:
            new_current = checkpoint_planner_current(
                current,
                {"plan_blob_sha": plan_sha},
                expected_memory_version=int(current["memory_version"]),
                trigger="PLAN_CHANGED",
                written_at=utc_now(),
            )
        else:
            new_current = current

    new_cell = copy.deepcopy(cell)
    new_cell["planner_control"] = new_control
    new_cell["status"] = (
        "CLEANING" if outcome_kind == "COMPLETE"
        else "ERROR" if outcome_kind == "ERROR"
        else str(new_control.get("activity") or "ACTIVE")
    )

    worktree = _prepare_worktree(store, base_sha, f"planner-turn-reduce-{task_id}")
    try:
        _append_text(worktree, memory_ref, memory_entry, header="# Planner Memory\\n")
        paths = [memory_ref]

        for path, value in append_texts.items():
            _append_text(worktree, path, value)
            paths.append(path)
        for path, value in write_objects.items():
            _write_json(worktree, path, value)
            paths.append(path)
        for path, value in write_texts.items():
            _write_text(worktree, path, value)
            paths.append(path)

        _write_json(worktree, cell_ref, new_cell)
        _write_json(worktree, planner_current_path(task_id), new_current)
        paths.extend([cell_ref, planner_current_path(task_id)])
        message = f"Reduce Planner turn for {task_id}"
        if dispatched_count == 0:
            message += " [skip ci]"
        commit_sha = _commit_push(store, worktree, list(dict.fromkeys(paths)), message)
    finally:
        _drop_worktree(store, worktree)

    return {
        "ok": True,
        "action": "planner_turn_reduced",
        "task_id": task_id,
        "outcome": outcome_kind,
        "dispatched_workers": dispatched_count,
        "settled_slots": settled_count,
        "commit_sha": commit_sha,
    }


def observe_semantic_turn(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Bind a short browser signal to its existing canonical role/output.

    The host supplies identity from the observed conversation, never from the
    assistant's response. Semantic files remain the normal Git write surface.
    """
    store._safe_client(req.get("client_id"))
    task_id = _safe_task_id(req.get("task_id"))
    role = str(req.get("role") or "")
    signal = str(req.get("signal") or "").strip().lower()
    sync_from_git = bool(req.get("sync_from_git"))
    with store.git_lock:
        store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
        base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
        if role == "worker":
            task = _read_json(store, base_sha, _task_contract_ref(task_id))
            if task.get("kind") != "planner_worker_child":
                return {"ok": True, "accepted": False, "reason": "not_planner_child"}
            backend = str(task.get("backend_cl") or "")
            bg = _read_json(store, base_sha, backend)
            dispatch = bg.get("dispatch") or {}
            if (backend != req.get("backend_cl") or
                str(dispatch.get("dispatch_id")) != str(req.get("dispatch_id")) or
                int(dispatch.get("generation") or 0) != int(req.get("dispatch_generation") or 0) or
                str(dispatch.get("fence_token")) != str(req.get("fence_token")) or
                str(dispatch.get("acked_by_worker_ref") or "") != str(req.get("worker_ref") or "")):
                return {"ok": True, "accepted": False, "reason": "stale_dispatch"}
            if _terminal_dispatch_state(dispatch.get("state")):
                return {"ok": True, "accepted": True, "duplicate": True, "role": "worker"}
            output_ref = str(task["expected_result_ref"])
            previous_output = _try_read_json(store, base_sha, output_ref) or {}
            if sync_from_git:
                signal = str(previous_output.get("turn_signal") or "").strip().lower()
            status = {"continue": "CONTINUE", "complete": "PASS", "blocked": "BLOCKED", "error": "ERROR"}.get(signal)
            if status is None:
                return {
                    "ok": True,
                    "accepted": False,
                    "reason": "worker_turn_signal_missing" if sync_from_git else "worker_signal_missing",
                    "role": "worker",
                }
            output = copy.deepcopy(previous_output)
            if status in {"CONTINUE", "PASS"}:
                try:
                    from .task_cell_ledgers import worker_reply_has_write
                except ImportError:
                    from task_cell_ledgers import worker_reply_has_write
                reply_ref = str(task.get("worker_reply_entry_ref") or "")
                try:
                    reply_text = store._git("show", f"{base_sha}:{reply_ref}").stdout
                except subprocess.SubprocessError:
                    reply_text = ""
                if not worker_reply_has_write(reply_text, str(dispatch["wake_id"])):
                    # A final word without durable work is an observed protocol
                    # failure, not success and not an indefinitely pending turn.
                    status = "ERROR"
                    output["summary"] = f"Worker emitted {signal} without writing its current Reply entry."
                    output["blocker"] = {"kind": "WORKER_REPLY_MISSING", "reply_ref": reply_ref, "observed_signal": signal}
            # These fields are filled mechanically, not echoed by the Worker.
            output.update(task.get("result_contract") or {})
            output["status"] = status
            output.setdefault("summary", "See the current Worker Child Reply entry.")
            sha = base_sha
            if output != previous_output:
                worktree = _prepare_worktree(store, base_sha, f"worker-signal-{task_id}")
                try:
                    _write_json(worktree, output_ref, output)
                    sha = _commit_push(store, worktree, [output_ref], f"Record Worker {signal} for {task_id} [skip ci]")
                finally:
                    _drop_worktree(store, worktree)
            try:
                from .scheduler import _try_finalize_planner_worker_child
            except ImportError:
                from scheduler import _try_finalize_planner_worker_child
            result = _try_finalize_planner_worker_child(store, task_id=task_id, backend_cl_rel=backend, canonical_sha=sha, attempt=0)
            return {"ok": True, "accepted": bool(result and result.get("finalized")), "result": result}

        if role not in {"planner", "helper"}:
            return {"ok": False, "error": "SEMANTIC_ROLE_INVALID"}
        cell_ref = _task_cell_path(task_id)
        cell = _read_json(store, base_sha, cell_ref)
        if int(cell.get("control_epoch") or 0) != int(req.get("control_epoch") or 0):
            return {"ok": True, "accepted": False, "reason": "stale_epoch"}
        control = cell.get("planner_control") or {}
        runtime = _runtime(control)
        request_id = str(req.get("request_id") or "")
        output_ref = str(req.get("output_ref") or "")
        if role == "helper":
            binding = (cell.get("roles") or {}).get("helper") or {}
            pending = next((row for row in runtime.get("pending_role_outputs") or []
                            if row.get("request_id") == request_id and row.get("artifact_ref") == output_ref), None)
            if not pending or binding.get("conversation_id") != req.get("conversation_id"):
                return {"ok": True, "accepted": False, "reason": "helper_binding_mismatch"}
            output = _try_read_json(store, base_sha, output_ref)
            if sync_from_git and isinstance(output, dict):
                signal = str(output.get("turn_signal") or "").strip().lower()
            if signal != "done":
                return {
                    "ok": True,
                    "accepted": False,
                    "reason": "helper_turn_signal_missing" if sync_from_git else "helper_signal_missing",
                    "role": "helper",
                }
            ready = (
                isinstance(output, dict)
                and bool(str(output.get("diagnosis") or "").strip())
                and bool(str(output.get("repair_result") or "").strip())
            )
            if not ready:
                return {"ok": True, "accepted": False, "reason": "helper_output_missing", "role": "helper"}
            if not pending.get("done_observed_at"):
                pending["done_observed_at"] = utc_now()
                control["runtime"] = runtime
                cell["planner_control"] = control
                worktree = _prepare_worktree(store, base_sha, f"helper-done-{task_id}")
                try:
                    _write_json(worktree, cell_ref, cell)
                    _commit_push(store, worktree, [cell_ref], f"Receive Helper done for {task_id} [skip ci]")
                finally:
                    _drop_worktree(store, worktree)
            return {"ok": True, "accepted": True, "role": "helper", "reason": None}

        authority = control.get("authority") or {}
        if (control.get("successor") or {}).get("state", "NONE") != "NONE":
            return {"ok": True, "accepted": False, "reason": "planner_rotation_pending"}
        if authority.get("conversation_id") != req.get("conversation_id"):
            return {"ok": True, "accepted": False, "reason": "planner_binding_mismatch"}
        if (control.get("last_decision") or {}).get("decision_ref") == output_ref:
            return {"ok": True, "accepted": True, "duplicate": True, "role": "planner"}
        active = runtime.get("active_turn") or {}
        pending = runtime.get("pending_output") or {}
        if active.get("outcome_ref") != output_ref or pending.get("request_id") != request_id:
            return {"ok": True, "accepted": False, "reason": "planner_turn_mismatch"}
        output = _try_read_json(store, base_sha, output_ref)
        if sync_from_git and isinstance(output, dict):
            signal = str(output.get("turn_signal") or "").strip().lower()
        if signal == "handoff":
            result = request_planner_rotation(store, {"task_id": task_id, "reason": "context_compacted", "caller_role": "harness"})
            return {**result, "accepted": bool(result.get("staged")), "role": "planner"}
        kind = {"done": "CONTINUE", "rework": "CONTINUE", "complete": "COMPLETE",
                "wait": "WAIT", "need_user": "NEED_USER", "error": "ERROR"}.get(signal)
        if kind is None:
            return {
                "ok": True,
                "accepted": False,
                "reason": "planner_turn_signal_missing" if sync_from_git else "planner_signal_missing",
                "role": "planner",
            }
        if not isinstance(output, dict):
            return {"ok": True, "accepted": False, "reason": "planner_output_missing", "role": "planner"}
        if output.get("outcome") != kind:
            output["outcome"] = kind
            worktree = _prepare_worktree(store, base_sha, f"planner-signal-{task_id}")
            try:
                _write_json(worktree, output_ref, output)
                base_sha = _commit_push(store, worktree, [output_ref], f"Record Planner {signal} for {task_id} [skip ci]")
            finally:
                _drop_worktree(store, worktree)
        state = _read_json(store, base_sha, "state/chatgpt.json")
        result = _consume_planner_turn(store, base_sha=base_sha, cell_ref=cell_ref, cell=cell,
                                       state=state, project_id=str(req.get("project_id") or ""),
                                       repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY")
        return {"ok": True, "accepted": bool(result and result.get("action") == "planner_turn_reduced"),
                "role": "planner", "result": result}


def sync_semantic_turn(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Actively reconcile a bound semantic turn from its durable Git turn_signal.

    The kick carries no semantic result and no routing decision. Harness fetches
    canonical Git, validates the existing bound identity, reads turn_signal from
    the pre-bound output, and then reuses the normal semantic reducer. Browser
    response-end observation remains a fallback only.
    """
    return observe_semantic_turn(store, {**req, "sync_from_git": True, "signal": ""})


def _terminal_dispatch_state(value: Any) -> bool:
    return str(value or "") in {"DONE", "ERROR", "BLOCKED", "CANCELLED"}


def _child_wake_payload(
    *,
    project_id: str,
    repo: str,
    owner_task_id: str,
    owner_control_epoch: int,
    child_task_id: str,
    backend_cl: str,
    lane_id: str,
    worker_project_key: str,
    dispatch: dict[str, Any],
    result_ref: str,
    child_reply_ref: str,
    worker_reply_entry_ref: str,
) -> dict[str, Any]:
    return {
        "v": 1,
        "project_id": project_id,
        "repo": repo,
        "state": "NEED_AGENT",
        "created_at": dispatch["requested_at"],
        "kind": "task_start",
        "wake_id": dispatch["wake_id"],
        "lane_id": lane_id,
        "worker_project_key": worker_project_key,
        "owner_task_id": owner_task_id,
        "owner_control_epoch": int(owner_control_epoch),
        "task_id": child_task_id,
        "backend_cl": backend_cl,
        "dispatch_id": dispatch["dispatch_id"],
        "dispatch_generation": dispatch["generation"],
        "fence_token": dispatch["fence_token"],
        "result_ref": result_ref,
        "child_reply_ref": child_reply_ref,
        "worker_reply_entry_ref": worker_reply_entry_ref,
    }


def _build_worker_child(
    *,
    parent_task_id: str,
    parent_control_epoch: int,
    decision_ref: str,
    action: dict[str, Any],
    project_id: str,
    repo: str,
) -> dict[str, Any]:
    child_task_id = _safe_task_id(action.get("child_task_id"))
    lane_id = str(action.get("lane_id") or "").strip()
    worker_project_key = str(action.get("worker_project_key") or "").strip()
    if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
        raise PlannerRuntimeError("PLANNER_CHILD_SCHEDULING_INVALID")
    task_payload = action.get("task_payload")
    if not isinstance(task_payload, dict):
        raise PlannerRuntimeError("PLANNER_CHILD_TASK_PAYLOAD_REQUIRED")
    wait = action.get("wait")
    if not isinstance(wait, dict) or str(wait.get("kind") or "") != "WAIT_ADMISSION":
        raise PlannerRuntimeError("PLANNER_CHILD_WAIT_ADMISSION_REQUIRED")
    task_ref = f"tasks/{child_task_id}.json"
    backend_cl = f"cl/{child_task_id}.backend.json"
    now_text = utc_now()
    dispatch_id = _stable_token("dispatch", {"parent": parent_task_id, "decision": decision_ref, "child": child_task_id})
    wake_id = _stable_token("wake", {"dispatch_id": dispatch_id, "generation": 1})
    fence_token = _stable_token("fence", {"dispatch_id": dispatch_id, "generation": 1})
    expected_result_ref = f"{ROLE_OUTPUT_ROOT}/{parent_task_id}/roles/worker/{child_task_id}/{wake_id}.json"
    child_reply_ref = worker_child_reply_path(child_task_id)
    worker_reply_entry_ref = worker_turn_reply_entry_path(child_task_id, wake_id)
    dispatch = {
        "dispatch_id": dispatch_id,
        "wake_id": wake_id,
        "generation": 1,
        "fence_token": fence_token,
        "state": "READY",
        "requested_at": now_text,
        "continuation_ref": task_ref,
        "delivered_at": None,
        "acked_at": None,
        "acked_by_worker_ref": None,
        "lease_expires_at": None,
        "wait_ref": None,
        "ack_source": None,
    }
    result_contract = {
        "v": 1,
        "task_id": child_task_id,
    }
    child_task = {
        **copy.deepcopy(task_payload),
        "v": 1,
        "task_id": child_task_id,
        "kind": "planner_worker_child",
        "parent_task_id": parent_task_id,
        "owner_task_id": parent_task_id,
        "owner_control_epoch": int(parent_control_epoch),
        "parent_planner_decision_ref": decision_ref,
        "backend_cl": backend_cl,
        "lane_id": lane_id,
        "worker_project_key": worker_project_key,
        "expected_result_ref": expected_result_ref,
        "result_contract": result_contract,
        "child_reply_ref": child_reply_ref,
        "worker_reply_entry_ref": worker_reply_entry_ref,
        "execution_allowed": True,
        "status": "READY",
    }
    cl = {
        "v": 1,
        "cl_id": f"bg-{child_task_id}",
        "task_id": child_task_id,
        "scope": "backend_execution",
        "overall": "READY",
        "created_at": now_text,
        "updated_at": now_text,
        "result_ref": None,
        "error": None,
        "wait_ref": None,
        "dispatch": dispatch,
        "scheduling": {
            "lane_id": lane_id,
            "worker_project_key": worker_project_key,
            "owner_task_id": parent_task_id,
            "owner_control_epoch": int(parent_control_epoch),
        },
        "conditions": [
            {"id": "claimed", "state": "WAIT", "detail": "waiting for exact Worker response-start ACK", "evidence_ref": None},
            {"id": "semantic_work", "state": "WAIT", "detail": "Planner-authorized bounded child work pending", "evidence_ref": None},
            {"id": "branch_output", "state": "WAIT", "detail": "durable child semantic result pending", "evidence_ref": None},
        ],
    }
    wake = _child_wake_payload(
        project_id=project_id,
        repo=repo,
        owner_task_id=parent_task_id,
        owner_control_epoch=int(parent_control_epoch),
        child_task_id=child_task_id,
        backend_cl=backend_cl,
        lane_id=lane_id,
        worker_project_key=worker_project_key,
        dispatch=dispatch,
        result_ref=expected_result_ref,
        child_reply_ref=child_reply_ref,
        worker_reply_entry_ref=worker_reply_entry_ref,
    )
    wake["kind"] = "planner_worker_child"
    wake["replace_conversation"] = True
    planner_direction_id = _stable_token(
        "planner-direction",
        {"parent": parent_task_id, "decision": decision_ref, "child": child_task_id},
    )
    child_reply_initial = (
        "# Worker Child Reply\n"
        + render_child_reply_entry(
            entry_id=planner_direction_id,
            writer_role="planner",
            entry_type="DIRECTION",
            semantic=task_payload,
        )
    )
    return {
        "child_task_id": child_task_id,
        "task_ref": task_ref,
        "backend_cl": backend_cl,
        "lane_id": lane_id,
        "worker_project_key": worker_project_key,
        "owner_task_id": parent_task_id,
        "owner_control_epoch": int(parent_control_epoch),
        "task": child_task,
        "cl": cl,
        "wake_ref": f"requests/worker-wake/{wake_id}.json",
        "wake": wake,
        "dispatch": dispatch,
        "child_reply_ref": child_reply_ref,
        "child_reply_initial": child_reply_initial,
        "worker_reply_entry_ref": worker_reply_entry_ref,
        "worker_reply_entry_initial": worker_turn_reply_entry_header(wake_id),
        "work_branch": str(action.get("work_branch") or "").strip() or None,
        "work_branch_base_ref": str(action.get("work_branch_base_ref") or "main").strip() or "main",
    }



LOCAL_WORKER_LANES_ENV = "CAH_LOCAL_WORKER_LANES"


def _configured_local_worker_lanes() -> set[str]:
    """Return lanes physically hosted by this bridge process.

    This is transport configuration only. Canonical lane/task authority remains
    in Git; the list merely decides whether an already-pushed rollover wake may
    take the local delivery fast path.
    """
    raw = str(os.environ.get(LOCAL_WORKER_LANES_ENV) or "")
    lanes: set[str] = set()
    for item in raw.split(","):
        lane_id = item.strip()
        if lane_id.startswith("lane-"):
            lanes.add(lane_id)
    return lanes


def _emit_local_worker_rollover(
    store: Any,
    continuation: dict[str, Any],
    *,
    commit_sha: str,
) -> dict[str, Any]:
    """Best-effort local delivery after the rollover is canonical in Git.

    The durable requests/worker-wake entry is intentionally retained. If this
    fast path is unavailable or fails, the existing Actions/reconcile path still
    delivers the same exact wake. WakeStore.emit is idempotent by wake_id, so a
    later durable delivery safely collapses to the same doorbell.
    """
    lane_id = str(continuation.get("lane_id") or "").strip()
    dispatch = continuation.get("dispatch") if isinstance(continuation.get("dispatch"), dict) else {}
    wake = continuation.get("wake") if isinstance(continuation.get("wake"), dict) else {}
    generation = int(dispatch.get("generation") or 0)

    if not commit_sha:
        return {"attempted": False, "ok": False, "reason": "canonical_commit_missing"}
    if generation < 2 or wake.get("replace_conversation") is not True:
        return {"attempted": False, "ok": False, "reason": "not_worker_rollover"}
    if lane_id not in _configured_local_worker_lanes():
        return {
            "attempted": False,
            "ok": True,
            "reason": "lane_not_local",
            "lane_id": lane_id or None,
            "fallback": "canonical_worker_wake",
        }

    try:
        emitted = store.emit(wake)
    except Exception as exc:
        return {
            "attempted": True,
            "ok": False,
            "reason": "local_emit_failed",
            "lane_id": lane_id,
            "wake_id": str(wake.get("wake_id") or "") or None,
            "error": str(exc)[:300],
            "fallback": "canonical_worker_wake",
        }

    exact = (
        isinstance(emitted, dict)
        and emitted.get("ok") is True
        and str(emitted.get("wake_id") or "") == str(wake.get("wake_id") or "")
    )
    return {
        "attempted": True,
        "ok": exact,
        "reason": "local_emit" if exact else "local_emit_unacknowledged",
        "lane_id": lane_id,
        "wake_id": str(wake.get("wake_id") or "") or None,
        "duplicate": bool(emitted.get("duplicate")) if isinstance(emitted, dict) else False,
        "canonical_commit_sha": commit_sha,
        "fallback": None if exact else "canonical_worker_wake",
    }

def _continue_worker_child(
    store: Any,
    base_sha: str,
    *,
    parent_task_id: str,
    parent_control_epoch: int,
    child: dict[str, Any],
    direction_id: str,
    project_id: str,
    repo: str,
) -> dict[str, Any]:
    child_task_id = _safe_task_id(child.get("child_task_id"))
    task_ref = str(child.get("task_ref") or f"tasks/{child_task_id}.json")
    backend_cl = str(child.get("backend_cl") or f"cl/{child_task_id}.backend.json")
    task = _read_json(store, base_sha, task_ref)
    cl = _read_json(store, base_sha, backend_cl)
    current = cl.get("dispatch") if isinstance(cl.get("dispatch"), dict) else {}
    generation = int(current.get("generation") or 0) + 1
    lane_id = str(child.get("lane_id") or task.get("lane_id") or "").strip()
    worker_project_key = str(child.get("worker_project_key") or task.get("worker_project_key") or "").strip()
    now_text = utc_now()
    dispatch_id = _stable_token(
        "dispatch",
        {
            "parent": parent_task_id,
            "child": child_task_id,
            "direction": direction_id,
            "generation": generation,
        },
    )
    wake_id = _stable_token("wake", {"dispatch_id": dispatch_id, "generation": generation})
    fence_token = _stable_token("fence", {"dispatch_id": dispatch_id, "generation": generation})
    expected_result_ref = f"{ROLE_OUTPUT_ROOT}/{parent_task_id}/roles/worker/{child_task_id}/{wake_id}.json"
    child_reply_ref = str(task.get("child_reply_ref") or worker_child_reply_path(child_task_id))
    worker_reply_entry_ref = worker_turn_reply_entry_path(child_task_id, wake_id)
    dispatch = {
        "dispatch_id": dispatch_id,
        "wake_id": wake_id,
        "generation": generation,
        "fence_token": fence_token,
        "state": "READY",
        "requested_at": now_text,
        "continuation_ref": task_ref,
        "delivered_at": None,
        "acked_at": None,
        "acked_by_worker_ref": None,
        "lease_expires_at": None,
        "wait_ref": None,
        "ack_source": None,
    }
    next_task = copy.deepcopy(task)
    # The next generation may replace only the Worker that owned this Child's
    # previous dispatch. Admission compares this durable predecessor atomically.
    dispatch["predecessor_worker_ref"] = current.get("acked_by_worker_ref")
    next_task["expected_result_ref"] = expected_result_ref
    next_task["worker_reply_entry_ref"] = worker_reply_entry_ref
    next_task["status"] = "READY"
    next_cl = copy.deepcopy(cl)
    next_cl["overall"] = "READY"
    next_cl["updated_at"] = now_text
    next_cl["result_ref"] = None
    next_cl["error"] = None
    next_cl["wait_ref"] = None
    next_cl["dispatch"] = dispatch
    for condition in next_cl.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if condition.get("id") in {"claimed", "semantic_work", "branch_output"}:
            condition["state"] = "WAIT"
            condition["evidence_ref"] = None
    wake = _child_wake_payload(
        project_id=project_id,
        repo=repo,
        owner_task_id=parent_task_id,
        owner_control_epoch=int(parent_control_epoch),
        child_task_id=child_task_id,
        backend_cl=backend_cl,
        lane_id=lane_id,
        worker_project_key=worker_project_key,
        dispatch=dispatch,
        result_ref=expected_result_ref,
        child_reply_ref=child_reply_ref,
        worker_reply_entry_ref=worker_reply_entry_ref,
    )
    wake["kind"] = "planner_worker_child"
    wake["replace_conversation"] = True
    return {
        "child_task_id": child_task_id,
        "task_ref": task_ref,
        "backend_cl": backend_cl,
        "lane_id": lane_id,
        "worker_project_key": worker_project_key,
        "owner_task_id": parent_task_id,
        "owner_control_epoch": int(parent_control_epoch),
        "task": next_task,
        "cl": next_cl,
        "wake_ref": f"requests/worker-wake/{wake_id}.json",
        "wake": wake,
        "dispatch": dispatch,
        "child_reply_ref": child_reply_ref,
        "worker_reply_entry_ref": worker_reply_entry_ref,
        "worker_reply_entry_initial": worker_turn_reply_entry_header(wake_id),
    }


def _public_final_delivery(cell_ref: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_cell_ref": cell_ref,
        "event_id": event.get("event_id"),
        "task_id": event.get("task_id"),
        "control_epoch": event.get("control_epoch"),
        "decision_ref": event.get("decision_ref"),
        "result_ref": event.get("result_ref"),
        "result_blob_sha": event.get("result_blob_sha"),
        "state": event.get("state"),
        "claim_id": event.get("claim_id"),
        "claimed_at": event.get("claimed_at"),
        "delivered_at": event.get("delivered_at"),
        "consumed_at": event.get("consumed_at"),
        "published_at": event.get("published_at"),
        "kind": event.get("kind"),
        "question": event.get("question"),
        "terminal_status": event.get("terminal_status"),
        "error_code": event.get("error_code"),
        "foreground_cl": cell_ref,
        "cleanup_complete": event.get("cleanup_complete", False),
        "cleanup_receipt": event.get("cleanup_receipt"),
        "record_archive": event.get("record_archive"),
    }


def _ensure_work_branch(store: Any, base_sha: str, branch_name: str | None) -> None:
    if not branch_name:
        return
    name = str(branch_name).strip()
    if not name.startswith("work/") or len(name) > 180:
        raise PlannerRuntimeError("PLANNER_CHILD_WORK_BRANCH_INVALID")
    try:
        existing = store._git("ls-remote", "--heads", store.git_remote, f"refs/heads/{name}").stdout.strip()
    except subprocess.SubprocessError as exc:
        raise PlannerRuntimeError("PLANNER_CHILD_WORK_BRANCH_LOOKUP_FAILED") from exc
    if existing:
        return
    try:
        store._git("push", store.git_remote, f"{base_sha}:refs/heads/{name}")
    except subprocess.SubprocessError as exc:
        raise PlannerRuntimeError("PLANNER_CHILD_WORK_BRANCH_CREATE_FAILED") from exc


def enqueue_planner_event(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    kind = str(req.get("kind") or "").strip()
    source_role = str(req.get("source_role") or "").strip()
    source_identity = req.get("source_identity")
    refs = req.get("refs") or []
    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            cell_ref = _task_cell_path(task_id)
            cell = _read_json(store, base_sha, cell_ref)
            control = cell.get("planner_control")
            if not isinstance(control, dict) or control.get("enabled") is not True:
                return {"ok": False, "error": "PLANNER_RUNTIME_NOT_ENABLED"}
            try:
                event = make_planner_event(
                    task_id=task_id,
                    control_epoch=int(cell.get("control_epoch") or 0),
                    kind=kind,
                    source_role=source_role,
                    source_identity=source_identity,
                    refs=refs,
                )
                new_control, inserted = insert_planner_event(control, event)
            except PlannerControlError as exc:
                return {"ok": False, "error": exc.code}
            if not inserted:
                return {"ok": True, "inserted": False, "event_id": event["event_id"], "idle": "duplicate_event"}
            new_cell = copy.deepcopy(cell)
            new_cell["planner_control"] = new_control
            worktree = _prepare_worktree(store, base_sha, f"planner-event-{task_id}-{attempt}")
            try:
                _write_json(worktree, cell_ref, new_cell)
                try:
                    commit_sha = _commit_push(store, worktree, [cell_ref], f"Enqueue Planner event for {task_id} [skip ci]")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_EVENT_PUSH_FAILED"}
                return {"ok": True, "inserted": True, "event_id": event["event_id"], "commit_sha": commit_sha}
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_EVENT_RETRY_EXHAUSTED"}


def _set_control_request(
    state: dict[str, Any],
    *,
    kind: str,
    request_id: str,
    task_id: str,
    epoch: int,
    role: str,
    challenge: str,
    project_key: str,
    prompt: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    if not _control_slot_available(state):
        raise PlannerRuntimeError("PLANNER_RUNTIME_CONTROL_SLOT_BUSY")
    state["control_request"] = {
        "v": 1,
        "kind": kind,
        "request_id": request_id,
        "status": "PENDING",
        "requested_at": utc_now(),
        "task_cell_project_key": project_key,
        "task_id": task_id,
        "control_epoch": int(epoch),
        "role": role,
        "challenge": challenge,
        "prompt": prompt,
        **(copy.deepcopy(extra) if extra else {}),
    }
    state["updated"] = utc_now()[:10]
    state["writeback_reason"] = f"Planner runtime staged {kind} {request_id} for {task_id}."


def _rotation_due(control: dict[str, Any], now: datetime | None = None) -> tuple[bool, str | None]:
    # Context compaction is signalled by the bound Planner, not inferred from
    # prompt counts or elapsed wall time. Runtime failures go to Helper.
    return False, None


def _stage_rotation_in_memory(
    *,
    cell: dict[str, Any],
    current: dict[str, Any],
    reason: str,
    now_text: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str, str]:
    control = copy.deepcopy(cell.get("planner_control") or {})
    authority = _authority(cell)
    successor = control.get("successor") or {}
    if str(successor.get("state") or "NONE") != "NONE":
        raise PlannerRuntimeError("PLANNER_RUNTIME_SUCCESSOR_ALREADY_EXISTS")
    generation = int(authority.get("planner_generation") or 0)
    if generation < 1:
        raise PlannerRuntimeError("PLANNER_RUNTIME_GENERATION_INVALID")
    handoff_id = _stable_token("handoff", {
        "task_id": cell["task_id"],
        "epoch": cell["control_epoch"],
        "generation": generation,
        "memory_version": current.get("memory_version"),
        "reason": reason,
    })
    pending_fence = _stable_token("planner-fence", {
        "task_id": cell["task_id"],
        "epoch": cell["control_epoch"],
        "to_generation": generation + 1,
        "handoff_id": handoff_id,
    })
    handoff_ref = planner_handoff_path(cell["task_id"], handoff_id)
    updated_current = copy.deepcopy(current)
    current_ref = planner_current_path(cell["task_id"])
    current_sha = memory_blob_sha(updated_current)
    sealed = seal_planner_generation(
        updated_current,
        conversation_identity={
            "project_key": authority.get("project_key"),
            "conversation_id": authority.get("conversation_id"),
        },
        started_at=str(_runtime(control).get("generation_started_at") or now_text),
        sealed_at=now_text,
        seal_reason="ROLLOVER",
        source_current_ref=current_ref,
        source_current_blob_sha=current_sha,
        successor_handoff_id_or_null=handoff_id,
    )
    generation_ref = planner_generation_path(cell["task_id"], generation)
    sealed_sha = memory_blob_sha(sealed)
    handoff = prepare_planner_handoff(
        updated_current,
        sealed,
        handoff_id=handoff_id,
        reason=reason,
        pending_successor_fence_token=pending_fence,
        current_memory_ref=current_ref,
        current_memory_blob_sha=current_sha,
        sealed_generation_ref=generation_ref,
        sealed_generation_blob_sha=sealed_sha,
        canonical_task_cell_ref=_task_cell_path(cell["task_id"]),
        canonical_task_cell_blob_sha=_stable_token("cell-snapshot", cell),
        predecessor_project_key=str(authority.get("project_key") or ""),
        predecessor_conversation_id=str(authority.get("conversation_id") or ""),
    )
    staged_control = stage_planner_successor(
        control,
        handoff_id=handoff_id,
        reason=reason,
        pending_fence_token=pending_fence,
    )
    runtime = _runtime(staged_control)
    runtime["rotation_reason"] = reason
    runtime["rotation_started_at"] = now_text
    runtime["browser_promotion_complete"] = False
    runtime["pending_output"] = None
    staged_control["runtime"] = runtime
    new_cell = copy.deepcopy(cell)
    new_cell["planner_control"] = staged_control
    roles = new_cell.setdefault("roles", {})
    planner_role = copy.deepcopy(roles.get("planner") or {})
    planner_role["status"] = "FENCED_ROTATING"
    planner_role["semantic_ready"] = False
    roles["planner"] = planner_role
    return new_cell, updated_current, sealed, handoff_id, pending_fence, handoff_ref


def migrate_planner_runtime(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            cell = _read_json(store, base_sha, _task_cell_path(task_id))
            if isinstance(cell.get("planner_control"), dict):
                return {"ok": True, "migrated": False, "idle": "already_migrated"}
            task_ref = _task_contract_ref(task_id)
            plan_ref = f"tasks/{task_id}.plan.json"
            task = _read_json(store, base_sha, task_ref)
            plan = _read_json(store, base_sha, plan_ref)
            task_sha = store._git("rev-parse", f"{base_sha}:{task_ref}").stdout.strip()
            plan_sha = store._git("rev-parse", f"{base_sha}:{plan_ref}").stdout.strip()
            control_cell = migrate_flat_planner_binding(
                cell,
                admission_ok=True,
                task_contract_ref=task_ref,
                task_contract_revision=int(task.get("task_contract_revision") or task.get("v") or 1),
                task_contract_blob_sha=task_sha,
                plan_ref=plan_ref,
                plan_blob_sha=plan_sha,
            )
            control = control_cell["planner_control"]
            runtime = _runtime(control)
            runtime.update({
                "foreground_runtime_dependency": bool(req.get("foreground_runtime_dependency", False)),
                "authority_mode": "PLANNER",
                "prompt_count": 0,
                "generation_started_at": utc_now(),
                "max_prompts_per_generation": int(req.get("max_prompts_per_generation") or DEFAULT_MAX_PROMPTS_PER_GENERATION),
                "max_generation_age_seconds": int(req.get("max_generation_age_seconds") or DEFAULT_MAX_GENERATION_AGE_SECONDS),
                "semantic_output_timeout_seconds": int(req.get("semantic_output_timeout_seconds") or DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS),
            })
            control["runtime"] = runtime
            control_cell["planner_control"] = control
            authority = control["authority"]
            current = make_planner_current(
                task_id=task_id,
                control_epoch=int(cell["control_epoch"]),
                planner_generation=int(authority["planner_generation"]),
                planner_fence_token=str(authority["planner_fence_token"]),
                task_contract_ref=task_ref,
                task_contract_revision=int(task.get("task_contract_revision") or task.get("v") or 1),
                task_contract_blob_sha=task_sha,
                plan_ref=plan_ref,
                plan_blob_sha=plan_sha,
                written_at=utc_now(),
            )
            worktree = _prepare_worktree(store, base_sha, f"planner-migrate-{task_id}-{attempt}")
            try:
                _write_json(worktree, _task_cell_path(task_id), control_cell)
                _write_json(worktree, planner_current_path(task_id), current)
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        [_task_cell_path(task_id), planner_current_path(task_id)],
                        f"Migrate Planner runtime for {task_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_MIGRATION_PUSH_FAILED"}
                return {
                    "ok": True,
                    "migrated": True,
                    "task_id": task_id,
                    "planner_generation": authority["planner_generation"],
                    "planner_fence_token": authority["planner_fence_token"],
                    "foreground_runtime_dependency": bool(runtime.get("foreground_runtime_dependency")),
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_MIGRATION_RETRY_EXHAUSTED"}


def planner_final_delivery_status(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("project_id required")
    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            cells = [(ref, _try_read_json(store, base_sha, ref)) for ref in _list_task_cells(store, base_sha)]
            # A previous task may still await Foreground delivery. It must not
            # block another task's physical cleanup and reset.
            def cleanup_priority(item: Any) -> bool:
                cell = item[1] or {}
                event = ((cell.get("planner_control") or {}).get("runtime") or {}).get("final_delivery") or {}
                return not (event.get("kind") == "planner_final_delivery"
                            and event.get("state") in {"PENDING", "CLAIMED", "DELIVERED"}
                            and not event.get("cleanup_complete"))
            for cell_ref, cell in sorted(cells, key=cleanup_priority):
                if not isinstance(cell, dict):
                    continue
                control = cell.get("planner_control")
                if not isinstance(control, dict) or str(control.get("activity") or "") not in {"WAIT_USER", "CLEANING", "DONE", "ERROR"}:
                    continue
                runtime = _runtime(control)
                event = runtime.get("final_delivery")
                if (
                    not isinstance(event, dict)
                    or str(event.get("kind") or "") not in {"planner_user_query", "planner_final_delivery"}
                ):
                    continue
                state = str(event.get("state") or "")
                if state == "CONSUMED":
                    continue
                if state == "DELIVERED":
                    return {"ok": True, "event": None, "delivered_wait_response_end": _public_final_delivery(cell_ref, event)}
                if state not in {"PENDING", "CLAIMED"}:
                    return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_STATE_INVALID"}

                if state == "CLAIMED":
                    return {"ok": True, "event": _public_final_delivery(cell_ref, event), "duplicate_claim": True}

                claim_id = _stable_token("claim", {
                    "event_id": event.get("event_id"),
                    "canonical_sha": base_sha,
                })
                new_cell = copy.deepcopy(cell)
                new_control = copy.deepcopy(control)
                rt = _runtime(new_control)
                delivery = copy.deepcopy(rt.get("final_delivery") or {})
                delivery["state"] = "CLAIMED"
                delivery["claim_id"] = claim_id
                delivery["claimed_at"] = utc_now()
                rt["final_delivery"] = delivery
                new_control["runtime"] = rt
                new_cell["planner_control"] = new_control
                worktree = _prepare_worktree(store, base_sha, f"planner-final-claim-{cell['task_id']}-{attempt}")
                try:
                    _write_json(worktree, cell_ref, new_cell)
                    try:
                        commit_sha = _commit_push(
                            store,
                            worktree,
                            [cell_ref],
                            f"Claim Planner final delivery {delivery['event_id']} [skip ci]",
                        )
                    except subprocess.SubprocessError:
                        if attempt == 0:
                            break
                        return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_CLAIM_PUSH_FAILED"}
                    return {
                        "ok": True,
                        "event": _public_final_delivery(cell_ref, delivery),
                        "commit_sha": commit_sha,
                    }
                finally:
                    _drop_worktree(store, worktree)
            else:
                return {"ok": True, "event": None}
    return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_CLAIM_RETRY_EXHAUSTED"}


def _archive_project_records(worktree: Path, cell: dict[str, Any], event: dict[str, Any], base_sha: str) -> dict[str, Any]:
    """Archive exact task-owned Git records; never reclaim engineering files."""
    import os
    from harness.git_process import run_git

    task_id = _safe_task_id(cell["task_id"])
    epoch = int(cell["control_epoch"])
    task_ref = _task_contract_ref(task_id)
    task_path = worktree / task_ref
    task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
    directory = str(task.get("project_directory") or "")
    if not directory:
        # Older contracts never bound an engineering directory. Do not guess a
        # destination or silently destroy their durable continuity records.
        return {"status": "RETAINED_UNBOUND", "pruned_cache_refs": []}
    root = Path(directory)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("project_directory must be an existing absolute engineering directory")
    root = root.resolve(strict=True)
    children = []
    for path in (worktree / "tasks").glob("*.json"):
        child = json.loads(path.read_text(encoding="utf-8"))
        if (child.get("kind") == "planner_worker_child" and child.get("owner_task_id") == task_id
                and int(child.get("owner_control_epoch") or 0) == epoch):
            children.append(_safe_task_id(child["task_id"]))
    prefixes = [f"memory/planner/{task_id}/", f"state/planner_turns/{task_id}/"]
    for child in children:
        prefixes += [f"memory/worker/{child}/", f"state/worker_turns/{child}/"]
    tracked = _run(worktree, "ls-tree", "-r", "--name-only", "-z", base_sha).stdout.split("\0")
    caches = sorted(p for p in tracked if p and any(p.startswith(prefix) for prefix in prefixes))
    refs = set(caches + [task_ref, f"tasks/{task_id}.plan.json"]
               + [f"tasks/{child}.json" for child in children])
    refs.update(str(event.get(k) or "") for k in ("decision_ref", "result_ref"))
    records = {p: run_git(worktree, "show", f"{base_sha}:{p}", binary=True).stdout
               for p in sorted(refs) if p in tracked}
    hashes = {p: hashlib.sha256(data).hexdigest() for p, data in records.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    sections = [f"# Project process records\n\nTask: {task_id}\nEpoch: {epoch}\n"
                f"Event: {event['event_id']}\nRecords SHA256: {digest}\n"]
    for ref, data in records.items():
        body = data.decode("utf-8")
        fence = "```"
        while fence in body:
            fence += "`"
        sections.append(f"\n## {ref}\n\nSHA256: {hashes[ref]}\n\n{fence}\n{body}\n{fence}\n")
    content = "".join(sections).encode("utf-8")
    destination = root / "records.md"
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or destination.read_bytes() != content:
            destination = root / f"records-{task_id}-g{epoch}-{digest[:12]}.md"
    if destination.is_symlink():
        raise ValueError("archive destination is a symlink")
    if not destination.exists():
        with destination.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    if destination.read_bytes() != content:
        raise ValueError("archive readback differs; original records retained")
    manifest_ref = f"evidence/{task_id}/cleanup/project-records-g{epoch}.json"
    return {"status": "ARCHIVED", "path": str(destination),
            "sha256": hashlib.sha256(content).hexdigest(), "source_commit": base_sha,
            "manifest_ref": manifest_ref, "record_hashes": hashes, "pruned_cache_refs": caches}


def planner_final_delivery_update(store: Any, req: dict[str, Any], *, operation: str) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    cell_ref = str(req.get("task_cell_ref") or "").replace("\\", "/").strip()
    event_id = str(req.get("event_id") or "").strip()
    claim_id = str(req.get("claim_id") or "").strip()
    if (
        not cell_ref.startswith("state/task_cells/")
        or not event_id.startswith(("terminal-planner-", "planner-user-query-"))
        or not claim_id.startswith("claim-")
    ):
        return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_IDENTITY_INVALID"}
    if operation not in {"delivered", "consumed", "cleaned"}:
        return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_OPERATION_INVALID"}

    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            cell = _read_json(store, base_sha, cell_ref)
            control = copy.deepcopy(cell.get("planner_control") or {})
            runtime = _runtime(control)
            event = copy.deepcopy(runtime.get("final_delivery") or {})
            if str(event.get("event_id") or "") != event_id or str(event.get("claim_id") or "") != claim_id:
                return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_CLAIM_MISMATCH"}
            state = str(event.get("state") or "")
            if operation == "cleaned":
                if event.get("cleanup_complete"):
                    return {"ok": True, "duplicate": True, "event": _public_final_delivery(cell_ref, event)}
                if event.get("kind") != "planner_final_delivery" or state not in {"CLAIMED", "DELIVERED"}:
                    return {"ok": False, "error": "PLANNER_CLEANUP_STATE_MISMATCH"}
                receipt = req.get("cleanup_receipt")
                if not isinstance(receipt, dict) or receipt.get("all_deleted") is not True:
                    return {"ok": False, "error": "PLANNER_CHAT_DELETION_UNCONFIRMED"}
                event["cleanup_complete"] = True
                event["cleanup_receipt"] = copy.deepcopy(receipt)
                event["cleaned_at"] = utc_now()
            elif operation == "delivered":
                if state in {"DELIVERED", "CONSUMED"}:
                    return {"ok": True, "duplicate": True, "event": _public_final_delivery(cell_ref, event)}
                if state != "CLAIMED":
                    return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_STATE_MISMATCH"}
                event["state"] = "DELIVERED"
                event["delivered_at"] = utc_now()
            else:
                if event.get("kind") == "planner_final_delivery" and not event.get("cleanup_complete"):
                    return {"ok": False, "error": "PLANNER_CHAT_CLEANUP_REQUIRED"}
                if state == "CONSUMED":
                    return {"ok": True, "duplicate": True, "event": _public_final_delivery(cell_ref, event)}
                if state != "DELIVERED":
                    return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_STATE_MISMATCH"}
                event["state"] = "CONSUMED"
                event["consumed_at"] = utc_now()
            new_cell = copy.deepcopy(cell)
            paths = [cell_ref]
            lane_state = None
            reset_state = None
            closed_task = None
            event_kind = str(event.get("kind") or "")
            if operation in {"cleaned", "consumed"} and event_kind == "planner_final_delivery":
                authority = copy.deepcopy(control.get("authority") or {})
                authority["semantic_authority"] = False
                authority["authority_revoked"] = True
                authority["revoked_at"] = event.get("consumed_at") or event.get("cleaned_at")
                control["authority"] = authority
                control["enabled"] = False
                control["semantic_authority_closed"] = True
                terminal_activity = "ERROR" if str(event.get("terminal_status") or "") == "ERROR" else "DONE"
                control["activity"] = terminal_activity
                control["wait"] = None
                control["inbox"] = {"events": {}, "active_doorbell": None}
                control["successor"] = {
                    "state": "NONE",
                    "handoff_id": None,
                    "from_generation": None,
                    "to_generation": None,
                    "packet_ref": None,
                    "candidate_conversation_id": None,
                    "candidate_request_id": None,
                    "candidate_challenge": None,
                    "pending_fence_token": None,
                }
                if operation == "consumed":
                    control.pop("runtime", None)
                else:
                    control["runtime"] = {"final_delivery": event}
                new_cell["status"] = terminal_activity
                new_cell["roles"] = {}

                lane_state = _read_json(store, base_sha, "state/lanes.json")
                owner_key = f"{cell.get('task_id')}::{int(cell.get('control_epoch') or 0)}"
                for lane in lane_state.get("lanes") or []:
                    if not isinstance(lane, dict):
                        continue
                    pools = lane.get("task_pools")
                    pool = pools.get(owner_key) if isinstance(pools, dict) else None
                    if not isinstance(pool, dict):
                        continue
                    pools.pop(owner_key, None)
                lane_state["updated_at"] = event.get("consumed_at") or event.get("cleaned_at")
                paths.append("state/lanes.json")
                # Reset only this task; preserve unrelated paused/current work.
                reset_state = _read_json(store, base_sha, "state/chatgpt.json")
                if reset_state.get("active_task") == cell.get("task_id"):
                    reset_state.update(active_task=None, active_action=None, active_dispatch_ref=None,
                                       handoff_packet_ref=None, phase="IDLE",
                                       next_reads=[], next_action="Await the next current user task.",
                                       writeback_reason=f"Terminal reset completed for {cell['task_id']}; task authority closed.")
                request = reset_state.get("control_request") or {}
                if request.get("task_id") == cell.get("task_id"):
                    reset_state["control_request"] = None
                paths.append("state/chatgpt.json")
                task_ref = _task_contract_ref(str(cell["task_id"]))
                closed_task = _try_read_json(store, base_sha, task_ref)
                if closed_task is not None:
                    closed_task["status"] = terminal_activity
                    paths.append(task_ref)
            else:
                runtime["final_delivery"] = event
                control["runtime"] = runtime

            new_cell["planner_control"] = control
            worktree = _prepare_worktree(store, base_sha, f"planner-final-{operation}-{cell.get('task_id')}-{attempt}")
            try:
                if operation == "cleaned":
                    try:
                        archive = _archive_project_records(worktree, cell, event, base_sha)
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        return {"ok": False, "error": "PLANNER_RECORD_ARCHIVE_FAILED", "detail": str(exc)}
                    event["record_archive"] = archive
                    control["runtime"]["final_delivery"] = event
                    if archive.get("manifest_ref"):
                        _write_json(worktree, archive["manifest_ref"], archive)
                        paths.append(archive["manifest_ref"])
                    # Only exact tracked CAH cache files covered by the verified
                    # archive are unlinked in this disposable Git transaction.
                    for ref in archive["pruned_cache_refs"]:
                        (worktree / ref).unlink()
                        paths.append(ref)
                _write_json(worktree, cell_ref, new_cell)
                if lane_state is not None:
                    _write_json(worktree, "state/lanes.json", lane_state)
                if reset_state is not None:
                    _write_json(worktree, "state/chatgpt.json", reset_state)
                if closed_task is not None:
                    _write_json(worktree, task_ref, closed_task)
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        paths,
                        f"{operation.capitalize()} Planner final delivery {event_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_UPDATE_PUSH_FAILED"}
                return {
                    "ok": True,
                    "duplicate": False,
                    "event": _public_final_delivery(cell_ref, event),
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_FINAL_DELIVERY_UPDATE_RETRY_EXHAUSTED"}



def begin_foreground_planner_handoff(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    source_request_id = _safe_id(req.get("source_request_id"), "PLANNER_FOREGROUND_SOURCE_REQUEST_REQUIRED")
    project_key = str(req.get("task_cell_project_key") or "").strip()
    epoch = int(req.get("control_epoch") or 0)
    if not project_key.startswith("g-p-") or epoch < 1:
        return {"ok": False, "error": "PLANNER_FOREGROUND_HANDOFF_IDENTITY_INVALID"}

    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            control_req = state.get("control_request")
            if (
                not isinstance(control_req, dict)
                or str(control_req.get("request_id") or "") != source_request_id
                or str(control_req.get("kind") or "") != "task_cell_planner_foreground_handoff"
                or str(control_req.get("status") or "") != "PENDING"
            ):
                return {"ok": False, "error": "PLANNER_FOREGROUND_CONTROL_MISMATCH"}

            task_ref = _task_contract_ref(task_id)
            plan_ref = f"tasks/{task_id}.plan.json"
            task = _read_json(store, base_sha, task_ref)
            _read_json(store, base_sha, plan_ref)
            task_sha = store._git("rev-parse", f"{base_sha}:{task_ref}").stdout.strip()
            plan_sha = store._git("rev-parse", f"{base_sha}:{plan_ref}").stdout.strip()

            generation = 1
            planner_request_id = _stable_token("planner-foreground-takeover", {
                "task_id": task_id,
                "epoch": epoch,
                "source_request_id": source_request_id,
                "task_contract": task_sha,
                "plan": plan_sha,
            })
            challenge = _stable_token("planner-foreground-challenge", {
                "task_id": task_id,
                "epoch": epoch,
                "request_id": planner_request_id,
            })
            fence_token = _stable_token("planner-fence", {
                "task_id": task_id,
                "epoch": epoch,
                "generation": generation,
                "request_id": planner_request_id,
            })
            doorbell_id = _stable_token(
                "doorbell-initial",
                {"task_id": task_id, "epoch": epoch, "request_id": planner_request_id},
            )
            memory_ref = planner_semantic_memory_path(task_id)
            plan_note_ref = planner_plan_note_path(task_id)
            memory_entry_ref = planner_turn_memory_entry_path(task_id, doorbell_id)
            outcome_ref = planner_turn_outcome_path(task_id, doorbell_id)
            slot_rows, slot_bindings = _lane_turn_surfaces(
                store, base_sha, task_id=task_id, epoch=epoch,
                doorbell_id=doorbell_id, generation=generation, fence=fence_token, owned=[],
            )
            output_ref = outcome_ref
            current_ref = planner_current_path(task_id)
            current = make_planner_current(
                task_id=task_id,
                control_epoch=epoch,
                planner_generation=generation,
                planner_fence_token=fence_token,
                task_contract_ref=task_ref,
                task_contract_revision=int(task.get("task_contract_revision") or task.get("v") or 1),
                task_contract_blob_sha=task_sha,
                plan_ref=plan_ref,
                plan_blob_sha=plan_sha,
                written_at=utc_now(),
            )
            prompt = _planner_turn_prompt(
                task_id=task_id,
                doorbell={"doorbell_id": doorbell_id, "event_ids": []},
                control={"inbox": {"events": {}}},
                plan_ref=plan_ref,
                memory_ref=memory_ref,
                memory_entry_ref=memory_entry_ref,
                slots=slot_rows,
                outcome_ref=outcome_ref, git_branch=store.git_branch,
            )
            cell = {
                "v": 1,
                "task_cell_id": task_id,
                "task_id": task_id,
                "task_cell_project_key": project_key,
                "control_epoch": epoch,
                "status": "BOOTSTRAPPING",
                "roles": {},
                "updated_at": utc_now(),
            }
            state["control_request"] = {
                **control_req,
                "phase": "CREATE_INITIAL_PLANNER",
                "planner_request_id": planner_request_id,
                "planner_challenge": challenge,
                "planner_generation": generation,
                "planner_fence_token": fence_token,
                "required_output_ref": output_ref,
                "semantic_output_kind": "PLANNER_TURN",
                "prompt": prompt,
                "task_contract_blob_sha": task_sha,
                "plan_blob_sha": plan_sha,
                "initial_turn": {
                    "doorbell_id": doorbell_id,
                    "memory_entry_ref": memory_entry_ref,
                    "outcome_ref": outcome_ref,
                    "slots": slot_bindings,
                },
            }
            state["writeback_reason"] = f"Foreground handoff is creating generation-1 Planner for {task_id}."

            worktree = _prepare_worktree(store, base_sha, f"planner-foreground-create-{task_id}-{attempt}")
            try:
                _write_json(worktree, _task_cell_path(task_id), cell)
                _write_json(worktree, current_ref, current)
                if not (worktree / memory_ref).exists():
                    _write_text(worktree, memory_ref, "# Planner Memory\n")
                if not (worktree / plan_note_ref).exists():
                    _write_text(worktree, plan_note_ref, "# Planner Plan Note\n")
                _write_text(
                    worktree,
                    memory_entry_ref,
                    planner_turn_memory_entry_header(
                        entry_id=doorbell_id,
                        planner_generation=generation,
                    ),
                )
                for slot in slot_rows:
                    _write_json(worktree, str(slot["slot_ref"]), slot)
                _write_json(
                    worktree,
                    outcome_ref,
                    make_planner_turn_outcome(
                        task_id=task_id,
                        control_epoch=epoch,
                        doorbell_id=doorbell_id,
                        planner_generation=generation,
                        planner_fence_token=fence_token,
                    ),
                )
                _write_json(worktree, "state/chatgpt.json", state)
                initial_paths = [
                    _task_cell_path(task_id),
                    current_ref,
                    memory_ref,
                    plan_note_ref,
                    memory_entry_ref,
                    outcome_ref,
                    "state/chatgpt.json",
                ] + [str(slot["slot_ref"]) for slot in slot_rows]
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        initial_paths,
                        f"Create initial Planner turn for {task_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_FOREGROUND_CREATE_PUSH_FAILED"}
                return {
                    "ok": True,
                    "staged": True,
                    "create_planner": True,
                    "task_id": task_id,
                    "control_epoch": epoch,
                    "request_id": planner_request_id,
                    "challenge": challenge,
                    "planner_generation": generation,
                    "planner_fence_token": fence_token,
                    "required_output_ref": output_ref,
                    "prompt": prompt,
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_FOREGROUND_CREATE_RETRY_EXHAUSTED"}



def record_foreground_planner_create_attempt(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    source_request_id = _safe_id(req.get("source_request_id"), "PLANNER_FOREGROUND_SOURCE_REQUEST_REQUIRED")
    max_attempts = 5

    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            control_req = state.get("control_request")
            if (
                not isinstance(control_req, dict)
                or str(control_req.get("request_id") or "") != source_request_id
                or str(control_req.get("kind") or "") != "task_cell_planner_foreground_handoff"
                or str(control_req.get("status") or "") != "PENDING"
                or str(control_req.get("phase") or "") != "CREATE_INITIAL_PLANNER"
                or str(control_req.get("task_id") or "") != task_id
            ):
                return {"ok": False, "error": "PLANNER_FOREGROUND_ATTEMPT_CONTROL_MISMATCH"}

            current_attempts = int(control_req.get("create_attempts") or 0)
            if current_attempts >= max_attempts:
                failed = copy.deepcopy(control_req)
                failed["status"] = "ERROR"
                failed["error"] = "INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED"
                failed["create_attempts"] = current_attempts
                failed["max_create_attempts"] = max_attempts
                failed["failed_at"] = utc_now()
                state["control_request"] = failed
                state["writeback_reason"] = (
                    f"Initial Planner creation exhausted {current_attempts} attempts for {task_id}."
                )
                worktree = _prepare_worktree(store, base_sha, f"planner-foreground-attempt-exhausted-{task_id}-{attempt}")
                try:
                    _write_json(worktree, "state/chatgpt.json", state)
                    try:
                        commit_sha = _commit_push(
                            store,
                            worktree,
                            ["state/chatgpt.json"],
                            f"Stop exhausted initial Planner creation for {task_id} [skip ci]",
                        )
                    except subprocess.SubprocessError:
                        if attempt == 0:
                            continue
                        return {"ok": False, "error": "PLANNER_FOREGROUND_ATTEMPT_PUSH_FAILED"}
                    return {
                        "ok": False,
                        "error": "INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED",
                        "task_id": task_id,
                        "attempts": current_attempts,
                        "max_attempts": max_attempts,
                        "commit_sha": commit_sha,
                    }
                finally:
                    _drop_worktree(store, worktree)

            next_attempt = current_attempts + 1
            next_control = copy.deepcopy(control_req)
            next_control["create_attempts"] = next_attempt
            next_control["max_create_attempts"] = max_attempts
            next_control["last_create_attempt_at"] = utc_now()
            state["control_request"] = next_control
            state["writeback_reason"] = (
                f"Initial Planner creation attempt {next_attempt}/{max_attempts} for {task_id}."
            )
            worktree = _prepare_worktree(store, base_sha, f"planner-foreground-attempt-{task_id}-{attempt}")
            try:
                _write_json(worktree, "state/chatgpt.json", state)
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        ["state/chatgpt.json"],
                        f"Record initial Planner creation attempt {next_attempt} for {task_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_FOREGROUND_ATTEMPT_PUSH_FAILED"}
                return {
                    "ok": True,
                    "task_id": task_id,
                    "attempt": next_attempt,
                    "max_attempts": max_attempts,
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_FOREGROUND_ATTEMPT_RETRY_EXHAUSTED"}


def complete_foreground_planner_handoff_binding(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    source_request_id = _safe_id(req.get("source_request_id"), "PLANNER_FOREGROUND_SOURCE_REQUEST_REQUIRED")
    planner_request_id = _safe_id(req.get("planner_request_id"), "PLANNER_FOREGROUND_PLANNER_REQUEST_REQUIRED")
    conversation_id = _safe_id(req.get("conversation_id"), "PLANNER_FOREGROUND_CONVERSATION_REQUIRED")
    conversation_url = str(req.get("conversation_url") or "").strip()
    project_key = str(req.get("task_cell_project_key") or "").strip()

    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            control_req = state.get("control_request")
            if (
                not isinstance(control_req, dict)
                or str(control_req.get("request_id") or "") != source_request_id
                or str(control_req.get("kind") or "") != "task_cell_planner_foreground_handoff"
                or str(control_req.get("status") or "") != "PENDING"
                or str(control_req.get("phase") or "") != "CREATE_INITIAL_PLANNER"
                or str(control_req.get("planner_request_id") or "") != planner_request_id
            ):
                return {"ok": False, "error": "PLANNER_FOREGROUND_BIND_CONTROL_MISMATCH"}

            cell = _read_json(store, base_sha, _task_cell_path(task_id))
            epoch = int(cell.get("control_epoch") or 0)
            task_ref = _task_contract_ref(task_id)
            plan_ref = f"tasks/{task_id}.plan.json"
            task = _read_json(store, base_sha, task_ref)
            task_sha = str(control_req.get("task_contract_blob_sha") or "")
            plan_sha = str(control_req.get("plan_blob_sha") or "")
            challenge = str(control_req.get("planner_challenge") or "")
            fence_token = str(control_req.get("planner_fence_token") or "")
            output_ref = str(control_req.get("required_output_ref") or "")

            initial_turn = control_req.get("initial_turn")
            if not isinstance(initial_turn, dict):
                return {"ok": False, "error": "PLANNER_INITIAL_TURN_MISSING"}

            bound_cell = copy.deepcopy(cell)
            bootstrap_runtime = _runtime(bound_cell.pop("planner_control", {})) if cell.get("bootstrap_recovery") else None
            bound_cell["roles"] = {
                **(bound_cell.get("roles") or {}),
                "planner": {
                    "role": "planner",
                    "status": "ACTIVE",
                    "semantic_ready": True,
                    "request_id": planner_request_id,
                    "challenge": challenge,
                    "conversation_id": conversation_id,
                    "conversation_url": conversation_url,
                    "response_started": True,
                    "observed_at": utc_now(),
                }
            }
            control_cell = migrate_flat_planner_binding(
                bound_cell,
                admission_ok=True,
                task_contract_ref=task_ref,
                task_contract_revision=int(task.get("task_contract_revision") or task.get("v") or 1),
                task_contract_blob_sha=task_sha,
                plan_ref=plan_ref,
                plan_blob_sha=plan_sha,
            )
            control = control_cell["planner_control"]
            control["authority"]["planner_fence_token"] = fence_token
            control_cell["roles"]["planner"]["planner_fence_token"] = fence_token
            control["activity"] = "WAKING"
            control["migration"]["created_new_conversation"] = True
            control["inbox"]["active_doorbell"] = {
                "doorbell_id": str(initial_turn.get("doorbell_id") or ""),
                "event_ids": [],
                "planner_generation": 1,
                "planner_fence_token": fence_token,
                "delivery_state": "CLAIMED",
            }
            runtime = _runtime(control)
            if bootstrap_runtime is not None:
                runtime["pending_role_requests"] = bootstrap_runtime.get("pending_role_requests", [])
                runtime["pending_role_outputs"] = bootstrap_runtime.get("pending_role_outputs", [])
                control_cell["bootstrap_recovery"]["state"] = "BOUND"
            timeout = int(runtime.get("semantic_output_timeout_seconds") or DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS)
            runtime.update({
                "foreground_runtime_dependency": False,
                "authority_mode": "PLANNER",
                "prompt_count": 1,
                "generation_started_at": utc_now(),
                "last_response_started_at": utc_now(),
                "pending_output": {
                    "kind": "PLANNER_TURN",
                    "ref": output_ref,
                    "request_id": planner_request_id,
                    "conversation_id": conversation_id,
                    "status": "WAITING",
                    "started_at": utc_now(),
                    "deadline_at": _iso_after(timeout),
                    "wake_prompt": str(control_req.get("prompt") or ""),
                },
                "active_turn": copy.deepcopy(initial_turn),
                "foreground_handoff": {
                    "state": "COMPLETE",
                    "request_id": planner_request_id,
                    "required_output_ref": output_ref,
                    "completed_at": utc_now(),
                },
            })
            control["runtime"] = runtime
            control_cell["planner_control"] = control
            control_cell["status"] = "ACTIVE"
            control_cell["updated_at"] = utc_now()

            state["control_request"] = {
                **control_req,
                "status": "DONE",
                "completed_at": utc_now(),
                "result": {
                    "task_id": task_id,
                    "control_epoch": epoch,
                    "role": "planner",
                    "conversation_id": conversation_id,
                    "planner_generation": 1,
                    "planner_fence_token": fence_token,
                    "response_started": True,
                    "semantic_output_ref": output_ref,
                },
            }

            worktree = _prepare_worktree(store, base_sha, f"planner-foreground-bind-{task_id}-{attempt}")
            try:
                _write_json(worktree, _task_cell_path(task_id), control_cell)
                _write_json(worktree, "state/chatgpt.json", state)
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        [_task_cell_path(task_id), "state/chatgpt.json"],
                        f"Bind initial Planner for {task_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_FOREGROUND_BIND_PUSH_FAILED"}
                return {
                    "ok": True,
                    "completed": True,
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "planner_generation": 1,
                    "planner_fence_token": fence_token,
                    "required_output_ref": output_ref,
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_FOREGROUND_BIND_RETRY_EXHAUSTED"}

def request_planner_rotation(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    reason = str(req.get("reason") or "").strip()
    if reason not in {"context_compacted", "broken_binding", "explicit_lifecycle_rollover"}:
        return {"ok": False, "error": "PLANNER_HANDOFF_REASON_INVALID"}
    caller_role = str(req.get("caller_role") or "").strip().lower()
    if caller_role not in {"planner", "helper", "harness", "foreground"}:
        return {"ok": False, "error": "PLANNER_ROTATION_CALLER_INVALID"}

    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            if caller_role == "foreground":
                task = _read_json(store, base_sha, _task_contract_ref(task_id))
                if not _canonical_break_glass_allowed(task):
                    return {"ok": False, "error": "FOREGROUND_ROTATION_BREAK_GLASS_NOT_CANONICAL"}
            if not _control_slot_available(state):
                return {"ok": True, "staged": False, "idle": "control_slot_busy"}
            cell = _read_json(store, base_sha, _task_cell_path(task_id))
            current = _read_json(store, base_sha, planner_current_path(task_id))
            now_text = utc_now()

            old_active = copy.deepcopy(_runtime(cell.get("planner_control") or {}).get("active_turn") or {})
            if reason in {"broken_binding", "context_compacted", "explicit_lifecycle_rollover"}:
                cell = copy.deepcopy(cell)
                control0 = copy.deepcopy(cell.get("planner_control") or {})
                inbox0 = control0.get("inbox") if isinstance(control0.get("inbox"), dict) else {}
                active0 = inbox0.get("active_doorbell") if isinstance(inbox0, dict) else None
                events0 = inbox0.get("events") if isinstance(inbox0.get("events"), dict) else {}
                for event_id in [str(x) for x in (active0 or {}).get("event_ids") or []]:
                    event = events0.get(event_id)
                    if isinstance(event, dict) and str(event.get("state") or "") == "CLAIMED":
                        event["state"] = "PENDING"
                        event["claim"] = None
                inbox0["events"] = events0
                inbox0["active_doorbell"] = None
                control0["inbox"] = inbox0
                runtime0 = _runtime(control0)
                runtime0["pending_output"] = None
                runtime0["active_turn"] = None
                control0["runtime"] = runtime0
                control0["activity"] = "ACTIVE"
                cell["planner_control"] = control0

            try:
                new_cell, new_current, sealed, handoff_id, pending_fence, handoff_ref = _stage_rotation_in_memory(
                    cell=cell,
                    current=current,
                    reason=reason,
                    now_text=now_text,
                )
            except (PlannerControlError, PlannerMemoryError, PlannerRuntimeError) as exc:
                return {"ok": False, "error": getattr(exc, "code", str(exc))}
            control = new_cell["planner_control"]
            successor = control["successor"]
            generation_ref = planner_generation_path(task_id, int(successor["from_generation"]))
            authority = control["authority"]
            planner = _role_binding(cell, "planner")
            successor_request_id = _stable_token("planner-successor", {
                "task_id": task_id,
                "epoch": cell["control_epoch"],
                "handoff_id": handoff_id,
            })
            successor_challenge = _stable_token("planner-successor-challenge", {
                "task_id": task_id,
                "handoff_id": handoff_id,
            })
            # The first successor message IS its semantic turn, never a binding ACK.
            new_generation = int(successor["to_generation"])
            doorbell_id = _stable_token("doorbell-successor", {"task": task_id, "handoff": handoff_id})
            inbox = control.setdefault("inbox", {"events": {}, "active_doorbell": None})
            event_ids = [key for key, value in inbox.get("events", {}).items() if value.get("state") == "PENDING"]
            doorbell = {"doorbell_id": doorbell_id, "event_ids": event_ids,
                "planner_generation": new_generation, "planner_fence_token": pending_fence,
                "delivery_state": "CLAIMED"}
            inbox["active_doorbell"] = doorbell
            for key in event_ids:
                inbox["events"][key]["state"] = "CLAIMED"
                inbox["events"][key]["claim"] = {**doorbell, "claimed_at": now_text}
            rt = _runtime(control)
            slot_rows, slot_bindings = _lane_turn_surfaces(store, base_sha,
                task_id=task_id, epoch=int(cell["control_epoch"]), doorbell_id=doorbell_id,
                generation=new_generation, fence=pending_fence, owned=list(rt.get("owned_children") or []))
            handoff_appends = {}
            memory_ref = planner_semantic_memory_path(task_id)
            if old_active.get("memory_entry_ref"):
                handoff_appends[memory_ref] = store._git("show", f"{base_sha}:{old_active['memory_entry_ref']}").stdout
            # Commit prior review prose before retiring its author. Unapplied directions
            # survive in the successor's pre-bound positions, without dispatching yet.
            for binding in old_active.get("slots") or []:
                old_slot = _try_read_json(store, base_sha, str(binding.get("slot_ref") or "")) or {}
                if not planner_slot_has_write(old_slot):
                    continue
                kind = old_slot.get("entry_type")
                if kind in {"DIRECTION", "REVIEW_DIRECTION"}:
                    for slot in slot_rows:
                        if slot.get("lane_id") == binding.get("lane_id"):
                            slot["entry_type"] = kind
                            slot["semantic"] = copy.deepcopy(old_slot.get("semantic"))
                reply_ref = binding.get("bound_child_reply_ref")
                if reply_ref:
                    handoff_appends[reply_ref] = handoff_appends.get(reply_ref, "") + render_child_reply_entry(
                        entry_id=_stable_token("handoff-review", {"handoff": handoff_id, "slot": binding.get("slot_index")}),
                        writer_role="planner", entry_type=kind, semantic=old_slot.get("semantic"))
            memory_entry_ref = planner_turn_memory_entry_path(task_id, doorbell_id)
            outcome_ref = planner_turn_outcome_path(task_id, doorbell_id)
            rt["pending_output"] = {"kind": "PLANNER_TURN", "ref": outcome_ref,
                "request_id": successor_request_id, "status": "DELIVERY_PENDING"}
            rt["active_turn"] = {"doorbell_id": doorbell_id, "memory_entry_ref": memory_entry_ref,
                "outcome_ref": outcome_ref, "slots": slot_bindings}
            control["runtime"] = rt
            prompt = _planner_turn_prompt(task_id=task_id, doorbell=doorbell, control=control,
                plan_ref=f"tasks/{task_id}.plan.json", memory_ref=memory_ref,
                memory_entry_ref=memory_entry_ref, slots=slot_rows, outcome_ref=outcome_ref,
                git_branch=store.git_branch)
            _set_control_request(
                state,
                kind="task_cell_planner_successor_bootstrap",
                request_id=successor_request_id,
                task_id=task_id,
                epoch=int(cell["control_epoch"]),
                role="planner",
                challenge=successor_challenge,
                project_key=str(authority.get("project_key") or planner.get("project_key") or cell.get("task_cell_project_key") or ""),
                prompt=prompt,
                extra={
                    "handoff_id": handoff_id,
                    "to_generation": int(successor["to_generation"]),
                    "pending_fence_token": pending_fence,
                    "predecessor_conversation_id": str(authority.get("conversation_id") or ""),
                    "transport_bootstrap_only": False,
                    "required_output_ref": outcome_ref,
                },
            )
            handoff = prepare_planner_handoff(
                new_current,
                sealed,
                handoff_id=handoff_id,
                reason=reason,
                pending_successor_fence_token=pending_fence,
                current_memory_ref=planner_current_path(task_id),
                current_memory_blob_sha=memory_blob_sha(new_current),
                sealed_generation_ref=generation_ref,
                sealed_generation_blob_sha=memory_blob_sha(sealed),
                canonical_task_cell_ref=_task_cell_path(task_id),
                canonical_task_cell_blob_sha=_stable_token("cell-snapshot", cell),
                predecessor_project_key=str(authority.get("project_key") or cell.get("task_cell_project_key") or ""),
                predecessor_conversation_id=str(authority.get("conversation_id") or ""),
            )
            # _stage_rotation_in_memory validates the same packet shape; materialize this exact packet.
            worktree = _prepare_worktree(store, base_sha, f"planner-rotate-{task_id}-{attempt}")
            try:
                _write_json(worktree, _task_cell_path(task_id), new_cell)
                _write_json(worktree, planner_current_path(task_id), new_current)
                _write_json(worktree, generation_ref, sealed)
                _write_json(worktree, handoff_ref, handoff)
                _write_json(worktree, "state/chatgpt.json", state)
                paths = [_task_cell_path(task_id), planner_current_path(task_id), generation_ref, handoff_ref, "state/chatgpt.json"]
                for path, value in handoff_appends.items():
                    _append_text(worktree, path, value, header="# Planner Memory\n" if path == memory_ref else None)
                    paths.append(path)
                _write_text(worktree, memory_entry_ref, planner_turn_memory_entry_header(entry_id=doorbell_id, planner_generation=new_generation))
                _write_json(worktree, outcome_ref, make_planner_turn_outcome(task_id=task_id,
                    control_epoch=int(cell["control_epoch"]), doorbell_id=doorbell_id,
                    planner_generation=new_generation, planner_fence_token=pending_fence))
                paths.extend([memory_entry_ref, outcome_ref])
                for slot in slot_rows:
                    _write_json(worktree, slot["slot_ref"], slot)
                    paths.append(slot["slot_ref"])
                try:
                    commit_sha = _commit_push(store, worktree, paths, f"Stage Planner successor for {task_id} [skip ci]")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_ROTATION_PUSH_FAILED"}
                return {
                    "ok": True,
                    "staged": True,
                    "task_id": task_id,
                    "handoff_id": handoff_id,
                    "to_generation": int(successor["to_generation"]),
                    "pending_fence_token": pending_fence,
                    "request_id": successor_request_id,
                    "transport_bootstrap_only": False,
                    "required_output_ref": outcome_ref,
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_ROTATION_RETRY_EXHAUSTED"}


def complete_planner_successor_bootstrap(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    request_id = _safe_id(req.get("request_id"), "PLANNER_SUCCESSOR_REQUEST_ID_REQUIRED")
    conversation_id = _safe_id(req.get("conversation_id"), "PLANNER_SUCCESSOR_CONVERSATION_REQUIRED")
    project_key = str(req.get("task_cell_project_key") or "").strip()
    if not project_key.startswith("g-p-"):
        return {"ok": False, "error": "PLANNER_SUCCESSOR_PROJECT_INVALID"}
    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            control_req = state.get("control_request")
            if not isinstance(control_req, dict) or str(control_req.get("request_id") or "") != request_id:
                return {"ok": False, "error": "PLANNER_SUCCESSOR_CONTROL_MISMATCH"}
            if str(control_req.get("kind") or "") != "task_cell_planner_successor_bootstrap":
                return {"ok": False, "error": "PLANNER_SUCCESSOR_CONTROL_KIND_MISMATCH"}
            cell = _read_json(store, base_sha, _task_cell_path(task_id))
            planner_control = copy.deepcopy(cell.get("planner_control") or {})
            successor = planner_control.get("successor") or {}
            handoff_id = str(control_req.get("handoff_id") or successor.get("handoff_id") or "")
            if not handoff_id:
                return {"ok": False, "error": "PLANNER_SUCCESSOR_HANDOFF_MISSING"}
            handoff_ref = planner_handoff_path(task_id, handoff_id)
            handoff = _read_json(store, base_sha, handoff_ref)
            current = _read_json(store, base_sha, planner_current_path(task_id))
            try:
                bound_control = bind_planner_successor(
                    planner_control,
                    handoff_id=handoff_id,
                    candidate_conversation_id=conversation_id,
                    candidate_request_id=request_id,
                    candidate_challenge=str(control_req.get("challenge") or ""),
                    packet_ref=handoff_ref,
                )
                bound_handoff = record_successor_binding(
                    handoff,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    challenge=str(control_req.get("challenge") or ""),
                    project_key=project_key,
                )
                bundle = promote_planner_authority_with_memory(
                    current,
                    bound_handoff,
                    bound_control,
                    expected_memory_version=int(current["memory_version"]),
                    promotion_ref=f"replacement-route:{conversation_id}",
                    promoted_at=utc_now(),
                )
            except (PlannerControlError, PlannerMemoryError) as exc:
                return {"ok": False, "error": getattr(exc, "code", str(exc))}

            new_control = bundle["planner_control"]
            runtime = _runtime(new_control)
            if isinstance(runtime.get("pending_output"), dict):
                runtime["pending_output"].update(status="WAITING", conversation_id=conversation_id)
            runtime["browser_promotion_complete"] = False
            new_control["runtime"] = runtime
            new_cell = copy.deepcopy(cell)
            new_cell["planner_control"] = new_control
            roles = new_cell.setdefault("roles", {})
            planner = copy.deepcopy(roles.get("planner") or {})
            planner["status"] = "FENCED_ROTATING"
            planner["semantic_ready"] = False
            planner["successor"] = {
                "status": "ROUTE_BOUND_PENDING_BROWSER_PROMOTION",
                "conversation_id": conversation_id,
                "request_id": request_id,
                "challenge": str(control_req.get("challenge") or ""),
                "planner_generation": int(new_control["authority"].get("planner_generation") or 0),
                "planner_fence_token": str(new_control["authority"].get("planner_fence_token") or ""),
                "handoff_id": handoff_id,
                "semantic_authority": False,
            }
            roles["planner"] = planner

            state["control_request"] = {
                **control_req,
                "status": "DONE",
                "completed_at": utc_now(),
                "result": {
                    "task_id": task_id,
                    "control_epoch": int(cell.get("control_epoch") or 0),
                    "role": "planner",
                    "conversation_id": conversation_id,
                    "handoff_id": handoff_id,
                    "response_started": True,
                    "transport_bootstrap_only": False,
                },
            }
            promote_request_id = _stable_token(
                "planner-promote",
                {"task_id": task_id, "handoff_id": handoff_id, "conversation": conversation_id},
            )
            _set_control_request(
                state,
                kind="task_cell_planner_successor_promote",
                request_id=promote_request_id,
                task_id=task_id,
                epoch=int(cell["control_epoch"]),
                role="planner",
                challenge=str(new_control["authority"].get("challenge") or ""),
                project_key=project_key,
                extra={
                    "handoff_id": handoff_id,
                    "conversation_id": conversation_id,
                    "to_generation": int(new_control["authority"].get("planner_generation") or 0),
                    "planner_fence_token": str(new_control["authority"].get("planner_fence_token") or ""),
                    "promotion_owner": "HARNESS",
                },
            )
            worktree = _prepare_worktree(store, base_sha, f"planner-replacement-bind-{task_id}-{attempt}")
            try:
                _write_json(worktree, _task_cell_path(task_id), new_cell)
                _write_json(worktree, planner_current_path(task_id), bundle["current"])
                _write_json(worktree, handoff_ref, bundle["handoff"])
                _write_json(worktree, "state/chatgpt.json", state)
                try:
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        [_task_cell_path(task_id), planner_current_path(task_id), handoff_ref, "state/chatgpt.json"],
                        f"Bind replacement Planner authority for {task_id} [skip ci]",
                    )
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_SUCCESSOR_BIND_PUSH_FAILED"}
                return {
                    "ok": True,
                    "completed": True,
                    "handoff_id": handoff_id,
                    "conversation_id": conversation_id,
                    "planner_generation": int(new_control["authority"].get("planner_generation") or 0),
                    "planner_fence_token": str(new_control["authority"].get("planner_fence_token") or ""),
                    "promotion_request_id": promote_request_id,
                    "commit_sha": commit_sha,
                }
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_SUCCESSOR_BIND_RETRY_EXHAUSTED"}

def complete_planner_successor_promote(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    request_id = _safe_id(req.get("request_id"), "PLANNER_PROMOTE_REQUEST_ID_REQUIRED")
    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            control_req = state.get("control_request")
            if not isinstance(control_req, dict) or str(control_req.get("request_id") or "") != request_id:
                return {"ok": False, "error": "PLANNER_PROMOTE_CONTROL_MISMATCH"}
            if str(control_req.get("kind") or "") != "task_cell_planner_successor_promote":
                return {"ok": False, "error": "PLANNER_PROMOTE_CONTROL_KIND_MISMATCH"}
            if str(control_req.get("status") or "") == "DONE":
                return {"ok": True, "completed": False, "idle": "already_done"}
            cell = _read_json(store, base_sha, _task_cell_path(task_id))
            control = copy.deepcopy(cell.get("planner_control") or {})
            authority = control.get("authority") or {}
            expected_conversation = str(control_req.get("conversation_id") or "")
            if str(authority.get("conversation_id") or "") != expected_conversation:
                return {"ok": False, "error": "PLANNER_PROMOTE_AUTHORITY_MISMATCH"}
            runtime = _runtime(control)
            runtime["browser_promotion_complete"] = True
            runtime["generation_started_at"] = utc_now()
            runtime["prompt_count"] = 0
            runtime["last_response_started_at"] = utc_now()
            control["runtime"] = runtime
            new_cell = copy.deepcopy(cell)
            new_cell["planner_control"] = control
            roles = new_cell.setdefault("roles", {})
            planner = copy.deepcopy(roles.get("planner") or {})
            successor_record = planner.get("successor") if isinstance(planner.get("successor"), dict) else {}
            planner.update({
                "conversation_id": expected_conversation,
                "request_id": str(authority.get("request_id") or ""),
                "challenge": str(authority.get("challenge") or ""),
                "planner_generation": int(authority.get("planner_generation") or 0),
                "planner_fence_token": str(authority.get("planner_fence_token") or ""),
                "status": "BOUND_ROTATING",
                "semantic_ready": False,
                "successor": successor_record,
            })
            roles["planner"] = planner
            state["control_request"] = {
                **control_req,
                "status": "DONE",
                "completed_at": utc_now(),
                "result": {"task_id": task_id, "conversation_id": expected_conversation, "promoted": True},
            }
            state["writeback_reason"] = f"Activated replacement Planner browser binding for {task_id}."
            worktree = _prepare_worktree(store, base_sha, f"planner-promote-complete-{task_id}-{attempt}")
            try:
                _write_json(worktree, _task_cell_path(task_id), new_cell)
                _write_json(worktree, "state/chatgpt.json", state)
                try:
                    commit_sha = _commit_push(store, worktree, [_task_cell_path(task_id), "state/chatgpt.json"], f"Complete Planner successor promotion for {task_id} [skip ci]")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_PROMOTE_COMPLETE_PUSH_FAILED"}
                return {"ok": True, "completed": True, "commit_sha": commit_sha}
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_PROMOTE_COMPLETE_RETRY_EXHAUSTED"}


def complete_planner_predecessor_retire(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(req.get("task_id"))
    request_id = _safe_id(req.get("request_id"), "PLANNER_RETIRE_REQUEST_ID_REQUIRED")
    deleted = bool(req.get("deleted"))
    for attempt in range(2):
        with store.git_lock:
            store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
            base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
            state = _read_json(store, base_sha, "state/chatgpt.json")
            control_req = state.get("control_request")
            if not isinstance(control_req, dict) or str(control_req.get("request_id") or "") != request_id:
                return {"ok": False, "error": "PLANNER_RETIRE_CONTROL_MISMATCH"}
            if str(control_req.get("kind") or "") != "task_cell_planner_predecessor_retire":
                return {"ok": False, "error": "PLANNER_RETIRE_CONTROL_KIND_MISMATCH"}
            handoff_id = str(control_req.get("handoff_id") or "")
            handoff_ref = planner_handoff_path(task_id, handoff_id)
            handoff = _read_json(store, base_sha, handoff_ref)
            cell = _read_json(store, base_sha, _task_cell_path(task_id))
            control = copy.deepcopy(cell.get("planner_control") or {})
            expected_project = str(control_req.get("task_cell_project_key") or "")
            expected_conversation = str(control_req.get("conversation_id") or "")
            if str(handoff.get("predecessor_project_key") or "") != expected_project or str(handoff.get("predecessor_conversation_id") or "") != expected_conversation:
                return {"ok": False, "error": "PLANNER_RETIRE_IDENTITY_MISMATCH"}
            status = "RETIRED" if deleted else "RETIREMENT_ERROR"
            new_handoff = record_predecessor_retirement_result(
                handoff,
                status=status,
                error=None if deleted else str(req.get("error") or "conversation_delete_failed"),
            )
            if not deleted:
                # A failed browser deletion is not a completed rotation. Keep
                # the same request and predecessor binding available to retry
                # or operational recovery; do not release pending semantic work.
                state["control_request"] = {
                    **control_req, "status": "PENDING",
                    "last_error": str(req.get("error") or "conversation_delete_failed"),
                    "last_attempt_at": utc_now(),
                }
                worktree = _prepare_worktree(store, base_sha, f"planner-retire-failed-{task_id}-{attempt}")
                try:
                    _write_json(worktree, handoff_ref, new_handoff)
                    _write_json(worktree, "state/chatgpt.json", state)
                    commit_sha = _commit_push(store, worktree, [handoff_ref, "state/chatgpt.json"], f"Preserve failed Planner retirement for {task_id} [skip ci]")
                finally:
                    _drop_worktree(store, worktree)
                return {"ok": False, "completed": False, "error": "PLANNER_PREDECESSOR_DELETE_FAILED", "status": status, "commit_sha": commit_sha}
            successor = control.get("successor") or {}
            control["successor"] = {
                **successor,
                "state": "NONE",
                "handoff_id": None,
                "from_generation": None,
                "to_generation": None,
                "packet_ref": None,
                "candidate_conversation_id": None,
                "candidate_request_id": None,
                "candidate_challenge": None,
                "pending_fence_token": None,
                "takeover_ref": None,
            }
            runtime = _runtime(control)
            runtime["last_rotation_completed_at"] = utc_now()
            runtime["rotation_reason"] = None
            runtime["rotation_started_at"] = None
            runtime["browser_promotion_complete"] = True
            control["runtime"] = runtime
            control["activity"] = "ACTIVE"
            new_cell = copy.deepcopy(cell)
            new_cell["planner_control"] = control
            roles = new_cell.setdefault("roles", {})
            planner = copy.deepcopy(roles.get("planner") or {})
            planner.pop("successor", None)
            planner["status"] = "ACTIVE"
            planner["semantic_ready"] = True
            roles["planner"] = planner
            state["control_request"] = {
                **control_req,
                "status": "DONE" if deleted else "ERROR",
                "completed_at": utc_now(),
                "result": {
                    "task_id": task_id,
                    "handoff_id": handoff_id,
                    "conversation_id": expected_conversation,
                    "deleted": deleted,
                    "error": None if deleted else str(req.get("error") or "conversation_delete_failed"),
                },
            }
            state["writeback_reason"] = f"Planner predecessor retirement {'completed' if deleted else 'failed'} for {task_id}."
            worktree = _prepare_worktree(store, base_sha, f"planner-retire-complete-{task_id}-{attempt}")
            try:
                _write_json(worktree, handoff_ref, new_handoff)
                _write_json(worktree, _task_cell_path(task_id), new_cell)
                _write_json(worktree, "state/chatgpt.json", state)
                try:
                    commit_sha = _commit_push(store, worktree, [handoff_ref, _task_cell_path(task_id), "state/chatgpt.json"], f"Record Planner predecessor retirement for {task_id} [skip ci]")
                except subprocess.SubprocessError:
                    if attempt == 0:
                        continue
                    return {"ok": False, "error": "PLANNER_RETIRE_COMPLETE_PUSH_FAILED"}
                return {"ok": deleted, "completed": True, "status": status, "commit_sha": commit_sha}
            finally:
                _drop_worktree(store, worktree)
    return {"ok": False, "error": "PLANNER_RETIRE_COMPLETE_RETRY_EXHAUSTED"}


def _list_task_cells(store: Any, ref: str) -> list[str]:
    try:
        shown = store._git("ls-tree", "-r", "--name-only", ref, TASK_CELL_ROOT).stdout
    except subprocess.SubprocessError:
        return []
    return sorted(
        line.strip() for line in shown.splitlines()
        if line.strip().startswith(TASK_CELL_ROOT + "/") and line.strip().endswith(".json")
    )


def _artifact_exists(store: Any, ref: str, path: str) -> bool:
    try:
        store._git("cat-file", "-e", f"{ref}:{path}")
        return True
    except subprocess.SubprocessError:
        return False





def _recover_stalled_initial_planner(store: Any, state: dict[str, Any], base_sha: str) -> dict[str, Any] | None:
    """Bootstrap also needs a watchdog, before any Planner authority exists."""
    request = state.get("control_request") or {}
    if (request.get("kind") != "task_cell_planner_foreground_handoff"
            or request.get("phase") != "CREATE_INITIAL_PLANNER"
            or request.get("status") not in {"PENDING", "APPLYING", "ERROR"}
            or (request.get("status") == "ERROR" and request.get("error") != "INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED")):
        return None
    task_id = _safe_task_id(request.get("task_id"))
    cell_ref = _task_cell_path(task_id)
    cell = _try_read_json(store, base_sha, cell_ref) or {}
    task = _try_read_json(store, base_sha, _task_contract_ref(task_id)) or {}
    started = _parse_time(request.get("requested_at"))
    if (cell.get("status") != "BOOTSTRAPPING" or cell.get("bootstrap_recovery")
            or int(cell.get("control_epoch") or 0) != int(request.get("control_epoch") or 0)
            or (cell.get("planner_control") or {}).get("authority")
            or task.get("status") in {"PAUSED_BY_USER", "PAUSED", "CANCELLED", "STOPPED", "DONE"}
            or not request.get("planner_request_id") or not request.get("prompt")
            or started is None):
        return None
    timeout = int(request.get("semantic_output_timeout_seconds") or DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS)
    if request.get("status") != "ERROR" and _now_dt() < started + timedelta(seconds=max(1, timeout)):
        return None
    evidence_ref = f"evidence/{task_id}/recovery/{_stable_token('bootstrap-timeout', {'request_id': request['request_id']})}.json"
    helper_id, helper_ref = _planner_helper_identity(task_id, evidence_ref)
    recovery = {"kind": "INITIAL_PLANNER_BOOTSTRAP", "request_id": request["planner_request_id"],
                "output_ref": request["required_output_ref"], "prompt": request["prompt"],
                "project_key": cell["task_cell_project_key"]}
    new_cell, new_state = copy.deepcopy(cell), copy.deepcopy(state)
    new_cell["bootstrap_recovery"] = {"state": "WAITING_HELPER", "helper_request_id": helper_id,
                                      "evidence_ref": evidence_ref, "control_request": copy.deepcopy(request)}
    new_state["control_request"] = {**request, "status": "ERROR", "recovery_ref": evidence_ref}
    # This is an incident mailbox, not a fabricated Planner authority or turn.
    control = {"enabled": False, "runtime": {}}
    runtime = _runtime(control)
    runtime["pending_role_requests"].append({
        "role": "helper", "kind": "helper_result", "request_id": helper_id,
        "challenge": _stable_token("helper-challenge", {"incident": evidence_ref}),
        "artifact_ref": helper_ref, "state": "REQUEST_PENDING", "decision_ref": evidence_ref,
        "recovery": recovery,
        "prompt": f"Initial Planner bootstrap did not reach a verified conversation binding. Inspect {evidence_ref}, the retained original control request, and only the browser evidence needed to distinguish still-running work from a missed/stopped delivery. If the original request should be retried, restore that same retained control request/event to its runnable state so Harness re-delivers it mechanically; do not create or rotate Planner and do not rewrite the prompt. Verify the resulting response-start/binding state, then record diagnosis/repair_result."
    })
    runtime, _ = _stage_pending_helper_request(new_state, new_cell, runtime, store.git_branch)
    control["runtime"] = runtime
    new_cell["planner_control"] = control
    evidence = {"v": 1, "code": "INITIAL_PLANNER_BOOTSTRAP_STALLED", "task_id": task_id,
                "control_epoch": cell["control_epoch"], "observed_at": utc_now(),
                "source_commit": base_sha, "request": request, "recovery": recovery}
    worktree = _prepare_worktree(store, base_sha, f"planner-bootstrap-helper-{task_id}")
    try:
        objects = {evidence_ref: evidence, cell_ref: new_cell, "state/chatgpt.json": new_state}
        for path, value in objects.items():
            _write_json(worktree, path, value)
        commit = _commit_push(store, worktree, list(objects), f"Wake Helper for initial Planner bootstrap {task_id} [skip ci]")
    finally:
        _drop_worktree(store, worktree)
    return {"ok": True, "action": "planner_bootstrap_helper_staged", "task_id": task_id,
            "helper_request_id": helper_id, "recovery_ref": evidence_ref, "commit_sha": commit}


def _recover_stalled_planner_delivery(store: Any, state: dict[str, Any], base_sha: str) -> dict[str, Any] | None:
    """Release only an expired, exact Planner delivery; preserve its request.

    This is transport recovery only. Missing semantic output remains canonical
    state for Harness/Playwright liveness reconciliation.
    """
    request = state.get("control_request") or {}
    if (request.get("kind") != "task_cell_role_prompt" or request.get("role") != "planner"
            or request.get("status") not in {"PENDING", "APPLYING"}):
        return None
    started = _parse_time(request.get("requested_at"))
    if started is None:
        return None  # Unknown delivery age is not fabricated timeout evidence.
    task_id = _safe_task_id(request.get("task_id"))
    cell_ref = _task_cell_path(task_id)
    cell = _try_read_json(store, base_sha, cell_ref)
    if not cell:
        return None
    task = _try_read_json(store, base_sha, _task_contract_ref(task_id)) or {}
    control = cell.get("planner_control") or {}
    authority = control.get("authority") or {}
    if (control.get("enabled") is not True or control.get("semantic_authority_closed") is True
            or any(str(x.get("status") or "").upper() in {"PAUSED_BY_USER", "PAUSED", "CANCELLED", "STOPPED", "DONE"} for x in (task, cell))
            or str(control.get("activity") or "") in {"WAIT_USER", "DONE", "ERROR", "HANDOFF", "ROTATING"}
            or int(request.get("control_epoch") or 0) != int(cell.get("control_epoch") or 0)
            or str(request.get("challenge") or "") != str(authority.get("challenge") or "")):
        return None
    runtime = _runtime(control)
    timeout = int(runtime.get("semantic_output_timeout_seconds") or DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS)
    if _now_dt() < started + timedelta(seconds=max(1, timeout)):
        return None
    output_ref = str(request.get("required_output_ref") or "")
    if not output_ref:
        return None
    output_exists = _artifact_exists(store, base_sha, output_ref)
    evidence_ref = f"evidence/{task_id}/recovery/control-{_stable_token('delivery', {'request_id': request.get('request_id')})}.json"
    evidence = {"v": 1, "task_id": task_id, "control_epoch": cell["control_epoch"],
                "code": "PLANNER_DELIVERY_LEASE_EXPIRED", "observed_at": utc_now(),
                "source_commit": base_sha, "request": copy.deepcopy(request), "output_exists": output_exists}
    new_state = copy.deepcopy(state)
    new_state["control_request"] = {**request, "status": "ERROR", "completed_at": utc_now(), "recovery_ref": evidence_ref}
    new_cell = copy.deepcopy(cell)
    recovery_inbox = ((cell.get("planner_control") or {}).get("inbox") or {})
    recovery_doorbell = recovery_inbox.get("active_doorbell") if isinstance(recovery_inbox, dict) else None
    event_ids = [
        str(x)
        for x in (recovery_doorbell.get("event_ids") if isinstance(recovery_doorbell, dict) else [])
    ]

    rt = _runtime(new_cell["planner_control"])
    helper_id, helper_ref = _planner_helper_identity(task_id, evidence_ref)
    pending = list(rt.get("pending_role_requests") or [])
    pending.append({"role": "helper", "kind": "helper_result", "request_id": helper_id,
        "challenge": _stable_token("helper-challenge", {"incident": evidence_ref}),
        "artifact_ref": helper_ref, "state": "REQUEST_PENDING", "decision_ref": evidence_ref,
        "prompt": f"Harness delivery timeout; inspect {evidence_ref} and the exact Planner chat {authority.get('conversation_id')}. If internal state has no clear stall, inspect the actual browser using available Playwright tools. Distinguish still-generating work from missed Enter/missing original wake; repair only that bound input, never invent a task or rotate Planner on timeout. Record observations/action/outcome."})
    rt["pending_role_requests"] = pending
    rt, _ = _stage_pending_helper_request(new_state, new_cell, rt, store.git_branch)
    new_cell["planner_control"]["runtime"] = rt
    worktree = _prepare_worktree(store, base_sha, f"planner-delivery-recovery-{task_id}")
    try:
        for path, value in {evidence_ref: evidence, cell_ref: new_cell, "state/chatgpt.json": new_state}.items():
            _write_json(worktree, path, value)
        commit_sha = _commit_push(store, worktree, [evidence_ref, cell_ref, "state/chatgpt.json"], f"Recover expired Planner delivery {task_id} [skip ci]")
    finally:
        _drop_worktree(store, worktree)

    helper = {"ok": True, "staged": True}
    return {
        "ok": True,
        "action": "planner_delivery_recovered",
        "task_id": task_id,
        "recovery_ref": evidence_ref,
        "output_exists": output_exists,
        "released_event_ids": event_ids,
        "recovery_commit_sha": commit_sha,
        "helper": helper,
    }


def _stage_expired_planner_output_helper(store: Any, state: dict[str, Any], cell: dict[str, Any], base_sha: str) -> dict[str, Any] | None:
    """Mechanical timeout -> existing Helper channel, independent of Planner approval."""
    control = cell.get("planner_control") or {}
    runtime = _runtime(control)
    pending = runtime.get("pending_output") or {}
    deadline = _parse_time(pending.get("deadline_at"))
    task_id = str(cell.get("task_id") or "")
    task = _try_read_json(store, base_sha, _task_contract_ref(task_id)) or {}
    if (pending.get("status") != "WAITING" or deadline is None or _now_dt() < deadline
            or control.get("semantic_authority_closed") is True
            or control.get("activity") in {"WAIT_USER", "DONE", "ERROR", "HANDOFF", "ROTATING"}
            or any(item.get("status") in {"PAUSED_BY_USER", "PAUSED", "CANCELLED", "STOPPED", "DONE"} for item in (task, cell))):
        return None
    authority = control.get("authority") or {}
    if not pending.get("conversation_id") or pending["conversation_id"] != authority.get("conversation_id"):
        return None
    evidence_ref = f"evidence/{task_id}/recovery/{_stable_token('output-timeout', {'request_id': pending.get('request_id'), 'deadline': pending.get('deadline_at')})}.json"
    helper_id, helper_ref = _planner_helper_identity(task_id, evidence_ref)
    if any(item.get("request_id") == helper_id for item in runtime.get("pending_role_requests", [])):
        return None
    original = state.get("control_request") or {}
    prompt = str(pending.get("wake_prompt") or "")
    if not prompt and pending.get("request_id") in {original.get("request_id"), original.get("planner_request_id")}:
        prompt = str(original.get("prompt") or "")  # Existing pre-fix live turn.
    recovery = {"request_id": pending["request_id"], "output_ref": pending["ref"],
                "conversation_id": pending["conversation_id"], "prompt": prompt,
                "project_key": str(cell.get("task_cell_project_key") or ""),
                "conversation_url": str((cell.get("roles", {}).get("planner") or {}).get("conversation_url") or ""),
                "planner_generation": authority.get("planner_generation"),
                "planner_fence_token": authority.get("planner_fence_token")}
    new_cell, new_state = copy.deepcopy(cell), copy.deepcopy(state)
    rt = _runtime(new_cell["planner_control"])
    rt["pending_role_requests"].append({
        "role": "helper", "kind": "helper_result", "request_id": helper_id,
        "challenge": _stable_token("helper-challenge", {"incident": evidence_ref}),
        "artifact_ref": helper_ref, "state": "REQUEST_PENDING", "decision_ref": evidence_ref,
        "recovery": recovery,
        "prompt": f"Harness observed no completed Planner output by its deadline. Inspect {evidence_ref}, the exact retained request/output binding, and only the browser evidence needed to distinguish genuine continued generation from a missed/stopped delivery. A timeout alone is not proof of a stopped AI. If the original bound request should be retried and no valid outcome exists, restore that same retained request/event to its runnable state so Harness re-delivers it mechanically to the same Planner. Never rewrite instructions or rotate Planner. Verify the resulting real response-start/state, then record diagnosis/repair_result."
    })
    rt, _ = _stage_pending_helper_request(new_state, new_cell, rt, store.git_branch)
    new_cell["planner_control"]["runtime"] = rt
    evidence = {"v": 1, "code": "PLANNER_OUTPUT_DEADLINE_EXPIRED", "task_id": task_id,
                "control_epoch": cell["control_epoch"], "observed_at": utc_now(),
                "source_commit": base_sha, "pending_output": pending, "recovery": recovery}
    worktree = _prepare_worktree(store, base_sha, f"planner-output-helper-{task_id}")
    try:
        objects = {evidence_ref: evidence, _task_cell_path(task_id): new_cell, "state/chatgpt.json": new_state}
        for path, value in objects.items():
            _write_json(worktree, path, value)
        commit = _commit_push(store, worktree, list(objects), f"Wake Helper for expired Planner output {task_id} [skip ci]")
    finally:
        _drop_worktree(store, worktree)
    return {"ok": True, "action": "planner_output_helper_staged", "task_id": task_id,
            "helper_request_id": helper_id, "recovery_ref": evidence_ref, "commit_sha": commit}


def _route_ready_bootstrap_helper_result(
    store: Any,
    state: dict[str, Any],
    base_sha: str,
    cell_ref: str,
    cell: dict[str, Any],
) -> dict[str, Any] | None:
    """Route a complete bootstrap Helper result before Helper exits.

    diagnosis+repair_result is the semantic handoff payload. turn_signal=done is
    deliberately not required here; it is written later only after the resumed
    Planner has actually started.
    """
    bootstrap = cell.get("bootstrap_recovery") if isinstance(cell.get("bootstrap_recovery"), dict) else {}
    if (
        str(cell.get("status") or "") != "BOOTSTRAPPING"
        or str(bootstrap.get("state") or "") != "WAITING_HELPER"
    ):
        return None
    helper_id = str(bootstrap.get("helper_request_id") or "")
    if not helper_id:
        return None
    control = cell.get("planner_control") if isinstance(cell.get("planner_control"), dict) else {}
    runtime = _runtime(control)
    pending_outputs = list(runtime.get("pending_role_outputs") or [])
    match_index = -1
    record: dict[str, Any] | None = None
    for index, row in enumerate(pending_outputs):
        if (
            isinstance(row, dict)
            and str(row.get("request_id") or "") == helper_id
            and str(row.get("state") or "") == "WAITING"
        ):
            match_index = index
            record = row
            break
    if record is None:
        return None
    output_ref = str(record.get("artifact_ref") or "")
    if not output_ref or not _artifact_exists(store, base_sha, output_ref):
        return None
    result = _try_read_json(store, base_sha, output_ref) or {}
    if not (
        bool(str(result.get("diagnosis") or "").strip())
        and bool(str(result.get("repair_result") or "").strip())
    ):
        return None
    if not _control_slot_available(state):
        return None
    current_request = state.get("control_request") or {}
    if (
        str(current_request.get("request_id") or "") != helper_id
        or str(current_request.get("status") or "") not in {"DONE", "ERROR"}
    ):
        return None
    task_id = str(cell.get("task_id") or "")
    task = _try_read_json(store, base_sha, _task_contract_ref(task_id)) or {}
    if task.get("status") in {"PAUSED_BY_USER", "PAUSED", "CANCELLED", "STOPPED", "DONE"}:
        return None
    original = bootstrap.get("control_request") if isinstance(bootstrap.get("control_request"), dict) else {}
    if not original:
        return None

    new_cell = copy.deepcopy(cell)
    new_state = copy.deepcopy(state)
    new_bootstrap = new_cell["bootstrap_recovery"]
    new_bootstrap["state"] = "RESUMING"
    new_bootstrap["helper_result_ref"] = output_ref
    rt = _runtime(new_cell["planner_control"])
    pending = list(rt.get("pending_role_outputs") or pending_outputs)
    if 0 <= match_index < len(pending) and isinstance(pending[match_index], dict):
        pending[match_index] = {
            **pending[match_index],
            "state": "EMITTED",
            "result_ready_at": utc_now(),
            "return_mode": "BOOTSTRAP_RESUME",
        }
    rt["pending_role_outputs"] = pending
    new_cell["planner_control"]["runtime"] = rt
    new_state["control_request"] = {
        **copy.deepcopy(original),
        "status": "PENDING",
        "bootstrap_recovery_ref": str(bootstrap.get("evidence_ref") or ""),
    }

    worktree = _prepare_worktree(store, base_sha, f"bootstrap-helper-result-{task_id}")
    try:
        _write_json(worktree, cell_ref, new_cell)
        _write_json(worktree, "state/chatgpt.json", new_state)
        commit_sha = _commit_push(
            store,
            worktree,
            [cell_ref, "state/chatgpt.json"],
            f"Route bootstrap Helper result for {task_id} [skip ci]",
        )
    finally:
        _drop_worktree(store, worktree)
    return {
        "ok": True,
        "action": "bootstrap_helper_result_routed",
        "task_id": task_id,
        "helper_request_id": helper_id,
        "output_ref": output_ref,
        "commit_sha": commit_sha,
    }


def planner_runtime_tick(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    project_id = str(req.get("project_id") or "").strip()
    if not project_id:
        return {"ok": False, "error": "project_id required"}

    with store.git_lock:
        store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
        base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
        state = _read_json(store, base_sha, "state/chatgpt.json")
        control_busy = not _control_slot_available(state)
        cells = _list_task_cells(store, base_sha)
        recovered = _recover_stalled_initial_planner(store, state, base_sha)
        if recovered:
            return recovered

    if control_busy:
        with store.git_lock:
            recovered = _recover_stalled_planner_delivery(store, state, base_sha)
        if recovered:
            return recovered
        return {"ok": True, "idle": "control_slot_busy", "task_cells_scanned": len(cells)}

    for cell_ref in cells:
        with store.git_lock:
            cell = _try_read_json(store, base_sha, cell_ref)
            if not isinstance(cell, dict):
                continue
            bootstrap_result = _route_ready_bootstrap_helper_result(
                store, state, base_sha, cell_ref, cell,
            )
            if bootstrap_result is not None:
                return bootstrap_result
            control = cell.get("planner_control")
            if not isinstance(control, dict) or control.get("enabled") is not True:
                continue
            task_id = str(cell.get("task_id") or "")
            if not task_id:
                continue
            runtime = _runtime(control)
            successor = control.get("successor") if isinstance(control.get("successor"), dict) else {"state": "NONE"}
            successor_state = str(successor.get("state") or "NONE")
            handoff_id = str(successor.get("handoff_id") or "")
            now = _now_dt()
            # Replacement generations receive no semantic work until the
            # old fenced conversation has been retired and rotation bookkeeping
            # is cleared back to NONE.
            planner_ready = successor_state == "NONE"

            if planner_ready:
                try:
                    turn_result = _consume_planner_turn(
                        store,
                        base_sha=base_sha,
                        cell_ref=cell_ref,
                        cell=cell,
                        state=state,
                        project_id=project_id,
                        repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
                    )
                except PlannerRuntimeError as exc:
                    return {"ok": False, "error": exc.code, "task_id": task_id}
                if turn_result is not None:
                    return turn_result

                recovered = _stage_expired_planner_output_helper(store, state, cell, base_sha)
                if recovered is not None:
                    return recovered

            # A committed Foreground Task Contract change is itself the
            # durable user-intent signal. Harness derives the Planner event.
            current_ref = planner_current_path(task_id)
            current = _try_read_json(store, base_sha, current_ref)
            task_ref = _task_contract_ref(task_id)
            task = _try_read_json(store, base_sha, task_ref)
            if (
                planner_ready
                and isinstance(current, dict)
                and isinstance(task, dict)
                and current.get("read_only") is not True
            ):
                task_sha = _blob_sha(store, base_sha, task_ref)
                if str(current.get("task_contract_blob_sha") or "") != task_sha:
                    revision = int(
                        task.get("intent_revision")
                        or task.get("task_contract_revision")
                        or task.get("v")
                        or 1
                    )
                    intent_event = make_planner_event(
                        task_id=task_id,
                        control_epoch=int(cell["control_epoch"]),
                        kind="foreground_intent",
                        source_role="foreground",
                        source_identity={
                            "task_contract_ref": task_ref,
                            "task_contract_blob_sha": task_sha,
                            "intent_revision": revision,
                        },
                        refs=[task_ref],
                    )
                    try:
                        new_control, _ = insert_planner_event(control, intent_event)
                        new_current = checkpoint_planner_current(
                            current,
                            {
                                "task_contract_revision": revision,
                                "task_contract_blob_sha": task_sha,
                            },
                            expected_memory_version=int(current["memory_version"]),
                            trigger="TASK_CONTRACT_CHANGED",
                            written_at=utc_now(),
                        )
                    except (PlannerControlError, PlannerMemoryError) as exc:
                        return {"ok": False, "error": getattr(exc, "code", str(exc)), "task_id": task_id}
                    new_cell = copy.deepcopy(cell)
                    new_cell["planner_control"] = new_control
                    worktree = _prepare_worktree(store, base_sha, f"planner-foreground-intent-{task_id}")
                    try:
                        _write_json(worktree, cell_ref, new_cell)
                        _write_json(worktree, current_ref, new_current)
                        commit_sha = _commit_push(
                            store,
                            worktree,
                            [cell_ref, current_ref],
                            f"Detect Foreground intent for Planner {task_id} [skip ci]",
                        )
                    finally:
                        _drop_worktree(store, worktree)
                    return {
                        "ok": True,
                        "action": "foreground_intent_event_enqueued",
                        "task_id": task_id,
                        "event_id": intent_event["event_id"],
                        "commit_sha": commit_sha,
                    }

            # Deterministically convert terminal child state into one
            # worker_result Planner event. Planner never polls the Worker chat.
            owned_children = list(runtime.get("owned_children") or [])
            for index, child in enumerate(owned_children):
                if not isinstance(child, dict) or str(child.get("event_state") or "") != "WAITING":
                    continue
                backend_cl = str(child.get("backend_cl") or "")
                child_cl = _try_read_json(store, base_sha, backend_cl) if backend_cl else None
                if not isinstance(child_cl, dict):
                    continue
                dispatch = child_cl.get("dispatch") if isinstance(child_cl.get("dispatch"), dict) else {}
                if not (_terminal_dispatch_state(child_cl.get("overall")) or _terminal_dispatch_state(dispatch.get("state"))):
                    continue
                result_ref = str(child_cl.get("result_ref") or "")
                if not result_ref or not _artifact_exists(store, base_sha, result_ref):
                    result_ref = backend_cl
                result = _try_read_json(store, base_sha, result_ref) or {}
                continuing = result.get("status") == "CONTINUE"
                generation = int(dispatch.get("generation") or 0)
                if continuing and generation % WORKER_REVIEW_INTERVAL:
                    nxt = _continue_worker_child(
                        store, base_sha, parent_task_id=task_id,
                        parent_control_epoch=int(cell["control_epoch"]), child=child,
                        direction_id=str(dispatch["dispatch_id"]), project_id=project_id,
                        repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
                    )
                    new_cell = copy.deepcopy(cell)
                    rt = _runtime(new_cell["planner_control"])
                    owned = list(rt["owned_children"])
                    owned[index] = {**child, "event_state": "WAITING",
                        "dispatch_id": nxt["dispatch"]["dispatch_id"],
                        "dispatch_generation": nxt["dispatch"]["generation"],
                        "fence_token": nxt["dispatch"]["fence_token"],
                        "worker_reply_entry_ref": nxt["worker_reply_entry_ref"]}
                    rt["owned_children"] = owned
                    new_cell["planner_control"]["runtime"] = rt
                    objects = {cell_ref: new_cell, nxt["task_ref"]: nxt["task"],
                        nxt["backend_cl"]: nxt["cl"], nxt["wake_ref"]: nxt["wake"],
                        nxt["task"]["expected_result_ref"]: {**nxt["task"]["result_contract"], "summary": ""}}
                    worktree = _prepare_worktree(store, base_sha, f"worker-continue-{task_id}")
                    try:
                        for path, value in objects.items():
                            _write_json(worktree, path, value)
                        _write_text(worktree, nxt["worker_reply_entry_ref"], nxt["worker_reply_entry_initial"])
                        sha = _commit_push(store, worktree, [*objects, nxt["worker_reply_entry_ref"]], f"Continue Worker generation {generation + 1} for {task_id}")
                    finally:
                        _drop_worktree(store, worktree)
                    local_delivery = _emit_local_worker_rollover(store, nxt, commit_sha=sha)
                    return {
                        "ok": True,
                        "action": "worker_continued",
                        "generation": generation + 1,
                        "commit_sha": sha,
                        "local_delivery": local_delivery,
                    }
                result_blob_sha = _blob_sha(store, base_sha, result_ref)
                source_identity = {
                    "child_task_id": str(child.get("child_task_id") or child_cl.get("task_id") or ""),
                    "backend_cl": backend_cl,
                    "dispatch_id": str(dispatch.get("dispatch_id") or child.get("dispatch_id") or ""),
                    "dispatch_generation": int(dispatch.get("generation") or child.get("dispatch_generation") or 0),
                    "fence_token": str(dispatch.get("fence_token") or child.get("fence_token") or ""),
                    "result_ref": result_ref,
                    "result_blob_sha": result_blob_sha,
                }
                try:
                    event = make_planner_event(
                        task_id=task_id,
                        control_epoch=int(cell["control_epoch"]),
                        kind="worker_result",
                        source_role="worker",
                        source_identity=source_identity,
                        refs=[
                            backend_cl,
                            result_ref,
                            str(child.get("child_reply_ref") or ""),
                        ],
                    )
                    new_control, _ = insert_planner_event(control, event)
                except PlannerControlError as exc:
                    return {"ok": False, "error": exc.code, "task_id": task_id}
                rt = _runtime(new_control)
                owned = list(rt.get("owned_children") or owned_children)
                if index < len(owned) and isinstance(owned[index], dict):
                    owned[index] = {**owned[index], "event_state": "REVIEW_PENDING" if continuing else "EMITTED", "event_id": event["event_id"], "terminal_observed_at": utc_now()}
                rt["owned_children"] = owned
                rt["worker_round"] = None
                new_control["activity"] = "REPLANNING"
                new_control["wait"] = None
                new_control["runtime"] = rt
                new_cell = copy.deepcopy(cell)
                new_cell["planner_control"] = new_control
                worktree = _prepare_worktree(store, base_sha, f"planner-child-event-{task_id}")
                try:
                    _write_json(worktree, cell_ref, new_cell)
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        [cell_ref],
                        f"Enqueue child result for Planner {task_id} [skip ci]",
                    )
                finally:
                    _drop_worktree(store, worktree)
                return {"ok": True, "action": "worker_result_event_enqueued", "task_id": task_id, "event_id": event["event_id"], "commit_sha": commit_sha}

            # A pending Helper may originate from Planner semantics or the
            # independent Worker watchdog. Keep those lifecycle labels distinct.
            pending_helper = _pending_helper_request(runtime)
            if planner_ready and pending_helper is not None:
                _helper_index, helper_request = pending_helper
                worker_helper = str(helper_request.get("kind") or "") == "worker_helper_result"
                new_control = copy.deepcopy(control)
                rt = _runtime(new_control)
                rt, state_changed = _stage_pending_helper_request(state, cell, rt, store.git_branch)
                if state_changed:
                    new_control["runtime"] = rt
                    new_cell = copy.deepcopy(cell)
                    new_cell["planner_control"] = new_control
                    stage_label = "worker-helper" if worker_helper else "planner-helper"
                    worktree = _prepare_worktree(store, base_sha, f"{stage_label}-stage-{task_id}")
                    try:
                        _write_json(worktree, cell_ref, new_cell)
                        _write_json(worktree, "state/chatgpt.json", state)
                        commit_sha = _commit_push(
                            store,
                            worktree,
                            [cell_ref, "state/chatgpt.json"],
                            (
                                f"Stage WORKER_HELPER request for {task_id} [skip ci]"
                                if worker_helper
                                else f"Stage Planner Helper request for {task_id} [skip ci]"
                            ),
                        )
                    finally:
                        _drop_worktree(store, worktree)
                    return {
                        "ok": True,
                        "action": (
                            "worker_helper_request_staged"
                            if worker_helper
                            else "planner_helper_request_staged"
                        ),
                        "task_id": task_id,
                        "commit_sha": commit_sha,
                    }

            # 0d. Two-phase Helper protocol: durable diagnosis+repair_result
            # is routed to the next semantic owner while Helper stays alive.
            # turn_signal=done is written later and means only "Helper may exit".
            pending_roles = list(runtime.get("pending_role_outputs") or [])
            for index, record in enumerate(pending_roles):
                if not isinstance(record, dict) or str(record.get("state") or "") != "WAITING":
                    continue
                output_ref = str(record.get("artifact_ref") or "")
                if not output_ref or not _artifact_exists(store, base_sha, output_ref):
                    continue
                helper_result = _read_json(store, base_sha, output_ref)
                ready_result = (
                    isinstance(helper_result, dict)
                    and bool(str(helper_result.get("diagnosis") or "").strip())
                    and bool(str(helper_result.get("repair_result") or "").strip())
                )
                if not ready_result:
                    continue
                output_blob_sha = _blob_sha(store, base_sha, output_ref)
                record_kind = str(record.get("kind") or "helper_result")
                worker_helper = record_kind == "worker_helper_result"
                return_target = (
                    str(helper_result.get("return_target") or "").strip().lower()
                    if worker_helper
                    else "planner"
                )

                # Worker Helper is not a disguised Planner Helper. If same-generation
                # Worker recovery is the return path, retain the result in-place for
                # Helper exit verification and do not manufacture a Planner event.
                if worker_helper and return_target in {"worker", "same_worker"}:
                    new_control = copy.deepcopy(control)
                    rt = _runtime(new_control)
                    pending = list(rt.get("pending_role_outputs") or pending_roles)
                    if index < len(pending) and isinstance(pending[index], dict):
                        pending[index] = {
                            **pending[index],
                            "state": "RESULT_READY",
                            "result_ready_at": utc_now(),
                            "return_mode": "WORKER_SAME_GENERATION",
                            "output_blob_sha": output_blob_sha,
                        }
                    rt["pending_role_outputs"] = pending
                    new_control["runtime"] = rt
                    new_cell = copy.deepcopy(cell)
                    new_cell["planner_control"] = new_control
                    worktree = _prepare_worktree(store, base_sha, f"worker-helper-ready-{task_id}")
                    try:
                        _write_json(worktree, cell_ref, new_cell)
                        commit_sha = _commit_push(
                            store,
                            worktree,
                            [cell_ref],
                            f"Record WORKER_HELPER result-ready for {task_id} [skip ci]",
                        )
                    finally:
                        _drop_worktree(store, worktree)
                    return {
                        "ok": True,
                        "action": "worker_helper_result_ready",
                        "task_id": task_id,
                        "return_target": "worker",
                        "commit_sha": commit_sha,
                    }

                if worker_helper and return_target != "planner":
                    # The Worker Helper prompt requires an explicit routing choice.
                    # Leave the record WAITING so Helper can correct the bound result.
                    continue

                source_identity = {
                    "helper_request_id": str(record.get("request_id") or ""),
                    "challenge": str(record.get("challenge") or ""),
                    "output_ref": output_ref,
                    "output_blob_sha": output_blob_sha,
                    "helper_source": str(
                        record.get("helper_source") or (
                            "worker_watchdog" if worker_helper else "planner"
                        )
                    ),
                }
                event_kind = "worker_helper_result" if worker_helper else "helper_result"
                try:
                    event = make_planner_event(
                        task_id=task_id,
                        control_epoch=int(cell["control_epoch"]),
                        kind=event_kind,
                        source_role="helper",
                        source_identity=source_identity,
                        refs=[output_ref],
                    )
                    new_control, _ = insert_planner_event(control, event)
                except PlannerControlError as exc:
                    return {"ok": False, "error": exc.code, "task_id": task_id}
                rt = _runtime(new_control)
                pending = list(rt.get("pending_role_outputs") or pending_roles)
                if index < len(pending) and isinstance(pending[index], dict):
                    pending[index] = {
                        **pending[index],
                        "state": "EMITTED",
                        "event_id": event["event_id"],
                        "result_ready_at": utc_now(),
                        "emitted_at": utc_now(),
                        "return_mode": "PLANNER_EVENT",
                    }
                rt["pending_role_outputs"] = pending
                new_control["runtime"] = rt
                new_cell = copy.deepcopy(cell)
                new_cell["planner_control"] = new_control
                stage_label = "worker-helper-planner-event" if worker_helper else "planner-helper-event"
                worktree = _prepare_worktree(store, base_sha, f"{stage_label}-{task_id}")
                try:
                    _write_json(worktree, cell_ref, new_cell)
                    commit_sha = _commit_push(
                        store,
                        worktree,
                        [cell_ref],
                        (
                            f"Enqueue WORKER_HELPER result for Planner {task_id} [skip ci]"
                            if worker_helper
                            else f"Enqueue Helper result for Planner {task_id} [skip ci]"
                        ),
                    )
                finally:
                    _drop_worktree(store, worktree)
                return {
                    "ok": True,
                    "action": (
                        "worker_helper_result_event_enqueued"
                        if worker_helper
                        else "helper_result_event_enqueued"
                    ),
                    "task_id": task_id,
                    "event_id": event["event_id"],
                    "commit_sha": commit_sha,
                }

            # 0e. Claim pending Planner events and ring exactly one durable
            # doorbell. Foreground presence is intentionally not consulted.
            inbox = control.get("inbox") if isinstance(control, dict) else None
            events = inbox.get("events") if isinstance(inbox, dict) else None
            active_doorbell = inbox.get("active_doorbell") if isinstance(inbox, dict) else None
            activity = str(control.get("activity") or "")
            compatible_kinds = {"foreground_intent"} if activity == "WAIT_USER" else None
            pending_event_exists = isinstance(events, dict) and any(
                isinstance(event, dict)
                and str(event.get("state") or "") == "PENDING"
                and (
                    compatible_kinds is None
                    or str(event.get("kind") or "") in compatible_kinds
                )
                for event in events.values()
            )
            if (
                planner_ready
                and runtime.get("foreground_runtime_dependency") is False
                and control.get("semantic_authority_closed") is not True
                and activity not in {"HANDOFF", "ROTATING", "DONE", "ERROR"}
                and pending_event_exists
                and not isinstance(active_doorbell, dict)
            ):
                authority = control.get("authority") or {}
                try:
                    claimed_control, doorbell = claim_planner_events(
                        control,
                        task_id=task_id,
                        control_epoch=int(cell["control_epoch"]),
                        planner_generation=int(authority.get("planner_generation") or 0),
                        planner_fence_token=str(authority.get("planner_fence_token") or ""),
                        claimed_at=utc_now(),
                        compatible_kinds=compatible_kinds,
                    )
                except PlannerControlError as exc:
                    return {"ok": False, "error": exc.code, "task_id": task_id}
                if doorbell:
                    doorbell_id = str(doorbell["doorbell_id"])
                    request_id = _stable_token("planner-doorbell", {
                        "task_id": task_id,
                        "doorbell_id": doorbell_id,
                        "generation": authority.get("planner_generation"),
                    })
                    rt = _runtime(claimed_control)
                    owned = list(rt.get("owned_children") or [])
                    slot_rows, slot_bindings = _lane_turn_surfaces(
                        store, base_sha, task_id=task_id, epoch=int(cell["control_epoch"]),
                        doorbell_id=doorbell_id, generation=int(authority["planner_generation"]),
                        fence=str(authority["planner_fence_token"]), owned=owned,
                    )
                    memory_ref = planner_semantic_memory_path(task_id)
                    memory_entry_ref = planner_turn_memory_entry_path(task_id, doorbell_id)
                    outcome_ref = planner_turn_outcome_path(task_id, doorbell_id)
                    plan_ref = f"tasks/{task_id}.plan.json"
                    prompt = _planner_turn_prompt(
                        task_id=task_id,
                        doorbell=doorbell,
                        control=claimed_control,
                        plan_ref=plan_ref,
                        memory_ref=memory_ref,
                        memory_entry_ref=memory_entry_ref,
                        slots=slot_rows,
                        outcome_ref=outcome_ref, git_branch=store.git_branch,
                    )
                    _set_control_request(
                        state,
                        kind="task_cell_role_prompt",
                        request_id=request_id,
                        task_id=task_id,
                        epoch=int(cell["control_epoch"]),
                        role="planner",
                        challenge=str(authority.get("challenge") or ""),
                        project_key=str(cell.get("task_cell_project_key") or ""),
                        prompt=prompt,
                        extra={
                            "required_output_ref": outcome_ref,
                            "semantic_output_kind": "PLANNER_TURN",
                            "planner_doorbell_id": doorbell_id,
                            "planner_generation": int(authority.get("planner_generation") or 0),
                            "planner_fence_token": str(authority.get("planner_fence_token") or ""),
                        },
                    )
                    rt["pending_output"] = {
                        "kind": "PLANNER_TURN",
                        "ref": outcome_ref,
                        "request_id": request_id,
                        "status": "DELIVERY_PENDING",
                    }
                    rt["active_turn"] = {
                        "doorbell_id": doorbell_id,
                        "memory_entry_ref": memory_entry_ref,
                        "outcome_ref": outcome_ref,
                        "slots": slot_bindings,
                    }
                    claimed_control["runtime"] = rt
                    claimed_control["activity"] = "WAKING"
                    new_cell = copy.deepcopy(cell)
                    new_cell["planner_control"] = claimed_control
                    worktree = _prepare_worktree(store, base_sha, f"planner-doorbell-{task_id}")
                    try:
                        memory_path = worktree / memory_ref
                        if not memory_path.exists():
                            _write_text(worktree, memory_ref, "# Planner Memory\n")
                        _write_text(
                            worktree,
                            memory_entry_ref,
                            planner_turn_memory_entry_header(
                                entry_id=doorbell_id,
                                planner_generation=int(authority.get("planner_generation") or 0),
                            ),
                        )
                        for slot in slot_rows:
                            _write_json(worktree, str(slot["slot_ref"]), slot)
                        _write_json(
                            worktree,
                            outcome_ref,
                            make_planner_turn_outcome(
                                task_id=task_id,
                                control_epoch=int(cell["control_epoch"]),
                                doorbell_id=doorbell_id,
                                planner_generation=int(authority.get("planner_generation") or 0),
                                planner_fence_token=str(authority.get("planner_fence_token") or ""),
                            ),
                        )
                        _write_json(worktree, cell_ref, new_cell)
                        _write_json(worktree, "state/chatgpt.json", state)
                        paths = [
                            memory_ref,
                            memory_entry_ref,
                            outcome_ref,
                            cell_ref,
                            "state/chatgpt.json",
                        ] + [str(slot["slot_ref"]) for slot in slot_rows]
                        commit_sha = _commit_push(
                            store,
                            worktree,
                            paths,
                            f"Stage Planner turn for {task_id} [skip ci]",
                        )
                    finally:
                        _drop_worktree(store, worktree)
                    return {"ok": True, "action": "planner_turn_staged", "task_id": task_id, "doorbell_id": doorbell_id, "commit_sha": commit_sha}

            # 1. Replacement binding/promotion is Harness-owned. The AI does
            # not produce a takeover artifact. Once browser promotion is durable,
            # retire the fenced predecessor before delivering semantic work.
            if successor_state == "PROMOTED" and handoff_id and runtime.get("browser_promotion_complete") is True:
                handoff_ref = planner_handoff_path(task_id, handoff_id)
                handoff = _read_json(store, base_sha, handoff_ref)
                try:
                    eligibility = validate_predecessor_retirement_eligibility(
                        handoff, control,
                        task_cell_project_key=str(cell.get("task_cell_project_key") or ""),
                        protected_conversation_ids=[str(control["authority"].get("conversation_id") or "")],
                    )
                except PlannerMemoryError as exc:
                    return {"ok": False, "error": exc.code, "task_id": task_id}
                _set_control_request(
                    state, kind="task_cell_planner_predecessor_retire",
                    request_id=str(eligibility["retirement_request_id"]), task_id=task_id,
                    epoch=int(cell["control_epoch"]), role="planner",
                    challenge=str(control["authority"].get("challenge") or ""),
                    project_key=str(eligibility["project_key"]),
                    extra={"handoff_id": handoff_id, "conversation_id": str(eligibility["conversation_id"]), "exact_id_only": True},
                )
                worktree = _prepare_worktree(store, base_sha, f"planner-retire-request-{task_id}")
                try:
                    _write_json(worktree, "state/chatgpt.json", state)
                    commit_sha = _commit_push(store, worktree, ["state/chatgpt.json"], f"Retire fenced Planner predecessor {task_id} [skip ci]")
                finally:
                    _drop_worktree(store, worktree)
                return {"ok": True, "action": "predecessor_retirement_requested", "task_id": task_id, "handoff_id": handoff_id, "commit_sha": commit_sha}

            # Normal Planner rotation is mechanical after a completed semantic turn.
            # No self-health/ROTATE AI round is required.
            active_turn_now = _runtime(control).get("active_turn")
            pending_output_now = _runtime(control).get("pending_output")
            if (
                planner_ready
                and not isinstance(active_turn_now, dict)
                and not isinstance(pending_output_now, dict)
                and not isinstance(active_doorbell, dict)
            ):
                rotation_due, _rotation_reason = _rotation_due(control, now)
                if rotation_due:
                    return request_planner_rotation(store, {
                        "task_id": task_id,
                        "reason": "explicit_lifecycle_rollover",
                        "caller_role": "harness",
                    })

    return {"ok": True, "idle": "no_planner_maintenance_action", "task_cells_scanned": len(cells)}
