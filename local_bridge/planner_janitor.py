#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .topology import _run, utc_now
    from .planner_memory import (
        PlannerMemoryError,
        classify_cleanup_candidate,
        plan_cleanup_apply,
        validate_cleanup_manifest,
    )
except ImportError:
    from topology import _run, utc_now
    from planner_memory import (
        PlannerMemoryError,
        classify_cleanup_candidate,
        plan_cleanup_apply,
        validate_cleanup_manifest,
    )


class PlannerJanitorError(ValueError):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


def _safe_task_id(value: Any) -> str:
    task_id = str(value or "").strip()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    if not 3 <= len(task_id) <= 128 or any(ch not in allowed for ch in task_id):
        raise PlannerJanitorError("CLEANUP_TASK_ID_INVALID")
    return task_id


def _safe_repo_rel(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise PlannerJanitorError("CLEANUP_GIT_PATH_INVALID")
    return text


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _canonical_local_target(path: str, root: str) -> tuple[Path, Path]:
    root_path = Path(root).expanduser().resolve(strict=False)
    raw = Path(path).expanduser()
    target = raw.resolve(strict=False)
    if not _path_within(target, root_path):
        raise PlannerJanitorError("CLEANUP_OUTSIDE_MANAGED_ROOT")
    # Resolve every existing parent independently. On Windows, junctions and
    # symlinks are both represented by a resolved path that may escape root.
    probe = raw
    while probe != probe.parent and not probe.exists():
        probe = probe.parent
    if probe.exists():
        resolved_probe = probe.resolve(strict=True)
        if not _path_within(resolved_probe, root_path):
            raise PlannerJanitorError("CLEANUP_OUTSIDE_MANAGED_ROOT")
    return target, root_path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_identity(path: Path) -> str:
    payload = json.dumps({"path": str(path.resolve(strict=False))}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _managed_roots(store: Any, task_id: str) -> dict[str, str]:
    # Deletion authority is code-derived from the canonical task id, never
    # widened by manifest/request data. Generic bridge/runtime/log roots are
    # intentionally NOT deletion roots because they may contain other tasks.
    task_id = _safe_task_id(task_id)
    return {
        "task_runtime": str((store.runtime / "tasks" / task_id).resolve()),
        "task_logs": str((store.logs / "tasks" / task_id).resolve()),
        "task_extension_events": str((store.extension_events / "tasks" / task_id).resolve()),
        "task_errors": str((store.errors / "tasks" / task_id).resolve()),
    }


def _read_json(store: Any, ref: str, rel: str) -> dict[str, Any]:
    try:
        value = json.loads(store._git("show", f"{ref}:{rel}").stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PlannerJanitorError("CLEANUP_MANIFEST_READ_FAILED", rel) from exc
    if not isinstance(value, dict):
        raise PlannerJanitorError("CLEANUP_MANIFEST_INVALID")
    return value


def _safe_git_cleanup_path(task_id: str, value: Any) -> str:
    task_id = _safe_task_id(task_id)
    rel = _safe_repo_rel(value)
    allowed = (
        f"memory/planner/{task_id}/",
        f"evidence/{task_id}/",
        f"cases/{task_id}/",
    )
    if not any(rel.startswith(prefix) for prefix in allowed):
        raise PlannerJanitorError("CLEANUP_GIT_PATH_NOT_TASK_OWNED", rel)
    return rel


def _git_blob_sha(store: Any, ref: str, rel: str) -> str | None:
    try:
        return store._git("rev-parse", f"{ref}:{rel}").stdout.strip()
    except subprocess.SubprocessError:
        return None



def _terminal_status_from_backend(value: dict[str, Any]) -> str | None:
    overall = str(value.get("overall") or value.get("status") or "").upper()
    dispatch = value.get("dispatch") if isinstance(value.get("dispatch"), dict) else {}
    dispatch_state = str(dispatch.get("state") or "").upper()
    if overall in {"BLOCKED", "WAIT", "WAIT_DEP", "WAIT_RESULT"} or dispatch_state == "BLOCKED":
        return "BLOCKED"
    if overall in {"ERROR", "FAILURE", "FAILED"} or dispatch_state == "ERROR":
        return "ERROR"
    if overall in {"CANCELLED", "CANCELED", "REVOKED"} or dispatch_state in {"CANCELLED", "CANCELED", "REVOKED"}:
        return "CANCELLED"
    if overall in {"GREEN", "PASS", "DONE", "SUCCESS", "COMPLETE", "COMPLETE_ACCEPTED"} or dispatch_state == "DONE":
        return "DONE"
    return None


def _read_worktree_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_worktree_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact_terminal_control_records(
    worktree: Path,
    *,
    task_id: str,
    runtime: dict[str, Any],
    terminal_refs: dict[str, Any],
    completed_at: str,
) -> dict[str, Any]:
    """Compact exact task-owned control records after semantic authority is closed."""
    task_id = _safe_task_id(task_id)
    changed: list[str] = []
    owner_ids: set[str] = {task_id}
    final_result_ref = str(terminal_refs.get("final_result_ref") or "")
    cleanup_result_ref = str(terminal_refs.get("cleanup_result_ref") or "")

    task_path = worktree / "tasks" / f"{task_id}.json"
    task = _read_worktree_json(task_path)
    if task is not None:
        compact = {
            "v": int(task.get("v") or 1),
            "task_id": task_id,
            "status": "DONE",
            "result_ref": final_result_ref or task.get("result_ref"),
            "cleanup_result_ref": cleanup_result_ref or None,
            "completed_at": completed_at,
            "history_ref": f"git history of tasks/{task_id}.json",
        }
        for key in ("tracking_issue", "kind"):
            if task.get(key) is not None:
                compact[key] = task.get(key)
        _write_worktree_json(task_path, {k: v for k, v in compact.items() if v is not None})
        changed.append(f"tasks/{task_id}.json")

    plan_path = worktree / "tasks" / f"{task_id}.plan.json"
    plan = _read_worktree_json(plan_path)
    if plan is not None:
        compact_plan = {
            "v": int(plan.get("v") or 1),
            "task_id": task_id,
            "status": "DONE",
            "result_ref": final_result_ref or None,
            "cleanup_result_ref": cleanup_result_ref or None,
            "completed_at": completed_at,
            "history_ref": f"git history of tasks/{task_id}.plan.json",
        }
        if plan.get("plan_revision") is not None:
            compact_plan["plan_revision"] = plan.get("plan_revision")
        _write_worktree_json(plan_path, {k: v for k, v in compact_plan.items() if v is not None})
        changed.append(f"tasks/{task_id}.plan.json")

    for child in runtime.get("owned_children") or []:
        if not isinstance(child, dict):
            continue
        child_id_raw = str(child.get("child_task_id") or "").strip()
        if not child_id_raw:
            continue
        try:
            child_id = _safe_task_id(child_id_raw)
        except PlannerJanitorError:
            continue
        try:
            backend_ref = _safe_repo_rel(child.get("backend_cl") or f"cl/{child_id}.backend.json")
        except PlannerJanitorError:
            continue
        backend = _read_worktree_json(worktree / backend_ref)
        if backend is None:
            continue
        child_status = _terminal_status_from_backend(backend)
        if child_status is None:
            continue
        owner_ids.add(child_id)
        child_path = worktree / "tasks" / f"{child_id}.json"
        child_task = _read_worktree_json(child_path)
        if child_task is None:
            continue
        dispatch = backend.get("dispatch") if isinstance(backend.get("dispatch"), dict) else {}
        compact_child = {
            "v": int(child_task.get("v") or 1),
            "task_id": child_id,
            "parent_task_id": task_id,
            "status": child_status,
            "backend_cl": backend_ref,
            "result_ref": backend.get("result_ref") or backend_ref,
            "dispatch": {
                "dispatch_id": dispatch.get("dispatch_id"),
                "generation": dispatch.get("generation"),
                "fence_token": dispatch.get("fence_token"),
                "state": dispatch.get("state"),
            },
            "completed_at": completed_at,
            "history_ref": f"git history of tasks/{child_id}.json",
        }
        _write_worktree_json(child_path, compact_child)
        changed.append(f"tasks/{child_id}.json")

    handoff_root = worktree / "state" / "handoffs"
    if handoff_root.exists():
        for path in sorted(handoff_root.glob("*.json")):
            packet = _read_worktree_json(path)
            if packet is None or str(packet.get("task_id") or "") not in owner_ids:
                continue
            rel = path.relative_to(worktree).as_posix()
            dispatch = packet.get("dispatch") if isinstance(packet.get("dispatch"), dict) else {}
            compact_handoff = {
                "v": int(packet.get("v") or 1),
                "kind": "worker_handoff_tombstone",
                "task_id": str(packet.get("task_id") or ""),
                "status": "RETIRED",
                "handoff_id": packet.get("handoff_id") or packet.get("outgoing_pool_id"),
                "lane_id": packet.get("lane_id"),
                "generation": packet.get("generation") or dispatch.get("generation"),
                "fence_token": packet.get("fence_token") or dispatch.get("fence_token"),
                "reason": packet.get("reason"),
                "retired_at": completed_at,
                "history_ref": f"git history of {rel}",
            }
            _write_worktree_json(path, {k: v for k, v in compact_handoff.items() if v is not None})
            changed.append(rel)

    receipt_root = worktree / "state" / "request_receipts"
    if receipt_root.exists():
        for path in sorted(receipt_root.glob("*.json")):
            receipt = _read_worktree_json(path)
            if receipt is None:
                continue
            try:
                source_ref = _safe_repo_rel(receipt.get("path"))
            except PlannerJanitorError:
                continue
            source = _read_worktree_json(worktree / source_ref)
            source_task_id = str((source or {}).get("task_id") or "")
            if source_task_id not in owner_ids:
                continue
            rel = path.relative_to(worktree).as_posix()
            prior_phase = str(receipt.get("phase") or "")
            compact_receipt = {
                "v": int(receipt.get("v") or 1),
                "kind": receipt.get("kind"),
                "path": source_ref,
                "blob_oid": receipt.get("blob_oid"),
                "request_key": receipt.get("request_key"),
                "phase": prior_phase if prior_phase in {"DONE", "SUPERSEDED", "REJECTED"} else "SUPERSEDED",
                "task_id": source_task_id,
                "prior_phase": prior_phase or None,
                "terminal_compacted": True,
                "retired_at": completed_at,
            }
            _write_worktree_json(path, {k: v for k, v in compact_receipt.items() if v is not None})
            changed.append(rel)

    foreground_path = worktree / "state" / "foreground-next-task.json"
    foreground = _read_worktree_json(foreground_path)
    if foreground is not None and str(foreground.get("task_id") or "") in owner_ids:
        _write_worktree_json(foreground_path, {
            "v": int(foreground.get("v") or 1),
            "kind": foreground.get("kind") or "foreground_next_task",
            "task_id": str(foreground.get("task_id") or ""),
            "status": "DONE",
            "result_ref": final_result_ref or foreground.get("result_ref"),
            "completed_at": completed_at,
        })
        changed.append("state/foreground-next-task.json")

    # Task Cell state is hot authority, not long-term history. Once semantic
    # authority is closed, keeping this file can leave enabled Planner control,
    # bound roles or pending inbox events looking live. Git history preserves
    # the retired state; remove the exact task-owned hot-state file.
    task_cell_path = worktree / "state" / "task_cells" / f"{task_id}.json"
    if task_cell_path.exists() and task_cell_path.is_file():
        task_cell_path.unlink()
        changed.append(f"state/task_cells/{task_id}.json")

    return {
        "status": "COMPACTED",
        "task_id": task_id,
        "owned_task_ids": sorted(owner_ids),
        "compacted_paths": sorted(set(changed)),
        "completed_at": completed_at,
    }


def _drop_worktree(store: Any, worktree: Path) -> None:
    try:
        store._git("worktree", "remove", "--force", str(worktree))
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
    try:
        store._git("worktree", "prune", "--expire", "now")
    except Exception:
        pass


def _delete_local(candidate: dict[str, Any], *, task_id: str, managed_roots: dict[str, str], manifest: dict[str, Any]) -> dict[str, Any]:
    # Re-run the P1 policy classifier immediately before mutation.
    planned = classify_cleanup_candidate(
        candidate,
        task_id=task_id,
        managed_roots=managed_roots,
        protected_refs=manifest.get("protected_refs_snapshot") or [],
        rollback_window_refs=manifest.get("rollback_window_refs") or [],
        dependency_checks=manifest.get("dependency_checks") or {},
        protected_conversation_ids=manifest.get("protected_conversation_ids") or [],
    )
    status = str(planned.get("status") or "")
    if status != "WOULD_DELETE":
        return {**planned, "mutated": False}

    root_id = str(candidate.get("managed_root_id_or_task_cell_project_key") or "")
    root = managed_roots.get(root_id)
    if not root:
        raise PlannerJanitorError("CLEANUP_MANAGED_ROOT_UNKNOWN", root_id)
    target, _ = _canonical_local_target(
        str(candidate.get("path_or_exact_resource_identity") or ""),
        root,
    )
    # Revalidate existence + identity at the last possible moment.
    if not target.exists():
        return {"candidate_id": candidate["candidate_id"], "status": "ALREADY_ABSENT", "mutated": False}
    expected = str(candidate.get("expected_blob_sha_or_identity_digest") or "")
    if expected:
        actual = _file_sha256(target) if target.is_file() else _directory_identity(target)
        if actual != expected:
            raise PlannerJanitorError("CLEANUP_IDENTITY_DIGEST_MISMATCH", str(candidate.get("candidate_id") or ""))
    reclaimed = target.stat().st_size if target.is_file() else 0
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    if target.exists():
        raise PlannerJanitorError("CLEANUP_DELETE_FAILED", str(candidate.get("candidate_id") or ""))
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "DELETED",
        "path": str(target),
        "bytes": reclaimed,
        "mutated": True,
    }


def _apply_git_deletes(store: Any, base_sha: str, manifest: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    if not candidates:
        return [], None
    task_id = _safe_task_id(manifest.get("task_id"))
    worktree = store.runtime / f"planner-janitor-git-{task_id}-{int(manifest.get('cleanup_generation') or 0)}"
    _drop_worktree(store, worktree)
    store._git("worktree", "add", "--force", "--detach", str(worktree), base_sha)
    _run(worktree, "config", "user.name", "gah-local-bridge")
    _run(worktree, "config", "user.email", "gah-local-bridge@example.invalid")
    results: list[dict[str, Any]] = []
    mutated: list[str] = []
    try:
        for candidate in candidates:
            # Re-run P1 classification at mutation time. GIT_PATH has no local
            # filesystem root, so P1 validates task ownership/path/protected set.
            planned = classify_cleanup_candidate(
                candidate,
                task_id=task_id,
                managed_roots=_managed_roots(store, task_id),
                protected_refs=manifest.get("protected_refs_snapshot") or [],
                rollback_window_refs=manifest.get("rollback_window_refs") or [],
                dependency_checks=manifest.get("dependency_checks") or {},
                protected_conversation_ids=manifest.get("protected_conversation_ids") or [],
            )
            if str(planned.get("status") or "") != "WOULD_DELETE":
                results.append({**planned, "mutated": False})
                continue
            rel = _safe_git_cleanup_path(task_id, candidate.get("path_or_exact_resource_identity"))
            # The janitor further restricts Git deletes to explicit task-owned
            # Planner/evidence/case prefixes, independent of manifest claims.
            blob = _git_blob_sha(store, base_sha, rel)
            if blob is None:
                results.append({"candidate_id": candidate["candidate_id"], "status": "ALREADY_ABSENT", "mutated": False})
                continue
            expected = str(candidate.get("expected_blob_sha_or_identity_digest") or "")
            if expected and expected != blob:
                raise PlannerJanitorError("CLEANUP_IDENTITY_DIGEST_MISMATCH", str(candidate.get("candidate_id") or ""))
            path = worktree / rel
            # Refetch is unnecessary inside this locked exact-base transaction;
            # verify the checked-out object is the exact canonical blob instead.
            checked = _run(worktree, "rev-parse", f"HEAD:{rel}").stdout.strip()
            if checked != blob:
                raise PlannerJanitorError("CLEANUP_GIT_BLOB_CHANGED", rel)
            _run(worktree, "rm", "--", rel)
            mutated.append(rel)
            results.append({"candidate_id": candidate["candidate_id"], "status": "DELETED", "path": rel, "blob_sha": blob, "mutated": True})

        if not mutated:
            return results, None
        _run(worktree, "commit", "-m", f"Planner cleanup Git paths for {task_id} [skip ci]")
        commit_sha = _run(worktree, "rev-parse", "HEAD").stdout.strip()
        _run(worktree, "push", store.git_remote, f"HEAD:{store.git_branch}")
        return results, commit_sha
    finally:
        _drop_worktree(store, worktree)


def execute_planner_cleanup(store: Any, req: dict[str, Any]) -> dict[str, Any]:
    store._safe_client(req.get("client_id"))
    task_id = _safe_task_id(req.get("task_id"))
    manifest_ref = _safe_repo_rel(req.get("manifest_ref"))
    dry_run = bool(req.get("dry_run", False))
    started_digest = str(req.get("started_manifest_digest") or "").strip() or None

    with store.git_lock:
        store._git("fetch", "--quiet", "--no-tags", store.git_remote, store.git_branch)
        base_sha = store._git("rev-parse", "FETCH_HEAD").stdout.strip()
        manifest = _read_json(store, base_sha, manifest_ref)
        digest = validate_cleanup_manifest(
            manifest,
            expected_task_id=task_id,
            expected_control_epoch=int(req.get("control_epoch") or manifest.get("control_epoch") or 0),
            started_manifest_digest=started_digest,
        )
        roots = _managed_roots(store, task_id)
        protected_conversation_ids = [
            str(x) for x in req.get("protected_conversation_ids") or []
        ]
        planned = plan_cleanup_apply(
            manifest,
            managed_roots=roots,
            started_manifest_digest=started_digest,
            protected_conversation_ids=protected_conversation_ids,
        )
        if dry_run or planned.get("status") != "DONE":
            return {
                "ok": planned.get("status") == "DONE",
                "dry_run": True,
                "manifest_ref": manifest_ref,
                **planned,
            }

        local_candidates: list[dict[str, Any]] = []
        git_candidates: list[dict[str, Any]] = []
        conversation_results: list[dict[str, Any]] = []
        preserved: list[dict[str, Any]] = []
        candidate_by_id = {
            str(c.get("candidate_id")): c
            for c in list(manifest.get("delete_candidates") or []) + list(manifest.get("preserve_candidates") or [])
            if isinstance(c, dict)
        }

        # Freeze exact candidate set from the validated digest; no target
        # substitution is allowed after cleanup begins.
        for row in planned.get("candidate_results") or []:
            cid = str(row.get("candidate_id") or "")
            candidate = candidate_by_id.get(cid)
            if not candidate:
                raise PlannerJanitorError("CLEANUP_CANDIDATE_SET_CHANGED", cid)
            status = str(row.get("status") or "")
            if status != "WOULD_DELETE":
                preserved.append({**row, "mutated": False})
                continue
            kind = str(candidate.get("resource_kind") or "")
            if kind in {"MANAGED_PATH", "RUNTIME_RECORD"}:
                local_candidates.append(candidate)
            elif kind == "GIT_PATH":
                git_candidates.append(candidate)
            elif kind == "TASK_CELL_CONVERSATION":
                # Browser UI mutation is deliberately not owned by the bridge.
                # Return an exact retirement request for extension execution.
                exact = row.get("exact_identity") or candidate.get("path_or_exact_resource_identity")
                conversation_results.append({
                    "candidate_id": cid,
                    "status": "RETIREMENT_REQUIRED",
                    "exact_identity": exact,
                    "mutated": False,
                })
            else:
                raise PlannerJanitorError("CLEANUP_RESOURCE_KIND_INVALID", kind)

        results = list(preserved)
        failures: list[str] = []
        deleted_bytes = 0
        for candidate in local_candidates:
            try:
                row = _delete_local(candidate, task_id=task_id, managed_roots=roots, manifest=manifest)
                results.append(row)
                deleted_bytes += int(row.get("bytes") or 0)
            except (PlannerJanitorError, PlannerMemoryError) as exc:
                code = getattr(exc, "code", str(exc))
                failures.append(code)
                results.append({"candidate_id": candidate.get("candidate_id"), "status": "FAILED", "error": code, "mutated": False})

        git_commit = None
        if git_candidates and not failures:
            try:
                rows, git_commit = _apply_git_deletes(store, base_sha, manifest, git_candidates)
                results.extend(rows)
            except (PlannerJanitorError, PlannerMemoryError, subprocess.SubprocessError) as exc:
                code = getattr(exc, "code", "CLEANUP_GIT_DELETE_FAILED")
                failures.append(str(code))
                for candidate in git_candidates:
                    if not any(str(row.get("candidate_id") or "") == str(candidate.get("candidate_id") or "") for row in results):
                        results.append({"candidate_id": candidate.get("candidate_id"), "status": "FAILED", "error": str(code), "mutated": False})

        results.extend(conversation_results)
        retirement_pending = bool(conversation_results)
        status = "CLEANUP_PARTIAL" if failures or retirement_pending else "CLEANUP_COMPLETE"
        return {
            "ok": not failures,
            "dry_run": False,
            "task_id": task_id,
            "control_epoch": int(manifest.get("control_epoch") or 0),
            "manifest_ref": manifest_ref,
            "manifest_digest": digest,
            "cleanup_generation": int(manifest.get("cleanup_generation") or 0),
            "candidate_results": results,
            "deleted_bytes": deleted_bytes,
            "git_cleanup_commit": git_commit,
            "conversation_retirements": conversation_results,
            "failure_codes": sorted(set(failures)),
            "status": status,
            "semantic_authority_reactivated": False,
            "completed_at": utc_now(),
        }
