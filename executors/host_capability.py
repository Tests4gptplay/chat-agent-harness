#!/usr/bin/env python3
"""Resolve one reusable host capability and emit a standard CAH result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.capabilities import STATE_AVAILABLE, STATE_NEED_HOST_CONFIG, resolve_capability


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("action must be a JSON object")
    return value


def safe_rel(value: Any, label: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a repository-relative path")
    return path.as_posix()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(action: dict[str, Any], result_path: Path) -> dict[str, Any]:
    payload = action.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    capability_id = str(payload.get("capability_id") or "").strip()
    if not capability_id:
        raise ValueError("payload.capability_id required")
    projection_rel = safe_rel(
        payload.get("projection_artifact") or f"evidence/host-capabilities/{action['task_id']}.json",
        "projection_artifact",
    )
    local_root_raw = str(payload.get("local_root") or "").strip()
    local_root = Path(local_root_raw) if local_root_raw else None

    projection, _private = resolve_capability(
        capability_id,
        local_root=local_root,
        persist=True,
    )
    projection_path = ROOT / projection_rel
    write_json(projection_path, projection)

    state = str(projection.get("state") or "")
    if state == STATE_AVAILABLE:
        status = "PASS"
        summary = f"host capability {capability_id} is verified and available"
        fault = "none"
    elif state == STATE_NEED_HOST_CONFIG:
        status = "BLOCKED"
        summary = str(projection.get("prompt") or f"host capability {capability_id} needs configuration")
        fault = "NEED_HOST_CONFIG"
    else:
        status = "BLOCKED"
        summary = f"host capability {capability_id} is unavailable"
        fault = "HOST_CAPABILITY_UNAVAILABLE"

    evidence = [
        {"type": "capability_id", "value": capability_id},
        {"type": "capability_state", "value": state},
        {"type": "capability_version", "value": projection.get("version")},
        {"type": "capability_source", "value": projection.get("source")},
        {"type": "capability_projection_ref", "value": projection_rel},
    ]
    if projection.get("prompt"):
        evidence.append({"type": "user_prompt", "value": projection["prompt"]})

    return {
        "v": 1,
        "result_id": f"result-{action['action_id']}",
        "action_id": action["action_id"],
        "task_id": action["task_id"],
        "round": action["round"],
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "artifacts": [projection_rel],
        "fault_boundary": fault,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    action = load_json(Path(args.action))
    try:
        result = execute(action, Path(args.result))
    except Exception as exc:
        result = {
            "v": 1,
            "result_id": f"result-{action.get('action_id', 'host-capability')}",
            "action_id": action.get("action_id", "host-capability"),
            "task_id": action.get("task_id", "host-capability"),
            "round": int(action.get("round") or 1),
            "status": "ERROR",
            "summary": f"host capability executor failed: {type(exc).__name__}: {exc}",
            "evidence": [],
            "artifacts": [],
            "fault_boundary": "host_capability_executor",
        }
    write_json(Path(args.result), result)
    print(json.dumps({"status": result["status"], "summary": result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
