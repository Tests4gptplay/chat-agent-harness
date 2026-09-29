from __future__ import annotations

import json
import os
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import BOOTSTRAP_LANES, TASK_CELL


ROLE_NAMES = {"planner", "helper"}


def _pool_key(task_id: str, epoch: int) -> str:
    return f"{task_id}::{int(epoch)}"


class HostState:
    """Small disposable browser-runtime state.

    Durable task ownership stays in Git. This file replaces chrome.storage for
    browser-local bindings and can be reconstructed/reconciled after restart.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _base(self) -> dict[str, Any]:
        return {
            "v": 1,
            "client_id": f"playwright-{uuid.uuid4()}",
            "task_cell": {
                **TASK_CELL,
                "roles": {},
                "planner_successors": {},
            },
            "lanes": {lane["lane_id"]: {**lane, "task_pools": {}} for lane in BOOTSTRAP_LANES},
            "foreground": {"conversation_url": None},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            value = self._base()
            self._atomic_write(value)
            return value
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ValueError("invalid Playwright browser-host state")
        return value

    def _atomic_write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def save(self) -> None:
        self._atomic_write(self.data)

    @property
    def client_id(self) -> str:
        return str(self.data["client_id"])

    def reconcile_lanes(self, canonical: dict[str, Any] | None) -> None:
        if not isinstance(canonical, dict) or not isinstance(canonical.get("lanes"), list):
            return
        prior = self.data.setdefault("lanes", {})
        next_lanes: dict[str, Any] = {}
        for row in canonical["lanes"]:
            if not isinstance(row, dict):
                continue
            lane_id = str(row.get("lane_id") or "")
            project_key = str(row.get("project_key") or "")
            if not lane_id.startswith("lane-") or not project_key.startswith("g-p-"):
                continue
            old = prior.get(lane_id) if isinstance(prior.get(lane_id), dict) else {}
            same_project = old.get("project_key") == project_key
            next_lanes[lane_id] = {
                "lane_id": lane_id,
                "display_name": str(row.get("display_name") or lane_id),
                "project_key": project_key,
                "project_root_url": str(row.get("project_root_url") or ""),
                "enabled": bool(row.get("enabled", True)),
                "pending_remove": bool(row.get("pending_remove", False)),
                "task_pools": deepcopy(old.get("task_pools") or {}) if same_project else {},
            }
        self.data["lanes"] = next_lanes
        self.save()

    def lane(self, lane_id: str) -> dict[str, Any] | None:
        value = self.data.get("lanes", {}).get(lane_id)
        return value if isinstance(value, dict) else None

    def get_pool(self, lane_id: str, task_id: str, epoch: int, create: bool = False) -> dict[str, Any] | None:
        lane = self.lane(lane_id)
        if lane is None:
            return None
        pools = lane.setdefault("task_pools", {})
        key = _pool_key(task_id, epoch)
        pool = pools.get(key)
        if pool is None and create:
            pool = {
                "v": 2,
                "owner_task_id": task_id,
                "owner_control_epoch": int(epoch),
                "current": None,
                "managed": [],
                "handoff": None,
            }
            pools[key] = pool
            self.save()
        return pool if isinstance(pool, dict) else None

    def set_pool(self, lane_id: str, pool: dict[str, Any]) -> None:
        lane = self.lane(lane_id)
        if lane is None:
            raise KeyError(lane_id)
        task_id = str(pool.get("owner_task_id") or "")
        epoch = int(pool.get("owner_control_epoch") or 0)
        if not task_id or epoch < 1:
            raise ValueError("invalid pool owner")
        lane.setdefault("task_pools", {})[_pool_key(task_id, epoch)] = deepcopy(pool)
        self.save()

    def remove_pool(self, lane_id: str, task_id: str, epoch: int) -> bool:
        lane = self.lane(lane_id)
        if lane is None:
            return False
        pools = lane.setdefault("task_pools", {})
        existed = _pool_key(task_id, epoch) in pools
        pools.pop(_pool_key(task_id, epoch), None)
        self.save()
        return existed

    def role_key(self, task_id: str, epoch: int, role: str) -> str:
        role = role.lower()
        if role not in ROLE_NAMES:
            raise ValueError("invalid role")
        return f"{task_id}|{int(epoch)}|{role}"

    def get_role(self, task_id: str, epoch: int, role: str) -> dict[str, Any] | None:
        roles = self.data["task_cell"].setdefault("roles", {})
        value = roles.get(self.role_key(task_id, epoch, role))
        return deepcopy(value) if isinstance(value, dict) else None

    def set_role(self, record: dict[str, Any]) -> dict[str, Any]:
        task_id = str(record.get("task_id") or "")
        epoch = int(record.get("control_epoch") or 0)
        role = str(record.get("role") or "").lower()
        conversation_id = str(record.get("conversation_id") or "")
        if not task_id or epoch < 1 or role not in ROLE_NAMES or not conversation_id:
            raise ValueError("invalid role binding")
        roles = self.data["task_cell"].setdefault("roles", {})
        key = self.role_key(task_id, epoch, role)
        for other_key, other in roles.items():
            if other_key == key or not isinstance(other, dict):
                continue
            if (
                str(other.get("task_id") or "") == task_id
                and int(other.get("control_epoch") or 0) == epoch
                and str(other.get("conversation_id") or "") == conversation_id
            ):
                raise ValueError("Task Cell roles require distinct conversations")
        roles[key] = deepcopy(record)
        self.save()
        return deepcopy(record)

    def clear_role(self, task_id: str, epoch: int, role: str, conversation_id: str = "") -> bool:
        roles = self.data["task_cell"].setdefault("roles", {})
        key = self.role_key(task_id, epoch, role)
        current = roles.get(key)
        if not isinstance(current, dict):
            return False
        if conversation_id and str(current.get("conversation_id") or "") != conversation_id:
            raise ValueError("role conversation mismatch")
        roles.pop(key, None)
        self.save()
        return True

    def clear_task_roles(self, task_id: str, epoch: int) -> int:
        roles = self.data["task_cell"].setdefault("roles", {})
        keys = [
            key for key, value in roles.items()
            if isinstance(value, dict)
            and str(value.get("task_id") or "") == task_id
            and int(value.get("control_epoch") or 0) == int(epoch)
        ]
        for key in keys:
            roles.pop(key, None)
        successors = self.data["task_cell"].setdefault("planner_successors", {})
        successors.pop(f"{task_id}|{int(epoch)}", None)
        self.save()
        return len(keys)

    def get_successor(self, task_id: str, epoch: int) -> dict[str, Any] | None:
        value = self.data["task_cell"].setdefault("planner_successors", {}).get(f"{task_id}|{int(epoch)}")
        return deepcopy(value) if isinstance(value, dict) else None

    def set_successor(self, task_id: str, epoch: int, record: dict[str, Any]) -> None:
        conversation_id = str(record.get("conversation_id") or "")
        active = self.get_role(task_id, epoch, "planner")
        if not active:
            raise ValueError("Cannot bind Planner successor without an active Planner")
        if str(active.get("conversation_id") or "") == conversation_id:
            raise ValueError("Planner successor must use a distinct conversation")

        for role in ROLE_NAMES:
            current = self.get_role(task_id, epoch, role)
            if current and str(current.get("conversation_id") or "") == conversation_id:
                raise ValueError("Planner successor conversation is already used by a Task Cell role")

        successors = self.data["task_cell"].setdefault("planner_successors", {})
        key = f"{task_id}|{int(epoch)}"
        prior = successors.get(key)
        if prior and str(prior.get("conversation_id") or "") != conversation_id:
            raise ValueError("Only one pending Planner successor is allowed")
        successors[key] = deepcopy(record)
        self.save()

    def pop_successor(self, task_id: str, epoch: int) -> dict[str, Any] | None:
        value = self.data["task_cell"].setdefault("planner_successors", {}).pop(f"{task_id}|{int(epoch)}", None)
        self.save()
        return deepcopy(value) if isinstance(value, dict) else None

    def promote_successor(self, task_id: str, epoch: int, conversation_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        successor = self.get_successor(task_id, epoch)
        if not successor or str(successor.get("conversation_id") or "") != conversation_id:
            raise ValueError("successor mismatch")
        predecessor = self.get_role(task_id, epoch, "planner")
        successor["role"] = "planner"
        successor["status"] = "BOUND"
        successor["semantic_authority"] = True
        self.set_role(successor)
        self.pop_successor(task_id, epoch)
        return successor, predecessor
