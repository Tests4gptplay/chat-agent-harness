#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from typing import Any


NODE_TERMINAL = {"DONE", "ERROR", "BLOCKED", "CANCELLED"}


class ParallelSchedulerError(ValueError):
    pass


def _node_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node["node_id"]): node for node in state.get("nodes", [])}


def _enabled_lanes(topology: dict[str, Any]) -> list[dict[str, Any]]:
    lanes = []
    seen = set()
    for raw in topology.get("lanes", []):
        if not isinstance(raw, dict) or not raw.get("enabled"):
            continue
        lane_id = str(raw.get("lane_id") or "")
        project_key = str(raw.get("project_key") or "")
        if not lane_id or not project_key:
            raise ParallelSchedulerError("enabled lane requires lane_id and project_key")
        if lane_id in seen:
            raise ParallelSchedulerError(f"duplicate lane_id: {lane_id}")
        seen.add(lane_id)
        lanes.append({"lane_id": lane_id, "project_key": project_key})
    lanes.sort(key=lambda item: item["lane_id"])
    return lanes


def _dispatch_identity(task_id: str, node_id: str, generation: int, lane_id: str) -> tuple[str, str]:
    seed = f"{task_id}|{node_id}|{generation}|{lane_id}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return f"dispatch-{digest[:24]}", f"fence-{digest[24:56]}"


