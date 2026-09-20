#!/usr/bin/env python3
"""Generic recoverable resource-wait transitions for CAH backend CLs."""
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
        raise ValueError(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def resource_wait_ref(capability_id: str) -> str:
    return f"host-capability:{capability_id}"


def enter_resource_wait(
    backend_cl_path: Path,
    *,
    capability_id: str,
    prompt: str,
    projection_ref: str | None = None,
) -> dict[str, Any]:
    bg = load_json(backend_cl_path)
    dispatch = bg.get("dispatch")
    if not isinstance(dispatch, dict):
        raise ValueError("resource wait requires a backend dispatch")
    state = str(dispatch.get("state") or "")
    if state not in {"RUNNING", "WAIT_RESULT", "WAIT_RESOURCE"}:
        raise ValueError(f"resource wait cannot start from {state or '<missing>'}")
    wait_ref = resource_wait_ref(capability_id)
    now = utc_now()
    dispatch["state"] = "WAIT_RESOURCE"
    dispatch["wait_ref"] = wait_ref
    dispatch["lease_expires_at"] = None
    bg["dispatch"] = dispatch
    bg["overall"] = "RUNNING"
    bg["wait_ref"] = wait_ref
    bg["resource_wait"] = {
        "kind": "NEED_HOST_CONFIG",
        "capability_id": capability_id,
        "prompt": prompt,
        "projection_ref": projection_ref,
        "requested_at": now,
    }
    bg["updated_at"] = now
    bg["error"] = None
    write_json(backend_cl_path, bg)
    return {
        "state": "WAIT_RESOURCE",
        "wait_ref": wait_ref,
        "resource_wait": bg["resource_wait"],
    }


def _resume_identity(task_id: str, generation: int, capability_id: str, observed_at: str) -> tuple[str, str, str]:
    seed = f"{task_id}|resource-resume|{generation}|{capability_id}|{observed_at}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return (
        f"dispatch-{digest[:24]}",
        f"fence-{digest[24:56]}",
        f"wake-{digest[:32]}",
    )


def resume_after_capability(
    backend_cl_path: Path,
    projection_path: Path,
) -> dict[str, Any]:
    bg = load_json(backend_cl_path)
    projection = load_json(projection_path)
    dispatch = bg.get("dispatch")
    wait = bg.get("resource_wait")
    if not isinstance(dispatch, dict) or str(dispatch.get("state") or "") != "WAIT_RESOURCE":
        raise ValueError("resource resume requires WAIT_RESOURCE dispatch")
    if not isinstance(wait, dict) or str(wait.get("kind") or "") != "NEED_HOST_CONFIG":
        raise ValueError("resource resume requires NEED_HOST_CONFIG record")

    capability_id = str(wait.get("capability_id") or "")
    if not capability_id or str(projection.get("capability_id") or "") != capability_id:
        raise ValueError("capability identity mismatch")
    if str(projection.get("state") or "") != "AVAILABLE" or projection.get("available") is not True:
        return {
            "scheduled": False,
            "reason": "capability_not_available",
            "capability_id": capability_id,
            "state": str(projection.get("state") or ""),
        }

    task_id = str(bg.get("task_id") or "")
    if not task_id:
        raise ValueError("backend CL task_id missing")
    generation = int(dispatch.get("generation") or 0) + 1
    observed_at = str(projection.get("observed_at") or utc_now())
    dispatch_id, fence_token, wake_id = _resume_identity(
        task_id, generation, capability_id, observed_at
    )
    now = utc_now()
    previous = {
        "dispatch_id": dispatch.get("dispatch_id"),
        "generation": dispatch.get("generation"),
        "fence_token": dispatch.get("fence_token"),
        "state": dispatch.get("state"),
        "wait_ref": dispatch.get("wait_ref"),
    }
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
        "continuation_ref": dispatch.get("continuation_ref"),
        "wait_ref": None,
        "ack_source": None,
    }
    bg["overall"] = "READY"
    bg["wait_ref"] = None
    bg["resource_wait"] = None
    bg["error"] = None
    bg["updated_at"] = now
    write_json(backend_cl_path, bg)
    return {
        "scheduled": True,
        "task_id": task_id,
        "capability_id": capability_id,
        "dispatch_id": dispatch_id,
        "dispatch_generation": generation,
        "fence_token": fence_token,
        "wake_id": wake_id,
        "resumed_from": previous,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="CAH recoverable resource-wait transitions")
    sub = parser.add_subparsers(dest="cmd", required=True)

    enter = sub.add_parser("enter")
    enter.add_argument("--backend-cl", required=True)
    enter.add_argument("--capability-id", required=True)
    enter.add_argument("--prompt", required=True)
    enter.add_argument("--projection-ref")

    resume = sub.add_parser("resume")
    resume.add_argument("--backend-cl", required=True)
    resume.add_argument("--projection", required=True)

    args = parser.parse_args()
    if args.cmd == "enter":
        out = enter_resource_wait(
            Path(args.backend_cl),
            capability_id=args.capability_id,
            prompt=args.prompt,
            projection_ref=args.projection_ref,
        )
    else:
        out = resume_after_capability(Path(args.backend_cl), Path(args.projection))
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
