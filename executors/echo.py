#!/usr/bin/env python3
"""Deterministic smoke executor.

Reads an action JSON, echoes payload.message, and writes a compact result JSON.
It exists only to prove the harness handoff without external dependencies.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    with open(args.action, "r", encoding="utf-8") as f:
        action = json.load(f)

    message = action.get("payload", {}).get("message")
    status = "PASS" if isinstance(message, str) and message else "FAIL"
    summary = f"echoed: {message}" if status == "PASS" else "payload.message missing or empty"

    result = {
        "v": 1,
        "result_id": f"result-{action['action_id']}",
        "action_id": action["action_id"],
        "task_id": action["task_id"],
        "round": action["round"],
        "status": status,
        "summary": summary,
        "evidence": [
            {"type": "echo", "value": message},
            {"type": "timestamp_utc", "value": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        ],
        "artifacts": [],
        "fault_boundary": "executor:echo" if status != "PASS" else "none"
    }

    Path(args.result).parent.mkdir(parents=True, exist_ok=True)
    with open(args.result, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(json.dumps({"status": status, "summary": summary}, ensure_ascii=False))
    raise SystemExit(0 if status == "PASS" else 1)


if __name__ == "__main__":
    main()