def make_state(task: dict[str, Any], topology: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    if not task_id:
        raise ParallelSchedulerError("task_id is required")
    max_parallel = int(task.get("max_parallel") or 1)
    if max_parallel < 1:
        raise ParallelSchedulerError("max_parallel must be >= 1")

    raw_nodes = task.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ParallelSchedulerError("nodes must be a non-empty list")

    seen = set()
    nodes = []
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ParallelSchedulerError("each node must be an object")
        node_id = str(raw.get("node_id") or "")
        if not node_id:
            raise ParallelSchedulerError("node_id is required")
        if node_id in seen:
            raise ParallelSchedulerError(f"duplicate node_id: {node_id}")
        seen.add(node_id)
        depends_on = [str(value) for value in raw.get("depends_on", [])]
        if len(depends_on) != len(set(depends_on)):
            raise ParallelSchedulerError(f"{node_id}: duplicate dependency")
        nodes.append(
            {
                "node_id": node_id,
                "kind": str(raw.get("kind") or "semantic"),
                "depends_on": depends_on,
                "status": "WAIT_DEP" if depends_on else "READY",
                "generation": 0,
                "dispatch": None,
                "result_ref": None,
                "error": None,
                "order": index,
            }
        )

    node_ids = {node["node_id"] for node in nodes}
    for node in nodes:
        missing = [dep for dep in node["depends_on"] if dep not in node_ids]
        if missing:
            raise ParallelSchedulerError(f"{node['node_id']}: unknown dependencies: {missing}")
        if node["node_id"] in node["depends_on"]:
            raise ParallelSchedulerError(f"{node['node_id']}: self dependency")

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {node["node_id"]: node for node in nodes}

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ParallelSchedulerError("dependency cycle detected")
        visiting.add(node_id)
        for dep in by_id[node_id]["depends_on"]:
            visit(dep)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in by_id:
        visit(node_id)

    lanes = _enabled_lanes(topology)
    if not lanes:
        raise ParallelSchedulerError("at least one enabled lane is required")

    return {
        "v": 1,
        "task_id": task_id,
        "status": "READY",
        "max_parallel": max_parallel,
        "nodes": nodes,
        "lanes": {
            lane["lane_id"]: {
                "project_key": lane["project_key"],
                "status": "IDLE",
                "node_id": None,
            }
            for lane in lanes
        },
        "completed_order": [],
    }


def refresh(state: dict[str, Any]) -> None:
    nodes = _node_map(state)

    for node in state["nodes"]:
        if node["status"] != "WAIT_DEP":
            continue
        dep_states = [nodes[dep]["status"] for dep in node["depends_on"]]
        if any(dep in {"ERROR", "BLOCKED", "CANCELLED"} for dep in dep_states):
            node["status"] = "BLOCKED"
            node["error"] = "dependency_failed"
        elif dep_states and all(dep == "DONE" for dep in dep_states):
            node["status"] = "READY"

    statuses = [node["status"] for node in state["nodes"]]
    if any(status == "ERROR" for status in statuses):
        state["status"] = "ERROR"
    elif any(status == "BLOCKED" for status in statuses):
        state["status"] = "BLOCKED"
    elif statuses and all(status == "DONE" for status in statuses):
        state["status"] = "DONE"
    elif any(status == "RUNNING" for status in statuses):
        state["status"] = "RUNNING"
    else:
        state["status"] = "READY"


def dispatch_ready(state: dict[str, Any]) -> list[dict[str, Any]]:
    refresh(state)
    if state["status"] in NODE_TERMINAL:
        return []

    running = sum(1 for node in state["nodes"] if node["status"] == "RUNNING")
    capacity = max(0, int(state["max_parallel"]) - running)
    if capacity == 0:
        return []

    idle_lanes = [
        lane_id
        for lane_id, lane in sorted(state["lanes"].items())
        if lane["status"] == "IDLE"
    ]
    ready_nodes = sorted(
        (node for node in state["nodes"] if node["status"] == "READY"),
        key=lambda node: int(node["order"]),
    )
    count = min(capacity, len(idle_lanes), len(ready_nodes))
    issued: list[dict[str, Any]] = []

    for index in range(count):
        lane_id = idle_lanes[index]
        node = ready_nodes[index]
        lane = state["lanes"][lane_id]
        node["generation"] = int(node["generation"]) + 1
        dispatch_id, fence_token = _dispatch_identity(
            str(state["task_id"]),
            str(node["node_id"]),
            int(node["generation"]),
            lane_id,
        )
        dispatch = {
            "dispatch_id": dispatch_id,
            "generation": node["generation"],
            "fence_token": fence_token,
            "lane_id": lane_id,
            "project_key": lane["project_key"],
            "state": "RUNNING",
        }
        node["dispatch"] = dispatch
        node["status"] = "RUNNING"
        lane["status"] = "RUNNING"
        lane["node_id"] = node["node_id"]
        issued.append(deepcopy(dispatch) | {"node_id": node["node_id"]})

    refresh(state)
    return issued


def complete_node(
    state: dict[str, Any],
    *,
    node_id: str,
    dispatch_id: str,
    fence_token: str,
    result_ref: str,
    status: str = "PASS",
) -> None:
    nodes = _node_map(state)
    if node_id not in nodes:
        raise ParallelSchedulerError(f"unknown node_id: {node_id}")
    node = nodes[node_id]
    if node["status"] != "RUNNING" or not isinstance(node.get("dispatch"), dict):
        raise ParallelSchedulerError(f"{node_id}: not RUNNING")
    dispatch = node["dispatch"]
    if dispatch.get("dispatch_id") != dispatch_id or dispatch.get("fence_token") != fence_token:
        raise ParallelSchedulerError(f"{node_id}: stale or mismatched dispatch fence")

    lane_id = str(dispatch["lane_id"])
    lane = state["lanes"].get(lane_id)
    if not isinstance(lane, dict) or lane.get("node_id") != node_id:
        raise ParallelSchedulerError(f"{node_id}: lane ownership mismatch")

    lane["status"] = "IDLE"
    lane["node_id"] = None
    dispatch["state"] = "DONE" if status == "PASS" else "ERROR"
    node["result_ref"] = result_ref
    if status == "PASS":
        node["status"] = "DONE"
        state["completed_order"].append(node_id)
    else:
        node["status"] = "ERROR"
        node["error"] = f"result_status={status}"
    refresh(state)


def run_dual_lane_smoke() -> dict[str, Any]:
    topology = {
        "v": 1,
        "lanes": [
            {"lane_id": "lane-00", "project_key": "g-p-smoke00", "enabled": True},
            {"lane_id": "lane-01", "project_key": "g-p-smoke01", "enabled": True},
        ],
    }
    task = {
        "v": 1,
        "task_id": "gah-dual-lane-smoke-001",
        "max_parallel": 2,
        "nodes": [
            {"node_id": "branch-a", "kind": "semantic", "depends_on": []},
            {"node_id": "branch-b", "kind": "semantic", "depends_on": []},
            {"node_id": "reduce", "kind": "reducer", "depends_on": ["branch-a", "branch-b"]},
        ],
    }
    state = make_state(task, topology)
    first = dispatch_ready(state)
    if {item["node_id"] for item in first} != {"branch-a", "branch-b"}:
        raise ParallelSchedulerError("smoke: independent branches were not dispatched together")
    if {item["lane_id"] for item in first} != {"lane-00", "lane-01"}:
        raise ParallelSchedulerError("smoke: branches did not occupy distinct lanes")

    by_node = {item["node_id"]: item for item in first}
    complete_node(
        state,
        node_id="branch-a",
        dispatch_id=by_node["branch-a"]["dispatch_id"],
        fence_token=by_node["branch-a"]["fence_token"],
        result_ref="results/smoke-a.json",
    )
    if _node_map(state)["reduce"]["status"] != "WAIT_DEP":
        raise ParallelSchedulerError("smoke: reducer released before barrier completion")
    if dispatch_ready(state):
        raise ParallelSchedulerError("smoke: reducer dispatched while branch-b was incomplete")

    complete_node(
        state,
        node_id="branch-b",
        dispatch_id=by_node["branch-b"]["dispatch_id"],
        fence_token=by_node["branch-b"]["fence_token"],
        result_ref="results/smoke-b.json",
    )
    if _node_map(state)["reduce"]["status"] != "READY":
        raise ParallelSchedulerError("smoke: reducer did not become READY after barrier")

    reducer_dispatches = dispatch_ready(state)
    if len(reducer_dispatches) != 1 or reducer_dispatches[0]["node_id"] != "reduce":
        raise ParallelSchedulerError("smoke: reducer was not dispatched exactly once")
    reducer = reducer_dispatches[0]
    complete_node(
        state,
        node_id="reduce",
        dispatch_id=reducer["dispatch_id"],
        fence_token=reducer["fence_token"],
        result_ref="results/smoke-reduce.json",
    )
    if state["status"] != "DONE":
        raise ParallelSchedulerError("smoke: graph did not reach DONE")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="CAH Stage-1 bounded parallel scheduler core")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke")
    args = parser.parse_args()
    if args.cmd == "smoke":
        print(json.dumps(run_dual_lane_smoke(), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
