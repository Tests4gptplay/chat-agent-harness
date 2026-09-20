#!/usr/bin/env python3
"""Small, dependency-free wake envelope helpers."""
from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_WAKE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_LANE_ID_RE = re.compile(r"^lane-[0-9]{2,}$")
_PROJECT_KEY_RE = re.compile(r"^g-p-[A-Za-z0-9]+$")
_KIND_RE = re.compile(r"^[A-Za-z0-9._:-]{3,80}$")
_ALLOWED_STATES = {"NEED_AGENT", "NEED_USER", "BLOCKED", "DONE"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def deterministic_wake_id(
    project_id: str,
    *,
    state: str = "NEED_AGENT",
    repo: str | None = None,
    run_id: str | int | None = None,
    result_ref: str | None = None,
    lane_id: str | None = None,
    worker_project_key: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
    backend_cl: str | None = None,
    dispatch_id: str | None = None,
    dispatch_generation: int | None = None,
    fence_token: str | None = None,
    attachment_ref: str | None = None,
) -> str | None:
    """Return a stable wake id when the request names a durable result.

    The same logical result maps to the same UUIDv5 across transports and process
    restarts. Calls without run_id/result_ref remain one-shot and use a random id.
    """
    if run_id is None and not result_ref:
        return None
    identity = {
        "project_id": project_id,
        "state": state,
        "repo": repo or "",
        "run_id": "" if run_id is None else str(run_id),
        "result_ref": result_ref or "",
        "lane_id": lane_id or "",
        "worker_project_key": worker_project_key or "",
        "kind": kind or "",
        "task_id": task_id or "",
        "backend_cl": backend_cl or "",
        "dispatch_id": dispatch_id or "",
        "dispatch_generation": "" if dispatch_generation is None else int(dispatch_generation),
        "fence_token": fence_token or "",
        "attachment_ref": attachment_ref or "",
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"git-agent-harness:wake:{canonical}"))


def make_wake(
    project_id: str,
    *,
    state: str = "NEED_AGENT",
    wake_id: str | None = None,
    repo: str | None = None,
    run_id: str | int | None = None,
    result_ref: str | None = None,
    lane_id: str | None = None,
    worker_project_key: str | None = None,
    kind: str | None = None,
    task_id: str | None = None,
    backend_cl: str | None = None,
    dispatch_id: str | None = None,
    dispatch_generation: int | None = None,
    fence_token: str | None = None,
    attachment_ref: str | None = None,
) -> dict[str, Any]:
    wake = {
        "v": 1,
        "wake_id": wake_id or str(uuid.uuid4()),
        "project_id": project_id,
        "state": state,
        "created_at": utc_now(),
    }
    if repo:
        wake["repo"] = repo
    if run_id is not None:
        wake["run_id"] = str(run_id)
    if result_ref:
        wake["result_ref"] = result_ref
    if lane_id:
        wake["lane_id"] = lane_id
    if worker_project_key:
        wake["worker_project_key"] = worker_project_key
    if kind:
        wake["kind"] = kind
    if task_id:
        wake["task_id"] = task_id
    if backend_cl:
        wake["backend_cl"] = backend_cl
    if dispatch_id:
        wake["dispatch_id"] = dispatch_id
    if dispatch_generation is not None:
        wake["dispatch_generation"] = int(dispatch_generation)
    if fence_token:
        wake["fence_token"] = fence_token
    if attachment_ref:
        wake["attachment_ref"] = attachment_ref
    validate_wake(wake)
    return wake


