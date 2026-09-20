#!/usr/bin/env python3
"""Measure AI startup/routing context cost without invoking an LLM.

Reports exact repository bytes for canonical state + state.next_reads and validates
machine-readable routing paths. Runtime is local script wall time only; it is not
a model-token or model-latency estimate.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_size(rel: str) -> int:
    p = ROOT / rel
    return p.stat().st_size if p.is_file() else -1


def collect_repo_map_paths(repo_map: dict) -> list[str]:
    paths: set[str] = set()
    authority = repo_map.get("authority", {})
    for value in authority.values():
        if isinstance(value, str):
            paths.add(value)
    for module in repo_map.get("modules", {}).values():
        for key in ("contracts", "shared", "tests"):
            for value in module.get(key, []):
                if "*" not in value:
                    paths.add(value)
        for value in module.get("owns", []):
            if "*" not in value and not value.endswith("/"):
                paths.add(value)
    for read_set in repo_map.get("read_sets", {}).values():
        for key in ("primary", "expand_on_failure"):
            for value in read_set.get(key, []):
                if "*" not in value:
                    paths.add(value)
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    state_path = ROOT / "state" / "chatgpt.json"
    map_path = ROOT / "ai" / "repo-map.json"
    state = load_json(state_path)
    repo_map = load_json(map_path)

    next_reads = list(state.get("next_reads", []))
    missing_next = [p for p in next_reads if file_size(p) < 0]
    next_sizes = {p: file_size(p) for p in next_reads if file_size(p) >= 0}

    routed_paths = collect_repo_map_paths(repo_map)
    missing_map = [p for p in routed_paths if file_size(p) < 0]

    hot_start = state.get("hot_start") or {}
    hot_router = hot_start.get("router")
    hot_read_set = hot_start.get("expand_read_set")
    hot_start_errors = []
    if hot_start:
        if hot_router != "ai/repo-map.json":
            hot_start_errors.append("hot_start.router must be ai/repo-map.json")
        if hot_read_set not in repo_map.get("read_sets", {}):
            hot_start_errors.append(f"unknown hot_start.expand_read_set: {hot_read_set}")

    state_bytes = state_path.stat().st_size
    next_read_bytes = sum(next_sizes.values())
    result = {
        "v": 1,
        "state_bytes": state_bytes,
        "next_reads_count": len(next_reads),
        "next_reads_bytes": next_read_bytes,
        "startup_total_bytes": state_bytes + next_read_bytes,
        "hot_path_bytes": state_bytes,
        "next_reads": next_sizes,
        "repo_map_bytes": map_path.stat().st_size,
        "repo_map_paths_checked": len(routed_paths),
        "missing_next_reads": missing_next,
        "missing_repo_map_paths": missing_map,
        "hot_start_errors": hot_start_errors,
        "audit_runtime_ms": round((time.perf_counter() - started) * 1000, 3),
        "runtime_scope": "local filesystem parse/stat only; excludes LLM and network latency"
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")

    if args.check and (missing_next or missing_map or hot_start_errors):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
