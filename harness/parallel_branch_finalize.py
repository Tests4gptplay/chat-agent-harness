#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
REPO_NAME = "example-owner/cah-private"
TERMINAL = {"PASS", "BLOCKED", "ERROR"}
AGGREGATE_SOURCE = "parallel_branch_finalize"


class ParallelBranchFinalizeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ParallelBranchFinalizeError(f"{path}: expected object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


GIT_TIMEOUT_SECONDS = 30


def run_git(
    root: Path,
    *args: str,
    check: bool = True,
    timeout_seconds: int = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "SSH_ASKPASS_REQUIRE": "never",
        }
    )
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParallelBranchFinalizeError(
            f"git {' '.join(args)} timed out after {timeout_seconds}s"
        ) from exc
    if check and proc.returncode != 0:
        raise ParallelBranchFinalizeError(
            f"git {' '.join(args)} failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}"
        )
    return proc


def safe_rel(value: Any, label: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    p = Path(raw)
    if not raw or p.is_absolute() or ".." in p.parts:
        raise ParallelBranchFinalizeError(f"invalid {label}")
    return p.as_posix()


def branch_result_ref(task: dict[str, Any]) -> str:
    output_root = safe_rel(task.get("output_root"), "output_root")
    return f"{output_root}/branch_result.json"


def fetch_branch_result(
    root: Path,
    *,
    remote: str,
    branch: str,
    result_ref: str,
) -> tuple[dict[str, Any], str]:
    run_git(root, "fetch", "--quiet", "--no-tags", remote, branch)
    head = run_git(root, "rev-parse", "FETCH_HEAD").stdout.strip()
    shown = run_git(root, "show", f"FETCH_HEAD:{result_ref}")
    try:
        result = json.loads(shown.stdout)
    except json.JSONDecodeError as exc:
        raise ParallelBranchFinalizeError("branch_result is not valid JSON") from exc
    if not isinstance(result, dict):
        raise ParallelBranchFinalizeError("branch_result must be an object")
    return result, head


def branch_path_exists(root: Path, branch_head: str, rel: str) -> bool:
    safe = safe_rel(rel, "artifact_ref")
    proc = run_git(root, "cat-file", "-e", f"{branch_head}:{safe}", check=False)
    return proc.returncode == 0


def branch_blob_oid(
    root: Path,
    branch_head: str,
    rel: str,
    *,
    required: bool = True,
) -> str | None:
    safe = safe_rel(rel, "evidence path")
    proc = run_git(root, "rev-parse", f"{branch_head}:{safe}", check=False)
    oid = proc.stdout.strip() if proc.returncode == 0 else ""
    if oid:
        return oid
    if required:
        raise ParallelBranchFinalizeError(f"evidence path missing at {branch_head}: {safe}")
    return None


def _evidence_fingerprint(
    *,
    result_path: str,
    result_blob_oid: str,
    artifacts: list[dict[str, Any]],
) -> str:
    payload = {
        "result": {"path": result_path, "blob_oid": result_blob_oid},
        "artifacts": [
            {"path": str(item["path"]), "blob_oid": item.get("blob_oid")}
            for item in sorted(artifacts, key=lambda item: str(item["path"]))
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_evidence_identity(
    root: Path,
    *,
    task: dict[str, Any],
    branch_head: str,
    result_ref: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not branch_head:
        raise ParallelBranchFinalizeError("branch_head required for immutable evidence identity")
    result_path = safe_rel(result_ref, "result_ref")
    result_blob_oid = branch_blob_oid(root, branch_head, result_path, required=True)
    assert result_blob_oid is not None

    refs = result.get("artifact_refs")
    artifact_refs = refs if isinstance(refs, list) else []
    artifacts: list[dict[str, Any]] = []
    for raw in artifact_refs:
        if not isinstance(raw, str) or not raw.strip():
            raise ParallelBranchFinalizeError("invalid artifact_ref")
        path = safe_rel(raw, "artifact_ref")
        blob_oid = branch_blob_oid(root, branch_head, path, required=False)
        artifacts.append(
            {
                "path": path,
                "blob_oid": blob_oid,
                "immutable_ref": (
                    f"github://{REPO_NAME}/{branch_head}/{path}" if blob_oid else None
                ),
            }
        )
    artifacts.sort(key=lambda item: str(item["path"]))
    fingerprint = _evidence_fingerprint(
        result_path=result_path,
        result_blob_oid=result_blob_oid,
        artifacts=artifacts,
    )
    work_branch = str(task.get("work_branch") or "").strip()
    return {
        "v": 1,
        "repository": REPO_NAME,
        "work_branch": work_branch,
        "validated_commit": branch_head,
        "content_fingerprint": fingerprint,
        "result": {
            "path": result_path,
            "blob_oid": result_blob_oid,
            "immutable_ref": f"github://{REPO_NAME}/{branch_head}/{result_path}",
        },
        "artifacts": artifacts,
    }


def set_condition(
    cl: dict[str, Any],
    cond_id: str,
    state: str,
    detail: str,
    evidence_ref: str,
    *,
    source: str | None = None,
) -> None:
    for item in cl.get("conditions") or []:
        if isinstance(item, dict) and item.get("id") == cond_id:
            item["state"] = state
            item["detail"] = detail
            item["evidence_ref"] = evidence_ref
            if source is not None:
                item["source"] = source
            return


def _condition(cl: dict[str, Any], cond_id: str) -> dict[str, Any] | None:
    for item in cl.get("conditions") or []:
        if isinstance(item, dict) and item.get("id") == cond_id:
            return item
    return None


def lane_condition_id(task: dict[str, Any]) -> str:
    lane = str(task.get("lane_id") or "")
    if not lane.startswith("lane-"):
        raise ParallelBranchFinalizeError("task lane_id missing")
    suffix = lane.split("-", 1)[1]
    if not suffix.isdigit():
        raise ParallelBranchFinalizeError("task lane_id invalid")
    return f"lane{suffix}"


def evidence_uri(
    task: dict[str, Any],
    result_ref: str,
    *,
    branch_head: str | None = None,
) -> str:
    ref = branch_head or str(task["work_branch"])
    return f"github://{REPO_NAME}/{ref}/{result_ref}"


def _positive_generation(value: Any, label: str) -> int:
    try:
        generation = int(value)
    except (TypeError, ValueError) as exc:
        raise ParallelBranchFinalizeError(f"{label} must be a positive integer") from exc
    if generation < 1:
        raise ParallelBranchFinalizeError(f"{label} must be a positive integer")
    return generation


def validate_identity(
    task: dict[str, Any],
    backend: dict[str, Any],
    result: dict[str, Any],
    *,
    foreground: dict[str, Any] | None = None,
) -> None:
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise ParallelBranchFinalizeError("task task_id missing")
    if str(backend.get("task_id") or "").strip() != task_id:
        raise ParallelBranchFinalizeError("backend task_id mismatch")
    if str(backend.get("scope") or "") not in {"", "backend_execution"}:
        raise ParallelBranchFinalizeError("backend scope mismatch")

    parent_task_id = str(task.get("parent_task_id") or "").strip()
    if foreground is not None and parent_task_id:
        if str(foreground.get("task_id") or "").strip() != parent_task_id:
            raise ParallelBranchFinalizeError("foreground parent task_id mismatch")
        if str(foreground.get("scope") or "") not in {"", "foreground_supervision"}:
            raise ParallelBranchFinalizeError("foreground scope mismatch")

    if str(result.get("task_id") or "").strip() != task_id:
        raise ParallelBranchFinalizeError("branch_result task_id mismatch")
    status = str(result.get("status") or "")
    if status not in TERMINAL:
        raise ParallelBranchFinalizeError(f"branch_result status is not terminal: {status or '<missing>'}")

    result_dispatch = result.get("dispatch")
    current = backend.get("dispatch")
    if not isinstance(result_dispatch, dict) or not isinstance(current, dict):
        raise ParallelBranchFinalizeError("branch/backend dispatch missing")

    current_dispatch_id = str(current.get("dispatch_id") or "").strip()
    result_dispatch_id = str(result_dispatch.get("dispatch_id") or "").strip()
    current_fence = str(current.get("fence_token") or "").strip()
    result_fence = str(result_dispatch.get("fence_token") or "").strip()
    if not current_dispatch_id or not result_dispatch_id:
        raise ParallelBranchFinalizeError("dispatch_id must be nonempty")
    if not current_fence or not result_fence:
        raise ParallelBranchFinalizeError("fence_token must be nonempty")
    current_generation = _positive_generation(current.get("generation"), "backend dispatch generation")
    result_generation = _positive_generation(result_dispatch.get("generation"), "branch_result dispatch generation")

    if result_dispatch_id != current_dispatch_id:
        raise ParallelBranchFinalizeError("stale branch_result dispatch_id")
    if result_generation != current_generation:
        raise ParallelBranchFinalizeError("stale branch_result generation")
    if result_fence != current_fence:
        raise ParallelBranchFinalizeError("stale branch_result fence_token")

def validate_pass_contract(
    task: dict[str, Any],
    result: dict[str, Any],
    *,
    artifact_exists: Callable[[str], bool],
) -> None:
    contract = task.get("branch_result_contract")
    required = []
    if isinstance(contract, dict):
        required = [str(x) for x in contract.get("required_fields") or [] if str(x)]
    missing = [key for key in required if key not in result or result.get(key) is None]
    if missing:
        raise ParallelBranchFinalizeError(
            "PASS branch_result missing required field(s): " + ", ".join(sorted(missing))
        )

    refs = result.get("artifact_refs")
    if not isinstance(refs, list) or not refs:
        raise ParallelBranchFinalizeError("PASS branch_result requires artifact_refs")
    for rel in refs:
        if not isinstance(rel, str) or not rel.strip():
            raise ParallelBranchFinalizeError("invalid artifact_ref")
        if not artifact_exists(rel):
            raise ParallelBranchFinalizeError(f"branch artifact missing: {rel}")


def _error_kind(result: dict[str, Any], status: str) -> str:
    blocker = result.get("blocker")
    if isinstance(blocker, dict) and str(blocker.get("kind") or ""):
        return str(blocker["kind"])
    return "parallel_branch_blocked" if status == "BLOCKED" else "parallel_branch_error"


def _identity_fingerprint(identity: Any) -> str:
    if not isinstance(identity, dict):
        return ""
    return str(identity.get("content_fingerprint") or "")


def _validate_evidence_identity(
    identity: dict[str, Any] | None,
    *,
    task: dict[str, Any],
    result_ref: str,
    branch_head: str | None,
) -> None:
    if identity is None:
        return
    if str(identity.get("repository") or "") != REPO_NAME:
        raise ParallelBranchFinalizeError("evidence identity repository mismatch")
    if str(identity.get("work_branch") or "") != str(task.get("work_branch") or ""):
        raise ParallelBranchFinalizeError("evidence identity work_branch mismatch")
    result = identity.get("result")
    if not isinstance(result, dict) or str(result.get("path") or "") != safe_rel(result_ref, "result_ref"):
        raise ParallelBranchFinalizeError("evidence identity result path mismatch")
    if branch_head and str(identity.get("validated_commit") or "") != branch_head:
        raise ParallelBranchFinalizeError("evidence identity commit mismatch")
    if not _identity_fingerprint(identity):
        raise ParallelBranchFinalizeError("evidence identity content_fingerprint missing")


def _generic_parent_error(
    state: str,
    lane_items: list[dict[str, Any]],
    fallback_ref: str,
) -> dict[str, Any]:
    lane = next(
        (item for item in lane_items if str(item.get("state") or "") == state),
        None,
    )
    evidence_ref = str((lane or {}).get("evidence_ref") or fallback_ref)
    summary = str((lane or {}).get("detail") or f"parallel branch aggregate {state}")
    return {
        "kind": "parallel_branch_error" if state == "ERROR" else "parallel_branch_blocked",
        "summary": summary,
        "evidence_ref": evidence_ref,
    }


def _downstream_terminals(
    foreground: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any]]]:
    terminals: list[tuple[str, str, dict[str, Any]]] = []
    for cond_id in ("reducer", "final_acceptance"):
        item = _condition(foreground, cond_id)
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "")
        if state not in {"ERROR", "BLOCKED"}:
            continue
        if str(item.get("source") or "") == AGGREGATE_SOURCE:
            continue
        terminals.append((cond_id, state, item))
    return terminals


def _terminal_precedence(states: list[str]) -> str | None:
    if "ERROR" in states:
        return "ERROR"
    if "BLOCKED" in states:
        return "BLOCKED"
    return None


def _downstream_parent_error(
    downstream: list[tuple[str, str, dict[str, Any]]],
    state: str,
    fallback_ref: str,
) -> dict[str, Any]:
    chosen = next(item for _cond_id, item_state, item in downstream if item_state == state)
    existing_ref = str(chosen.get("evidence_ref") or fallback_ref)
    return {
        "kind": "downstream_parallel_error" if state == "ERROR" else "downstream_parallel_blocked",
        "summary": str(chosen.get("detail") or f"downstream {state}"),
        "evidence_ref": existing_ref,
    }


def _is_independent_terminal(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        str(item.get("state") or "") in {"ERROR", "BLOCKED"}
        and str(item.get("source") or "") != AGGREGATE_SOURCE
    )


def _project_branch_owned_downstream(
    foreground: dict[str, Any],
    *,
    branch_state: str,
    evidence_ref: str,
) -> None:
    reducer = _condition(foreground, "reducer")
    if isinstance(reducer, dict) and not _is_independent_terminal(reducer):
        reducer_state = str(reducer.get("state") or "")
        reducer_source = str(reducer.get("source") or "")
        if branch_state == "ERROR":
            set_condition(
                foreground, "reducer", "ERROR", "reducer blocked by parallel branch ERROR",
                evidence_ref, source=AGGREGATE_SOURCE,
            )
        elif branch_state == "BLOCKED":
            set_condition(
                foreground, "reducer", "BLOCKED", "reducer blocked by parallel branch prerequisite",
                evidence_ref, source=AGGREGATE_SOURCE,
            )
        elif branch_state == "PASS":
            if reducer_state == "WAIT" or (
                reducer_source == AGGREGATE_SOURCE
                and reducer_state in {"ERROR", "BLOCKED", "WAIT"}
            ):
                set_condition(
                    foreground, "reducer", "READY", "parallel barrier satisfied",
                    evidence_ref, source=AGGREGATE_SOURCE,
                )
        elif reducer_source == AGGREGATE_SOURCE and reducer_state in {"READY", "ERROR", "BLOCKED"}:
            set_condition(
                foreground, "reducer", "WAIT", "awaiting remaining fenced branch results",
                evidence_ref, source=AGGREGATE_SOURCE,
            )

    final_acceptance = _condition(foreground, "final_acceptance")
    if isinstance(final_acceptance, dict) and not _is_independent_terminal(final_acceptance):
        fa_state = str(final_acceptance.get("state") or "")
        fa_source = str(final_acceptance.get("source") or "")
        if branch_state == "ERROR":
            set_condition(
                foreground, "final_acceptance", "ERROR", "parallel branch aggregate ERROR",
                evidence_ref, source=AGGREGATE_SOURCE,
            )
        elif branch_state == "BLOCKED":
            set_condition(
                foreground, "final_acceptance", "BLOCKED", "parallel branch aggregate BLOCKED",
                evidence_ref, source=AGGREGATE_SOURCE,
            )
        elif fa_source == AGGREGATE_SOURCE and fa_state in {"ERROR", "BLOCKED"}:
            set_condition(
                foreground, "final_acceptance", "WAIT", "awaiting reducer/final acceptance",
                evidence_ref, source=AGGREGATE_SOURCE,
            )


def recompute_parent_aggregate(
    foreground: dict[str, Any],
    *,
    current_status: str,
    current_backend_error: dict[str, Any] | None,
    evidence_ref: str,
    now: str,
) -> None:
    lane_items = [
        item
        for item in foreground.get("conditions") or []
        if isinstance(item, dict) and str(item.get("id") or "").startswith("lane")
    ]
    lane_states = {
        str(item.get("id")): str(item.get("state") or "")
        for item in lane_items
    }
    has_error = any(state == "ERROR" for state in lane_states.values())
    has_blocked = any(state == "BLOCKED" for state in lane_states.values())
    all_green = bool(lane_states) and all(state == "GREEN" for state in lane_states.values())

    if has_error:
        branch_state = "ERROR"
    elif has_blocked:
        branch_state = "BLOCKED"
    elif all_green:
        branch_state = "PASS"
    else:
        branch_state = "RUNNING"

    downstream = _downstream_terminals(foreground)
    downstream_state = _terminal_precedence([state for _cond_id, state, _item in downstream])
    parent_state = _terminal_precedence(
        [state for state in (branch_state, downstream_state) if state in {"ERROR", "BLOCKED"}]
    )

    foreground["parallel_branch_aggregate"] = {
        "v": 1,
        "state": branch_state,
        "lane_states": lane_states,
        "downstream_terminal_state": downstream_state,
        "parent_terminal_state": parent_state,
        "updated_at": now,
    }

    barrier_state = {
        "ERROR": "ERROR",
        "BLOCKED": "BLOCKED",
        "PASS": "GREEN",
        "RUNNING": "WAIT",
    }[branch_state]
    barrier_detail = {
        "ERROR": "parallel branch aggregate ERROR",
        "BLOCKED": "parallel branch aggregate BLOCKED",
        "PASS": "all fenced branch results accepted",
        "RUNNING": "awaiting remaining fenced branch results",
    }[branch_state]
    set_condition(
        foreground,
        "barrier",
        barrier_state,
        barrier_detail,
        evidence_ref,
        source=AGGREGATE_SOURCE,
    )

    # Branch aggregation may project reducer/final state only when those
    # conditions are not independently terminal. Independent ownership wins.
    _project_branch_owned_downstream(
        foreground,
        branch_state=branch_state,
        evidence_ref=evidence_ref,
    )

    if parent_state == "ERROR":
        foreground["overall"] = "ERROR"
        if downstream_state == "ERROR":
            existing_error = foreground.get("error")
            downstream_refs = {
                str(item.get("evidence_ref") or "")
                for _cond_id, state, item in downstream
                if state == "ERROR" and str(item.get("evidence_ref") or "")
            }
            if (
                not isinstance(existing_error, dict)
                or (
                    downstream_refs
                    and str(existing_error.get("evidence_ref") or "") not in downstream_refs
                )
            ):
                foreground["error"] = _downstream_parent_error(
                    downstream,
                    "ERROR",
                    evidence_ref,
                )
        elif current_status == "ERROR" and isinstance(current_backend_error, dict):
            foreground["error"] = dict(current_backend_error)
        else:
            foreground["error"] = _generic_parent_error("ERROR", lane_items, evidence_ref)
        return

    if parent_state == "BLOCKED":
        foreground["overall"] = "BLOCKED"
        if downstream_state == "BLOCKED":
            existing_error = foreground.get("error")
            downstream_refs = {
                str(item.get("evidence_ref") or "")
                for _cond_id, state, item in downstream
                if state == "BLOCKED" and str(item.get("evidence_ref") or "")
            }
            if (
                not isinstance(existing_error, dict)
                or (
                    downstream_refs
                    and str(existing_error.get("evidence_ref") or "") not in downstream_refs
                )
            ):
                foreground["error"] = _downstream_parent_error(
                    downstream,
                    "BLOCKED",
                    evidence_ref,
                )
        elif current_status == "BLOCKED" and isinstance(current_backend_error, dict):
            foreground["error"] = dict(current_backend_error)
        else:
            foreground["error"] = _generic_parent_error("BLOCKED", lane_items, evidence_ref)
        return

    foreground["error"] = None
    foreground["overall"] = "RUNNING"
    final_acceptance = _condition(foreground, "final_acceptance")
    if (
        isinstance(final_acceptance, dict)
        and str(final_acceptance.get("state") or "") == "GREEN"
    ):
        foreground["overall"] = "GREEN"

def apply_branch_result(
    *,
    task: dict[str, Any],
    backend: dict[str, Any],
    foreground: dict[str, Any],
    result: dict[str, Any],
    result_ref: str,
    artifact_exists: Callable[[str], bool],
    now: str | None = None,
    branch_head: str | None = None,
    evidence_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_identity(task, backend, result, foreground=foreground)
    status = str(result["status"])
    if status == "PASS":
        validate_pass_contract(task, result, artifact_exists=artifact_exists)

    _validate_evidence_identity(
        evidence_identity,
        task=task,
        result_ref=result_ref,
        branch_head=branch_head,
    )

    lane_cond = lane_condition_id(task)
    identity_result = (evidence_identity or {}).get("result")
    identity_ref = str(identity_result.get("immutable_ref") or "") if isinstance(identity_result, dict) else ""
    uri = identity_ref or evidence_uri(task, result_ref, branch_head=branch_head)
    summary = str(result.get("summary") or f"parallel branch {status}")
    at = now or utc_now()
    dispatch = backend["dispatch"]

    expected_dispatch_state = {
        "PASS": "DONE",
        "BLOCKED": "BLOCKED",
        "ERROR": "ERROR",
    }[status]
    condition_state = {
        "PASS": "GREEN",
        "BLOCKED": "BLOCKED",
        "ERROR": "ERROR",
    }[status]

    current_state = str(dispatch.get("state") or "")
    accepted = backend.get("accepted_evidence")
    if current_state == expected_dispatch_state:
        if isinstance(accepted, dict) and evidence_identity is not None:
            old_fp = _identity_fingerprint(accepted)
            new_fp = _identity_fingerprint(evidence_identity)
            if old_fp != new_fp:
                raise ParallelBranchFinalizeError(
                    "same-dispatch branch evidence changed after acceptance"
                )
            accepted_result = accepted.get("result")
            accepted_ref = str(
                accepted_result.get("immutable_ref") if isinstance(accepted_result, dict) else ""
            ) or str(backend.get("result_ref") or uri)
            return {
                "outcome": status,
                "already_finalized": True,
                "evidence_ref": accepted_ref,
                "lane_condition": lane_cond,
                "accepted_evidence": accepted,
            }
        if str(backend.get("result_ref") or "") == uri:
            return {
                "outcome": status,
                "already_finalized": True,
                "evidence_ref": uri,
                "lane_condition": lane_cond,
            }

    if current_state in {"DONE", "ERROR", "BLOCKED", "CANCELLED"}:
        raise ParallelBranchFinalizeError(
            f"backend dispatch already terminal with different projection: {current_state}"
        )

    dispatch["state"] = expected_dispatch_state
    dispatch["lease_expires_at"] = None
    dispatch["wait_ref"] = None
    backend["dispatch"] = dispatch
    backend["overall"] = condition_state
    backend["result_ref"] = uri
    backend["wait_ref"] = None
    backend["updated_at"] = at
    if evidence_identity is not None:
        backend["accepted_evidence"] = json.loads(json.dumps(evidence_identity))

    set_condition(backend, "semantic_work", condition_state, summary, uri)
    set_condition(backend, "branch_output", condition_state, summary, uri)
    set_condition(
        backend, "barrier_acceptance", condition_state,
        "fenced branch result accepted" if status == "PASS" else summary, uri,
    )
    scheduling = backend.get("scheduling")
    if isinstance(scheduling, dict):
        scheduling["completed_at"] = result.get("completed_at") or at

    if status == "PASS":
        backend["error"] = None
    else:
        backend["error"] = {
            "kind": _error_kind(result, status),
            "summary": summary,
            "evidence_ref": uri,
        }

    set_condition(foreground, lane_cond, condition_state, summary, uri)
    foreground["updated_at"] = at
    recompute_parent_aggregate(
        foreground,
        current_status=status,
        current_backend_error=backend.get("error") if isinstance(backend.get("error"), dict) else None,
        evidence_ref=uri,
        now=at,
    )

    return {
        "outcome": status,
        "already_finalized": False,
        "evidence_ref": uri,
        "lane_condition": lane_cond,
        "dispatch_state": expected_dispatch_state,
        "accepted_evidence": backend.get("accepted_evidence"),
    }


def finalize_task(
    task_id: str,
    *,
    root: Path = ROOT,
    remote: str = "origin",
) -> dict[str, Any]:
    task_path = root / "tasks" / f"{task_id}.json"
    task = load_json(task_path)
    if str(task.get("task_id") or "") != task_id:
        raise ParallelBranchFinalizeError("task identity mismatch")
    if str(task.get("kind") or "") != "parallel_semantic_branch":
        raise ParallelBranchFinalizeError("task is not a parallel_semantic_branch")

    work_branch = str(task.get("work_branch") or "").strip()
    if not work_branch:
        raise ParallelBranchFinalizeError("task work_branch missing")
    backend_rel = safe_rel(task.get("backend_cl"), "backend_cl")
    foreground_rel = safe_rel(task.get("foreground_cl"), "foreground_cl")
    result_rel = branch_result_ref(task)

    backend_path = root / backend_rel
    foreground_path = root / foreground_rel
    backend = load_json(backend_path)
    foreground = load_json(foreground_path)
    result, branch_head = fetch_branch_result(
        root,
        remote=remote,
        branch=work_branch,
        result_ref=result_rel,
    )
    evidence_identity = build_evidence_identity(
        root,
        task=task,
        branch_head=branch_head,
        result_ref=result_rel,
        result=result,
    )

    out = apply_branch_result(
        task=task,
        backend=backend,
        foreground=foreground,
        result=result,
        result_ref=result_rel,
        artifact_exists=lambda rel: branch_path_exists(root, branch_head, rel),
        branch_head=branch_head,
        evidence_identity=evidence_identity,
    )
    if not out["already_finalized"]:
        write_json(backend_path, backend)
        write_json(foreground_path, foreground)

    return {
        **out,
        "task_id": task_id,
        "work_branch": work_branch,
        "branch_head": branch_head,
        "branch_result_ref": result_rel,
        "backend_cl": backend_rel,
        "foreground_cl": foreground_rel,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a fenced parallel semantic branch result")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()
    try:
        out = finalize_task(args.task_id, remote=args.remote)
    except ParallelBranchFinalizeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