def validate_wake(wake: dict[str, Any]) -> None:
    if wake.get("v") != 1:
        raise ValueError("wake.v must equal 1")
    wake_id = wake.get("wake_id")
    if not isinstance(wake_id, str) or not _WAKE_ID_RE.fullmatch(wake_id):
        raise ValueError("wake_id must be 8-128 safe characters")
    project_id = wake.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 128:
        raise ValueError("project_id must be a non-empty string <=128 chars")
    state = wake.get("state")
    if state not in _ALLOWED_STATES:
        raise ValueError(f"state must be one of {sorted(_ALLOWED_STATES)}")
    created_at = wake.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("created_at is required")
    for optional in ("repo", "run_id", "result_ref", "backend_cl"):
        value = wake.get(optional)
        if value is not None and (not isinstance(value, str) or len(value) > 512):
            raise ValueError(f"{optional} must be a string <=512 chars")
    task_id = wake.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id or len(task_id) > 256):
        raise ValueError("task_id must be a non-empty string <=256 chars")
    dispatch_id = wake.get("dispatch_id")
    if dispatch_id is not None and (not isinstance(dispatch_id, str) or not _WAKE_ID_RE.fullmatch(dispatch_id)):
        raise ValueError("dispatch_id must be 8-128 safe characters")
    dispatch_generation = wake.get("dispatch_generation")
    if dispatch_generation is not None and (not isinstance(dispatch_generation, int) or dispatch_generation < 1):
        raise ValueError("dispatch_generation must be a positive integer")
    fence_token = wake.get("fence_token")
    if fence_token is not None and (not isinstance(fence_token, str) or len(fence_token) < 8 or len(fence_token) > 256):
        raise ValueError("fence_token must be 8-256 chars")
    dispatch_fields = [wake.get("backend_cl"), dispatch_id, dispatch_generation, fence_token]
    if any(value is not None for value in dispatch_fields) and not all(value is not None for value in dispatch_fields):
        raise ValueError("backend_cl, dispatch_id, dispatch_generation and fence_token must appear together")
    lane_id = wake.get("lane_id")
    if lane_id is not None and (not isinstance(lane_id, str) or not _LANE_ID_RE.fullmatch(lane_id)):
        raise ValueError("lane_id must look like lane-00")
    project_key = wake.get("worker_project_key")
    if project_key is not None and (not isinstance(project_key, str) or not _PROJECT_KEY_RE.fullmatch(project_key)):
        raise ValueError("worker_project_key must look like g-p-...")
    kind = wake.get("kind")
    if kind is not None and (not isinstance(kind, str) or not _KIND_RE.fullmatch(kind)):
        raise ValueError("kind contains unsafe characters")
    attachment_ref = wake.get("attachment_ref")
    if attachment_ref is not None:
        if not isinstance(attachment_ref, str) or not attachment_ref or len(attachment_ref) > 512:
            raise ValueError("attachment_ref must be a non-empty repository path <=512 chars")
        rel = Path(attachment_ref.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("attachment_ref must be a safe repository-relative path")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-/")
        if any(ch not in allowed for ch in attachment_ref):
            raise ValueError("attachment_ref contains unsafe characters")


def marker(wake: dict[str, Any]) -> str:
    validate_wake(wake)
    project = wake["project_id"].replace(" ", "_")
    return f"GAH_WAKE v=1 id={wake['wake_id']} project={project}"


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    mk = sub.add_parser("make")
    mk.add_argument("--project-id", required=True)
    mk.add_argument("--state", default="NEED_AGENT", choices=sorted(_ALLOWED_STATES))
    mk.add_argument("--wake-id")
    mk.add_argument("--repo")
    mk.add_argument("--run-id")
    mk.add_argument("--result-ref")
    mk.add_argument("--lane-id")
    mk.add_argument("--worker-project-key")
    mk.add_argument("--kind")
    mk.add_argument("--task-id")
    mk.add_argument("--backend-cl")
    mk.add_argument("--dispatch-id")
    mk.add_argument("--dispatch-generation", type=int)
    mk.add_argument("--fence-token")
    mk.add_argument("--attachment-ref")
    mk.add_argument("--out")

    va = sub.add_parser("validate")
    va.add_argument("path")

    ma = sub.add_parser("marker")
    ma.add_argument("path")

    args = p.parse_args()
    if args.cmd == "make":
        wake = make_wake(
            args.project_id,
            state=args.state,
            wake_id=args.wake_id,
            repo=args.repo,
            run_id=args.run_id,
            result_ref=args.result_ref,
            lane_id=args.lane_id,
            worker_project_key=args.worker_project_key,
            kind=args.kind,
            task_id=args.task_id,
            backend_cl=args.backend_cl,
            dispatch_id=args.dispatch_id,
            dispatch_generation=args.dispatch_generation,
            fence_token=args.fence_token,
            attachment_ref=args.attachment_ref,
        )
        text = json.dumps(wake, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    if args.cmd == "validate":
        wake = json.loads(Path(args.path).read_text(encoding="utf-8"))
        validate_wake(wake)
        print(json.dumps({"ok": True, "wake_id": wake["wake_id"]}))
        return 0
    if args.cmd == "marker":
        wake = json.loads(Path(args.path).read_text(encoding="utf-8"))
        print(marker(wake))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
