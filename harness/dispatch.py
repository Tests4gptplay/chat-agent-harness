#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe_repo_path(value: str) -> Path:
    rel = Path(str(value))
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit("path must be repository-relative")
    out = (ROOT / rel).resolve()
    out.relative_to(ROOT.resolve())
    return out


def continuation_identity(
    task_id: str,
    generation: int,
    action_id: str,
    round_no: int,
    result_id: str,
) -> tuple[str, str, str]:
    seed = f"{task_id}|{generation}|{action_id}|{round_no}|{result_id}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return (
        f"dispatch-{digest[:24]}",
        f"fence-{digest[24:56]}",
        f"wake-{digest[:32]}",
    )


def continue_after_result(
    backend_cl_path: Path,
    action_path: Path,
    result_path: Path,
    root: Path = ROOT,
) -> dict[str, Any]:
    bg = load_json(backend_cl_path)
    action = load_json(action_path)
    result = load_json(result_path)

    if not bool(action.get("worker_continuation")):
        return {"scheduled": False, "reason": "action_does_not_require_worker_continuation"}

    task_id = str(action.get("task_id") or "")
    if not task_id or bg.get("task_id") != task_id or result.get("task_id") != task_id:
        raise SystemExit("task identity mismatch while scheduling continuation")
    if result.get("action_id") != action.get("action_id") or result.get("round") != action.get("round"):
        raise SystemExit("result identity mismatch while scheduling continuation")

    dispatch = bg.get("dispatch")
    action_id = str(action.get("action_id") or "")
    seeded = not isinstance(dispatch, dict)
    if seeded:
        # A known deterministic executor may run before any semantic Worker is
        # scheduled. Its durable completion seeds generation 1 of the semantic
        # continuation instead of requiring an unnecessary pre-executor AI turn.
        generation = 1
    else:
        state = str(dispatch.get("state") or "")
        if state != "WAIT_RESULT":
            raise SystemExit(f"continuation requires WAIT_RESULT, got {state or '<missing>'}")
        wait_ref = str(dispatch.get("wait_ref") or "")
        if wait_ref and wait_ref != action_id:
            raise SystemExit(f"WAIT_RESULT wait_ref mismatch: expected {action_id}, got {wait_ref}")
        generation = int(dispatch.get("generation") or 0) + 1
    result_id = str(result.get("result_id") or "")
    dispatch_id, fence_token, wake_id = continuation_identity(
        task_id,
        generation,
        action_id,
        int(action.get("round") or 0),
        result_id,
    )
    result_ref = result_path.resolve().relative_to(root.resolve()).as_posix()
    now = utc_now()

    bg["dispatch"] = {
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
        "continuation_ref": result_ref,
        "wait_ref": None,
    }
    bg["overall"] = "READY"
    bg["result_ref"] = result_ref
    bg["wait_ref"] = None
    bg["error"] = None
    bg["updated_at"] = now
    write_json(backend_cl_path, bg)

    return {
        "scheduled": True,
        "task_id": task_id,
        "backend_cl": backend_cl_path.resolve().relative_to(root.resolve()).as_posix(),
        "dispatch_id": dispatch_id,
        "dispatch_generation": generation,
        "fence_token": fence_token,
        "wake_id": wake_id,
        "result_ref": result_ref,
        "seeded_from_executor": seeded,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="CAH semantic scheduler dispatch transitions")
    sub = p.add_subparsers(dest="cmd", required=True)

    cont = sub.add_parser("continue")
    cont.add_argument("--backend-cl", required=True)
    cont.add_argument("--action", required=True)
    cont.add_argument("--result", required=True)

    args = p.parse_args()
    if args.cmd == "continue":
        out = continue_after_result(
            safe_repo_path(args.backend_cl),
            safe_repo_path(args.action),
            safe_repo_path(args.result),
        )
        print(json.dumps(out, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
