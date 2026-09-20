#!/usr/bin/env python3
"""Emit one retry-safe wake to the local loopback bridge."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.wake import deterministic_wake_id, make_wake  # noqa: E402


def post_json(url: str, payload: dict, timeout: int = 5) -> dict:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "git-agent-harness-local/1",
            "X-GAH-Bridge": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"local bridge transport error: {exc}") from exc
    result = json.loads(raw)
    if not result.get("ok"):
        raise RuntimeError(f"local bridge rejected wake: {result}")
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project-id", required=True)
    p.add_argument("--state", default="NEED_AGENT")
    p.add_argument("--wake-id")
    p.add_argument("--repo")
    p.add_argument("--run-id")
    p.add_argument("--result-ref")
    p.add_argument("--lane-id")
    p.add_argument("--worker-project-key")
    p.add_argument("--kind")
    p.add_argument("--task-id")
    p.add_argument("--backend-cl")
    p.add_argument("--dispatch-id")
    p.add_argument("--dispatch-generation", type=int)
    p.add_argument("--fence-token")
    p.add_argument("--attachment-ref")
    p.add_argument("--endpoint", default=os.environ.get("GAH_LOCAL_ENDPOINT", "http://127.0.0.1:8765/api"))
    p.add_argument("--timeout", type=int, default=5)
    args = p.parse_args()

    stable_id = args.wake_id or deterministic_wake_id(
        args.project_id,
        state=args.state,
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
    retry_safe = stable_id is not None
    wake = make_wake(
        args.project_id,
        state=args.state,
        wake_id=stable_id,
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
    try:
        result = post_json(args.endpoint, {"op": "emit", "wake": wake}, timeout=args.timeout)
    except RuntimeError as exc:
        print(json.dumps({
            "ok": False,
            "wake": wake,
            "error": str(exc),
            "retry_safe_same_command": retry_safe,
        }, ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True,
        "wake": wake,
        "local": result,
        "retry_safe_same_command": retry_safe,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
