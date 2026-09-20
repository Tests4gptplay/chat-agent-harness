#!/usr/bin/env python3
"""Minimal state transition helper for git-agent-harness.

No external dependencies. This deliberately does not call an LLM, GitHub, or an
executor. It only validates basic handoff invariants and emits the next state.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return value


def write(path: str, value: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require(obj: dict[str, Any], keys: list[str], label: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SystemExit(f"{label}: missing required keys: {', '.join(missing)}")


def plan(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    require(state, ["agent", "phase"], "state")
    require(action, ["action_id", "task_id", "round", "executor", "operation", "payload"], "action")
    if state["phase"] not in {"IDLE", "NEED_AGENT", "VERIFY"}:
        raise SystemExit(f"cannot plan from phase {state['phase']}")
    active_task = state.get("active_task")
    if active_task not in (None, action["task_id"]):
        raise SystemExit(f"task mismatch: state={active_task!r} action={action['task_id']!r}")

    out = dict(state)
    out.update(
        updated=now(),
        phase="READY_TO_EXECUTE",
        active_task=action["task_id"],
        active_action=action["action_id"],
        next_action=None,
        writeback_reason=f"Prepared action {action['action_id']} for executor {action['executor']}",
    )
    return out


def observe(state: dict[str, Any], result: dict[str, Any], result_ref: str) -> dict[str, Any]:
    require(state, ["agent", "phase", "active_action"], "state")
    require(result, ["result_id", "action_id", "task_id", "round", "status", "summary"], "result")
    if state["phase"] not in {"READY_TO_EXECUTE", "EXECUTING"}:
        raise SystemExit(f"cannot observe from phase {state['phase']}")
    if result["action_id"] != state.get("active_action"):
        raise SystemExit(
            f"action mismatch: state={state.get('active_action')!r} result={result['action_id']!r}"
        )
    if state.get("active_task") not in (None, result["task_id"]):
        raise SystemExit("task mismatch between state and result")
    if result["status"] not in {"PASS", "FAIL", "ERROR", "BLOCKED"}:
        raise SystemExit(f"unknown result status {result['status']!r}")

    out = dict(state)
    out.update(
        updated=now(),
        phase="BLOCKED" if result["status"] == "BLOCKED" else "NEED_AGENT",
        last_result=result_ref,
        next_action=None,
        writeback_reason=f"Observed {result['status']} from {result['result_id']}",
    )
    if result.get("fault_boundary"):
        out["fault_boundary"] = result["fault_boundary"]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--state", required=True)
    p_plan.add_argument("--action", required=True)
    p_plan.add_argument("--out", required=True)

    p_obs = sub.add_parser("observe")
    p_obs.add_argument("--state", required=True)
    p_obs.add_argument("--result", required=True)
    p_obs.add_argument("--out", required=True)

    args = parser.parse_args()
    state = load(args.state)

    if args.cmd == "plan":
        out = plan(state, load(args.action))
    else:
        out = observe(state, load(args.result), args.result)

    write(args.out, out)
    print(json.dumps({"phase": out["phase"], "active_task": out.get("active_task"), "active_action": out.get("active_action"), "last_result": out.get("last_result")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
