#!/usr/bin/env python3
"""Loopback-only file-backed wake bridge for the local CAH browser host.

The bridge binds only to 127.0.0.1. Durable task/result state remains in Git;
files under the bridge root are disposable wake transport/runtime state.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PY_ROOT = Path(__file__).resolve().parents[1]
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))
from harness.git_process import run_git

try:
    from .control import begin_lane_clear, complete_lane_clear, complete_lane_pool_reset, complete_task_cell_clear, complete_task_cell_project_clear, complete_task_cell_prompt, control_status
    from .task_cell_roles import complete_task_cell_role_prompt
    from .scheduler import canonical_worker_owner, complete_worker_handoff, reconcile_dispatch_liveness, reconcile_foreground_terminal, semantic_lease_expires_at, stage_action_submit
    from .planner_runtime import begin_foreground_planner_handoff, record_foreground_planner_create_attempt, complete_foreground_planner_handoff_binding, complete_planner_predecessor_retire, complete_planner_successor_bootstrap, complete_planner_successor_promote, enqueue_planner_event, migrate_planner_runtime, planner_final_delivery_status, planner_final_delivery_update, planner_runtime_tick, request_planner_rotation, planner_git_cli_fallback, observe_semantic_turn, sync_semantic_turn, stage_worker_watchdog_helper
    from .planner_janitor import execute_planner_cleanup
    from .planner_control import PlannerControlError, insert_planner_event, make_planner_event
    from .topology import finalize_topology_request, stage_topology_request, topology_status
except ImportError:
    from control import begin_lane_clear, complete_lane_clear, complete_lane_pool_reset, complete_task_cell_clear, complete_task_cell_project_clear, complete_task_cell_prompt, control_status
    from task_cell_roles import complete_task_cell_role_prompt
    from scheduler import canonical_worker_owner, complete_worker_handoff, reconcile_dispatch_liveness, reconcile_foreground_terminal, semantic_lease_expires_at, stage_action_submit
    from planner_runtime import begin_foreground_planner_handoff, record_foreground_planner_create_attempt, complete_foreground_planner_handoff_binding, complete_planner_predecessor_retire, complete_planner_successor_bootstrap, complete_planner_successor_promote, enqueue_planner_event, migrate_planner_runtime, planner_final_delivery_status, planner_final_delivery_update, planner_runtime_tick, request_planner_rotation, planner_git_cli_fallback, observe_semantic_turn, sync_semantic_turn, stage_worker_watchdog_helper
    from planner_janitor import execute_planner_cleanup
    from planner_control import PlannerControlError, insert_planner_event, make_planner_event
    from topology import finalize_topology_request, stage_topology_request, topology_status

ROOT_DEFAULT = Path(os.environ.get("GAH_LOCAL_ROOT", r"__CAH_BRIDGE_ROOT__"))
REPO_ROOT_DEFAULT = Path(os.environ.get("GAH_REPO_ROOT", Path(__file__).resolve().parents[1]))
GIT_REMOTE_DEFAULT = os.environ.get("GAH_GIT_REMOTE", "origin")
GIT_BRANCH_DEFAULT = os.environ.get("GAH_GIT_BRANCH", "main")
HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 8765
MAX_BODY = 128 * 1024
MAX_EVENT_DATA = 16 * 1024
MAX_EVENT_LOG = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
ARTIFACT_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(token in normalized for token in ("secret", "password", "authorization", "cookie", "token")):
                return True
            if _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _task_execution_binding(task: dict[str, Any]) -> tuple[str, str, str]:
    """Return backend CL, lane id and Worker Project key declared by a task.

    Newer task contracts use top-level fields; one-shot/legacy contracts may
    place them under execution_contract or semantic_reduce.
    """
    contract = task.get("execution_contract") if isinstance(task.get("execution_contract"), dict) else {}
    semantic = task.get("semantic_reduce") if isinstance(task.get("semantic_reduce"), dict) else {}
    backend_cl = str(task.get("backend_cl") or contract.get("backend_cl") or "").replace("\\", "/").strip()
    lane_id = str(task.get("lane_id") or contract.get("lane_id") or semantic.get("lane_id") or "").strip()
    project_key = str(
        task.get("worker_project_key")
        or contract.get("worker_project_key")
        or semantic.get("worker_project_key")
        or ""
    ).strip()
    return backend_cl, lane_id, project_key


def _task_worker_owner(task: dict[str, Any], cl: dict[str, Any]) -> tuple[str, int]:
    scheduling = cl.get("scheduling") if isinstance(cl.get("scheduling"), dict) else {}
    owner_task_id = str(
        scheduling.get("owner_task_id")
        or task.get("owner_task_id")
        or task.get("parent_task_id")
        or task.get("task_id")
        or ""
    ).strip()
    owner_control_epoch = int(
        scheduling.get("owner_control_epoch")
        or task.get("owner_control_epoch")
        or 1
    )
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    if not 3 <= len(owner_task_id) <= 128 or any(ch not in allowed for ch in owner_task_id):
        raise ValueError("invalid owner_task_id")
    if owner_control_epoch < 1:
        raise ValueError("invalid owner_control_epoch")
    return owner_task_id, owner_control_epoch


def _task_pool_key(owner_task_id: str, owner_control_epoch: int) -> str:
    return f"{owner_task_id}::{int(owner_control_epoch)}"


class WakeStore:
    def __init__(
        self,
        root: Path,
        repo_root: Path | None = None,
        git_remote: str = GIT_REMOTE_DEFAULT,
        git_branch: str = GIT_BRANCH_DEFAULT,
    ):
        self.root = root.resolve()
        self.repo_root = (repo_root or REPO_ROOT_DEFAULT).resolve()
        self.git_remote = self._safe_git_component(git_remote, "git_remote")
        self.git_branch = self._safe_git_component(git_branch, "git_branch")
        self.inbox = self.root / "wake" / "inbox"
        self.claimed = self.root / "wake" / "claimed"
        self.consumed = self.root / "wake" / "consumed"
        self.runtime = self.root / "runtime"
        self.logs = self.root / "logs"
        self.extension_events = self.root / "extension-events"
        self.errors = self.root / "errors"
        for p in (
            self.inbox,
            self.claimed,
            self.consumed,
            self.runtime,
            self.logs,
            self.extension_events,
            self.errors,
        ):
            p.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.git_lock = threading.RLock()
        self.foreground_task_snapshot: dict[str, Any] | None = None
        self.foreground_task_snapshot_at: str | None = None

    @staticmethod
    def _safe_id(value: Any, name: str) -> str:
        s = str(value or "")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
        if not 8 <= len(s) <= 128 or any(ch not in allowed for ch in s):
            raise ValueError(f"invalid {name}")
        return s

    @staticmethod
    def _safe_repo_rel(value: Any, name: str) -> str:
        s = str(value or "").replace("\\", "/").strip()
        p = Path(s)
        if not s or len(s) > 512 or p.is_absolute() or ".." in p.parts:
            raise ValueError(f"invalid {name}")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-/")
        if any(ch not in allowed for ch in s):
            raise ValueError(f"invalid {name}")
        return s

    @staticmethod
    def _safe_client(value: Any) -> str:
        s = str(value or "")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:@/-")
        if not 3 <= len(s) <= 256 or any(ch not in allowed for ch in s):
            raise ValueError("invalid client_id")
        return s

    @staticmethod
    def _safe_event_name(value: Any) -> str:
        s = str(value or "")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")
        if not 3 <= len(s) <= 80 or any(ch not in allowed for ch in s):
            raise ValueError("invalid event")
        return s

    @staticmethod
    def _safe_git_component(value: Any, name: str) -> str:
        s = str(value or "")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-")
        if not 1 <= len(s) <= 128 or s.startswith("-") or any(ch not in allowed for ch in s):
            raise ValueError(f"invalid {name}")
        return s

    @staticmethod
    def _validate_wake(wake: dict[str, Any]) -> dict[str, Any]:
        if int(wake.get("v", 0)) != 1:
            raise ValueError("wake.v must equal 1")
        WakeStore._safe_id(wake.get("wake_id"), "wake_id")
        if not str(wake.get("project_id") or "").strip():
            raise ValueError("project_id required")
        if str(wake.get("state") or "") not in {"NEED_AGENT", "NEED_USER", "BLOCKED", "DONE"}:
            raise ValueError("invalid state")
        if not str(wake.get("created_at") or "").strip():
            raise ValueError("created_at required")
        return wake

    @staticmethod
    def _foreground_task_view(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        task_id = str(value.get("task_id") or "").strip()
        status = str(value.get("status") or "").strip().upper()
        if not task_id or len(task_id) > 256:
            return None
        if status not in {"RUNNING", "SUCCESS", "FAILURE", "BLOCKED", "CANCELLED"}:
            return None
        started_at = str(value.get("started_at") or "").strip()
        updated_at = str(value.get("updated_at") or "").strip()
        result_ref = str(value.get("result_ref") or "").strip()
        return {
            "task_id": task_id,
            "status": status,
            "started_at": started_at[:128] or None,
            "updated_at": updated_at[:128] or None,
            "result_ref": result_ref[:2048] or None,
        }

    def _path(self, folder: Path, wake_id: str) -> Path:
        return folder / f"{wake_id}.json"

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_git(self.repo_root, *args, timeout=20)

    def _git_bytes(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return run_git(self.repo_root, *args, timeout=20, binary=True)

    def _recover_expired(self) -> None:
        now = now_ms()
        for path in list(self.claimed.glob("*.json")):
            try:
                record = read_json(path)
                wake = self._validate_wake(record["wake"])
                claim = record.get("claim") or {}
                if int(claim.get("expires_at", 0)) > now:
                    continue
                wake_id = wake["wake_id"]
                target = self._path(self.inbox, wake_id)
                if not target.exists() and not self._path(self.consumed, wake_id).exists():
                    atomic_json(target, wake)
                path.unlink(missing_ok=True)
            except Exception:
                # Fail closed: leave malformed claim evidence in place for inspection.
                continue

    def emit(self, wake: dict[str, Any]) -> dict[str, Any]:
        wake = self._validate_wake(wake)
        wake_id = wake["wake_id"]
        with self.lock:
            self._recover_expired()
            for folder in (self.inbox, self.claimed, self.consumed):
                if self._path(folder, wake_id).exists():
                    return {"ok": True, "wake_id": wake_id, "duplicate": True}
            atomic_json(self._path(self.inbox, wake_id), wake)
            return {"ok": True, "wake_id": wake_id, "duplicate": False}

    def claim(self, req: dict[str, Any]) -> dict[str, Any]:
        client_id = self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "")
        lane_id = str(req.get("lane_id") or "").strip()
        if lane_id and not re.fullmatch(r"lane-[0-9]{2,}", lane_id):
            raise ValueError("invalid lane_id")
        lease_seconds = max(30, min(300, int(req.get("lease_seconds") or 120)))
        now = now_ms()
        with self.lock:
            self._recover_expired()

            # Re-return this client's active claim first. This makes claim retries safe.
            for path in sorted(self.claimed.glob("*.json")):
                try:
                    record = read_json(path)
                    wake = self._validate_wake(record["wake"])
                    claim = record.get("claim") or {}
                    if claim.get("client_id") != client_id or int(claim.get("expires_at", 0)) <= now:
                        continue
                    if project_id and wake.get("project_id") != project_id:
                        continue
                    if lane_id and str(wake.get("lane_id") or "") != lane_id:
                        continue
                    return {
                        "ok": True,
                        "wake": {**wake, "git_branch": self.git_branch},
                        "message_id": wake["wake_id"],
                        "lease_expires_at": int(claim["expires_at"]),
                    }
                except Exception:
                    continue

            candidates: list[tuple[str, Path, dict[str, Any]]] = []
            for path in self.inbox.glob("*.json"):
                try:
                    wake = self._validate_wake(read_json(path))
                    if project_id and wake.get("project_id") != project_id:
                        continue
                    if lane_id and str(wake.get("lane_id") or "") != lane_id:
                        continue
                    candidates.append((str(wake.get("created_at") or ""), path, wake))
                except Exception:
                    continue
            candidates.sort(key=lambda item: (item[0], item[1].name))
            if not candidates:
                return {"ok": True, "wake": None}

            _, path, wake = candidates[0]
            expires = now + lease_seconds * 1000
            record = {"wake": {**wake, "git_branch": self.git_branch}, "claim": {"client_id": client_id, "expires_at": expires}}
            atomic_json(self._path(self.claimed, wake["wake_id"]), record)
            path.unlink(missing_ok=True)
            return {
                "ok": True,
                "wake": {**wake, "git_branch": self.git_branch},
                "message_id": wake["wake_id"],
                "lease_expires_at": expires,
            }

    def consume(self, req: dict[str, Any]) -> dict[str, Any]:
        client_id = self._safe_client(req.get("client_id"))
        wake_id = self._safe_id(req.get("message_id"), "message_id")
        with self.lock:
            consumed_path = self._path(self.consumed, wake_id)
            if consumed_path.exists():
                return {"ok": True, "message_id": wake_id, "duplicate": True}
            claim_path = self._path(self.claimed, wake_id)
            if not claim_path.exists():
                return {"ok": False, "error": "MESSAGE_NOT_FOUND"}
            record = read_json(claim_path)
            wake = self._validate_wake(record["wake"])
            claim = record.get("claim") or {}
            if claim.get("client_id") != client_id and int(claim.get("expires_at", 0)) > now_ms():
                return {"ok": False, "error": "CLAIM_OWNED_BY_OTHER"}
            atomic_json(consumed_path, {"wake": {**wake, "git_branch": self.git_branch}, "consumed_at": utc_now(), "client_id": client_id})
            claim_path.unlink(missing_ok=True)
            self._path(self.inbox, wake_id).unlink(missing_ok=True)
            return {"ok": True, "message_id": wake_id, "duplicate": False}

    def release(self, req: dict[str, Any]) -> dict[str, Any]:
        client_id = self._safe_client(req.get("client_id"))
        wake_id = self._safe_id(req.get("message_id"), "message_id")
        with self.lock:
            if self._path(self.consumed, wake_id).exists():
                return {"ok": True, "message_id": wake_id}
            claim_path = self._path(self.claimed, wake_id)
            if not claim_path.exists():
                return {"ok": True, "message_id": wake_id}
            record = read_json(claim_path)
            wake = self._validate_wake(record["wake"])
            claim = record.get("claim") or {}
            if claim.get("client_id") != client_id and int(claim.get("expires_at", 0)) > now_ms():
                return {"ok": False, "error": "CLAIM_OWNED_BY_OTHER"}
            atomic_json(self._path(self.inbox, wake_id), wake)
            claim_path.unlink(missing_ok=True)
            return {"ok": True, "message_id": wake_id}

    def event(self, req: dict[str, Any]) -> dict[str, Any]:
        """Persist small disposable extension/runtime telemetry locally, never to Git."""
        client_id = self._safe_client(req.get("client_id"))
        event = self._safe_event_name(req.get("event"))
        level = str(req.get("level") or "info").lower()
        if level not in {"info", "warn", "error"}:
            raise ValueError("invalid event level")
        project_id = str(req.get("project_id") or "").strip()
        if len(project_id) > 256:
            raise ValueError("project_id too long")
        data = req.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("event data must be an object")
        if _contains_sensitive_key(data):
            raise ValueError("sensitive keys are not allowed in local event data")
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_EVENT_DATA:
            raise ValueError("event data too large")

        record = {
            "v": 1,
            "at": utc_now(),
            "event": event,
            "level": level,
            "client_id": client_id,
            "project_id": project_id or None,
            "data": data,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        log_path = self.extension_events / "events.jsonl"
        with self.lock:
            if log_path.exists() and log_path.stat().st_size + len(line.encode("utf-8")) > MAX_EVENT_LOG:
                previous = self.extension_events / "events.previous.jsonl"
                previous.unlink(missing_ok=True)
                os.replace(log_path, previous)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line)
            atomic_json(self.extension_events / "latest.json", record)
            if level == "error":
                atomic_json(self.errors / "latest.json", record)
        return {"ok": True, "event": event, "at": record["at"]}

    def artifact_read(self, req: dict[str, Any]) -> dict[str, Any]:
        """Return one small, allowlisted canonical-Git image for Worker attachment.

        This is intentionally not a generic local-file reader. Only repository
        case-camera image evidence may cross the localhost bridge. The bytes are
        read from the freshly fetched canonical branch, not the possibly stale
        local working tree used to host the bridge process.
        """
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")

        rel = self._safe_repo_rel(req.get("artifact_ref"), "artifact_ref")
        parts = Path(rel).parts
        if len(parts) < 4 or parts[0] != "cases" or parts[2] != "camera":
            raise ValueError("artifact_ref must be under cases/<task>/camera/")
        suffix = Path(rel).suffix.lower()
        mime_type = ARTIFACT_MIME_TYPES.get(suffix)
        if not mime_type:
            raise ValueError("artifact_ref must be PNG, JPEG, or WebP")

        if not self.repo_root.exists():
            return {"ok": False, "error": "GIT_REPO_NOT_FOUND", "artifact_ref": rel}

        with self.git_lock:
            try:
                self._git("fetch", "--quiet", "--no-tags", self.git_remote, self.git_branch)
                shown = self._git_bytes("show", f"FETCH_HEAD:{rel}")
            except (OSError, subprocess.SubprocessError):
                return {"ok": False, "error": "ARTIFACT_NOT_FOUND", "artifact_ref": rel}

        data = shown.stdout
        size = len(data)
        if size < 1 or size > MAX_ARTIFACT_BYTES:
            return {
                "ok": False,
                "error": "ARTIFACT_SIZE_UNSUPPORTED",
                "artifact_ref": rel,
                "size_bytes": size,
                "max_bytes": MAX_ARTIFACT_BYTES,
            }

        return {
            "ok": True,
            "artifact_ref": rel,
            "file_name": Path(rel).name,
            "mime_type": mime_type,
            "size_bytes": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            "base64": base64.b64encode(data).decode("ascii"),
            "source": "canonical_git_fetch_head",
        }

    def worker_takeover_status(self, req: dict[str, Any]) -> dict[str, Any]:
        """Read the exact task-owned Worker pool authority from canonical Git."""
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        handoff_id = self._safe_id(req.get("handoff_id"), "handoff_id")
        if not handoff_id.startswith("pool-"):
            raise ValueError("handoff_id must start with pool-")
        lane_id = str(req.get("lane_id") or "").strip()
        worker_project_key = str(req.get("worker_project_key") or "").strip()
        owner_task_id = str(req.get("owner_task_id") or "").strip()
        allowed_owner = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
        if not 3 <= len(owner_task_id) <= 256 or any(ch not in allowed_owner for ch in owner_task_id):
            raise ValueError("invalid owner_task_id")
        owner_control_epoch = int(req.get("owner_control_epoch") or 0)
        if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
            raise ValueError("lane identity required")
        if owner_control_epoch < 1:
            raise ValueError("owner_control_epoch must be positive")
        if not self.repo_root.exists():
            return {"ok": False, "error": "GIT_REPO_NOT_FOUND"}

        topology_finalize = finalize_topology_request(self, req)

        with self.git_lock:
            try:
                self._git("fetch", "--quiet", "--no-tags", self.git_remote, self.git_branch)
                state = json.loads(self._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
                lane_state = json.loads(self._git("show", "FETCH_HEAD:state/lanes.json").stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return {"ok": False, "error": "GIT_TAKEOVER_STATE_FAILED"}

        if not isinstance(state, dict) or state.get("agent") != "chatgpt":
            return {"ok": False, "error": "GIT_TAKEOVER_STATE_INVALID"}

        lane_record: dict[str, Any] | None = None
        for item in lane_state.get("lanes") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("lane_id") or "") != lane_id:
                continue
            if str(item.get("project_key") or "") != worker_project_key:
                return {"ok": False, "error": "GIT_TAKEOVER_LANE_MISMATCH"}
            lane_record = item
            break
        if lane_record is None:
            return {"ok": False, "error": "GIT_TAKEOVER_LANE_MISSING"}

        pools = lane_record.get("task_pools")
        owner_key = _task_pool_key(owner_task_id, owner_control_epoch)
        pool = pools.get(owner_key) if isinstance(pools, dict) else None
        if not isinstance(pool, dict):
            actual = ""
            rollover: dict[str, Any] = {}
        else:
            if (
                str(pool.get("owner_task_id") or "") != owner_task_id
                or int(pool.get("owner_control_epoch") or 0) != owner_control_epoch
            ):
                return {"ok": False, "error": "GIT_TAKEOVER_OWNER_MISMATCH"}
            actual = str(pool.get("last_pool_takeover_id") or "")
            rollover = pool.get("worker_rollover_request") if isinstance(pool.get("worker_rollover_request"), dict) else {}

        request_handoff = str(rollover.get("handoff_id") or rollover.get("outgoing_pool_id") or "")
        request_reason = str(rollover.get("reason") or "")
        handoff_packet_ref = str(rollover.get("handoff_packet_ref") or "")
        rollover_requested = request_handoff == handoff_id and request_reason in {"context_compacted", "semantic_stall"}
        foreground_task = self._foreground_task_view(state.get("foreground_task"))
        snapshot_at = utc_now()
        with self.lock:
            self.foreground_task_snapshot = foreground_task
            self.foreground_task_snapshot_at = snapshot_at
        return {
            "ok": True,
            "matched": actual == handoff_id,
            "handoff_id": handoff_id,
            "last_pool_takeover_id": actual or None,
            "state_updated": str(state.get("updated") or "") or None,
            "rollover_requested": rollover_requested,
            "rollover_request_handoff_id": request_handoff or None,
            "rollover_reason": request_reason or None,
            "handoff_packet_ref": handoff_packet_ref or None,
            "active_task": str(state.get("active_task") or "") or None,
            "lane_id": lane_id,
            "worker_project_key": worker_project_key,
            "owner_task_id": owner_task_id,
            "owner_control_epoch": owner_control_epoch,
            "lane_scoped": True,
            "task_pool_scoped": True,
            "topology_finalize": topology_finalize,
        }


    def dispatch_accept(self, req: dict[str, Any]) -> dict[str, Any]:
        """Mechanically mark an exact dispatch RUNNING once the browser proves
        that the target ChatGPT assistant response has started.

        The semantic Worker no longer burns its first tool call mutating its own
        scheduler TCB. The runtime owns dispatch admission; the Worker owns work.
        """
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        backend_cl = self._safe_repo_rel(req.get("backend_cl"), "backend_cl")
        dispatch_id = self._safe_id(req.get("dispatch_id"), "dispatch_id")
        generation = int(req.get("dispatch_generation") or 0)
        if generation < 1:
            raise ValueError("dispatch_generation must be positive")
        fence_token = str(req.get("fence_token") or "")
        if not 8 <= len(fence_token) <= 256:
            raise ValueError("invalid fence_token")
        task_id = self._safe_id(req.get("task_id"), "task_id")
        worker_ref = str(req.get("worker_ref") or "").strip()
        lane_id = str(req.get("lane_id") or "").strip()
        worker_project_key = str(req.get("worker_project_key") or "").strip()
        if not lane_id.startswith("lane-") or not worker_project_key.startswith("g-p-"):
            return {"ok": False, "error": "DISPATCH_LANE_MISMATCH"}
        if not worker_ref:
            return {"ok": False, "error": "DISPATCH_WORKER_MISMATCH"}

        for attempt in range(3):
            with self.git_lock:
                try:
                    self._git("fetch", "--quiet", "--no-tags", self.git_remote, self.git_branch)
                    shown = self._git("show", f"FETCH_HEAD:{backend_cl}")
                    cl = json.loads(shown.stdout)
                    task = json.loads(self._git("show", f"FETCH_HEAD:tasks/{task_id}.json").stdout)
                    canonical_state = json.loads(self._git("show", "FETCH_HEAD:state/chatgpt.json").stdout)
                    canonical_lanes = json.loads(self._git("show", "FETCH_HEAD:state/lanes.json").stdout)
                except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                    return {"ok": False, "error": "GIT_DISPATCH_ACCEPT_READ_FAILED"}

                if not isinstance(cl, dict) or cl.get("scope") != "backend_execution":
                    return {"ok": False, "error": "GIT_DISPATCH_CL_INVALID"}
                if str(cl.get("task_id") or "") != task_id:
                    return {"ok": False, "error": "DISPATCH_TASK_MISMATCH"}
                if not isinstance(task, dict) or str(task.get("task_id") or "") != task_id:
                    return {"ok": False, "error": "DISPATCH_TASK_MISMATCH"}
                declared_backend, declared_lane, declared_project = _task_execution_binding(task)
                if declared_backend and declared_backend != backend_cl:
                    return {"ok": False, "error": "DISPATCH_TASK_MISMATCH"}
                if declared_lane and declared_lane != lane_id:
                    return {"ok": False, "error": "DISPATCH_LANE_MISMATCH"}
                if declared_project and declared_project != worker_project_key:
                    return {"ok": False, "error": "DISPATCH_LANE_MISMATCH"}

                owner_task_id, owner_control_epoch = _task_worker_owner(task, cl)
                canonical_owner, owner_conflict, owner_source = canonical_worker_owner(
                    canonical_lanes,
                    lane_id=lane_id,
                    worker_project_key=worker_project_key,
                    owner_task_id=owner_task_id,
                    owner_control_epoch=owner_control_epoch,
                )
                if owner_source in {"lane_missing", "lane_project_mismatch"}:
                    return {
                        "ok": False,
                        "error": "DISPATCH_LANE_MISMATCH",
                        "owner_source": owner_source,
                    }
                if owner_source == "task_pool_identity_mismatch":
                    return {
                        "ok": False,
                        "error": "DISPATCH_WORKER_MISMATCH",
                        "owner_source": owner_source,
                    }
                pending_dispatch = cl.get("dispatch") or {}
                recovered_from = (
                    pending_dispatch.get("recovered_from")
                    if isinstance(pending_dispatch.get("recovered_from"), dict)
                    else {}
                )
                legacy_recovery_predecessor = (
                    recovered_from.get("acked_by_worker_ref")
                    if int(recovered_from.get("generation") or 0) == generation - 1
                    else None
                )
                predecessor_worker_ref = (
                    pending_dispatch.get("predecessor_worker_ref")
                    or legacy_recovery_predecessor
                )
                replacing_predecessor = (
                    task.get("kind") == "planner_worker_child"
                    and generation > 1
                    and pending_dispatch.get("state") in {"READY", "DISPATCHED", "ACKED"}
                    and not pending_dispatch.get("acked_by_worker_ref")
                    and bool(canonical_owner)
                    and predecessor_worker_ref == canonical_owner
                )
                if canonical_owner and canonical_owner != worker_ref and not replacing_predecessor:
                    return {
                        "ok": False,
                        "error": "DISPATCH_WORKER_MISMATCH",
                        "canonical_owner": canonical_owner,
                        "owner_source": owner_source,
                        "owner_mirror_conflict": owner_conflict,
                    }

                dispatch = cl.get("dispatch")
                if not isinstance(dispatch, dict):
                    return {"ok": False, "error": "DISPATCH_MISSING"}

                exact = (
                    str(dispatch.get("dispatch_id") or "") == dispatch_id
                    and int(dispatch.get("generation") or 0) == generation
                    and str(dispatch.get("fence_token") or "") == fence_token
                )
                if not exact:
                    return {"ok": False, "error": "DISPATCH_STALE"}

                current_state = str(dispatch.get("state") or "")
                if current_state in {
                    "RUNNING", "WAIT_RESULT", "WAIT_RESOURCE", "WAIT_DEP",
                    "HANDOFF", "DONE", "ERROR", "BLOCKED", "CANCELLED",
                }:
                    return {
                        "ok": True,
                        "accepted": False,
                        "idle": "already_accepted",
                        "state": current_state,
                    }
                if current_state not in {"READY", "DISPATCHED", "ACKED"}:
                    return {"ok": False, "error": f"DISPATCH_STATE_{current_state or 'UNKNOWN'}"}

                current = json.loads(json.dumps(cl))
                d = current.get("dispatch")
                if not isinstance(d, dict):
                    return {"ok": False, "error": "DISPATCH_CHANGED_DURING_ACCEPT"}

                now = utc_now()
                d["state"] = "RUNNING"
                d["delivered_at"] = d.get("delivered_at") or now
                d["acked_at"] = d.get("acked_at") or now
                d["acked_by_worker_ref"] = worker_ref
                d["ack_source"] = "extension_response_start"
                d["lease_expires_at"] = semantic_lease_expires_at()
                current["dispatch"] = d
                current["overall"] = "RUNNING"
                current["updated_at"] = now
                for item in current.get("conditions") or []:
                    if isinstance(item, dict) and item.get("id") == "claimed":
                        item["state"] = "GREEN"
                        item["detail"] = "runtime observed assistant response start for exact dispatch"
                        item["evidence_ref"] = backend_cl

                # Response-start admission also binds the task-owned Worker pool.
                # Harness writes machine ownership in the same admission commit.
                next_lanes = json.loads(json.dumps(canonical_lanes))
                owner_key = _task_pool_key(owner_task_id, owner_control_epoch)
                lane_bound = False
                for lane in next_lanes.get("lanes") or []:
                    if not isinstance(lane, dict):
                        continue
                    if str(lane.get("lane_id") or "") != lane_id:
                        continue
                    if str(lane.get("project_key") or "") != worker_project_key:
                        return {"ok": False, "error": "DISPATCH_LANE_MISMATCH"}
                    pools = lane.setdefault("task_pools", {})
                    pool = pools.get(owner_key)
                    if pool is None:
                        pool = {
                            "owner_task_id": owner_task_id,
                            "owner_control_epoch": owner_control_epoch,
                            "last_pool_takeover_id": worker_ref,
                            "worker_rollover_request": None,
                        }
                    elif not isinstance(pool, dict):
                        return {"ok": False, "error": "DISPATCH_WORKER_MISMATCH"}
                    else:
                        if (
                            str(pool.get("owner_task_id") or "") != owner_task_id
                            or int(pool.get("owner_control_epoch") or 0) != owner_control_epoch
                        ):
                            return {"ok": False, "error": "DISPATCH_WORKER_MISMATCH"}
                        pool["last_pool_takeover_id"] = worker_ref
                        if isinstance(pool.get("worker_rollover_request"), dict):
                            pool["worker_rollover_request"] = None
                    pool["owner_task_id"] = owner_task_id
                    pool["owner_control_epoch"] = owner_control_epoch
                    pool["updated_at"] = now
                    pools[owner_key] = pool
                    lane_bound = True
                    break
                if not lane_bound:
                    return {"ok": False, "error": "DISPATCH_LANE_MISMATCH"}
                next_lanes["updated_at"] = now

                # Planner-routed children must atomically turn exact Worker
                # response-start admission into a durable parent Planner event.
                updates: dict[str, dict[str, Any]] = {
                    backend_cl: current,
                    "state/lanes.json": next_lanes,
                }
                planner_event_id = None
                parent_task_id = str(task.get("parent_task_id") or "").strip()
                parent_decision_ref = str(task.get("parent_planner_decision_ref") or "").strip()
                if parent_task_id or parent_decision_ref:
                    if not parent_task_id or not parent_decision_ref:
                        return {"ok": False, "error": "PLANNER_PARENT_IDENTITY_INCOMPLETE"}
                    parent_cell_rel = f"state/task_cells/{parent_task_id}.json"
                    try:
                        parent_cell = json.loads(self._git("show", f"FETCH_HEAD:{parent_cell_rel}").stdout)
                    except (subprocess.SubprocessError, json.JSONDecodeError):
                        return {"ok": False, "error": "PLANNER_PARENT_CELL_INVALID"}
                    parent_control = parent_cell.get("planner_control")
                    if not isinstance(parent_control, dict) or parent_control.get("enabled") is not True:
                        return {"ok": False, "error": "PLANNER_PARENT_CONTROL_MISSING"}
                    parent_epoch = int(parent_cell.get("control_epoch") or 0)
                    if parent_epoch < 1:
                        return {"ok": False, "error": "PLANNER_PARENT_EPOCH_INVALID"}
                    admission_blob_sha = hashlib.sha256(
                        json.dumps(
                            current,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    try:
                        event = make_planner_event(
                            task_id=parent_task_id,
                            control_epoch=parent_epoch,
                            kind="admission_changed",
                            source_role="harness",
                            source_identity={
                                "admission_ref": backend_cl,
                                "admission_blob_sha": admission_blob_sha,
                                "new_state": "RUNNING",
                                "child_task_id": task_id,
                                "dispatch_id": dispatch_id,
                                "dispatch_generation": generation,
                                "fence_token": fence_token,
                                "worker_ref": worker_ref,
                                "parent_planner_decision_ref": parent_decision_ref,
                            },
                            refs=[backend_cl, f"tasks/{task_id}.json", parent_decision_ref],
                        )
                        parent_control, _ = insert_planner_event(parent_control, event)
                        # Admission is mechanical when Planner already waits for a result.
                        if (parent_control.get("wait") or {}).get("kind") == "WAIT_RESULT":
                            admitted = parent_control["inbox"]["events"][event["event_id"]]
                            admitted.update(state="CONSUMED", consumed_at=now, consumption_reason="runtime_admission")
                    except PlannerControlError as exc:
                        return {"ok": False, "error": exc.code}
                    parent_cell["planner_control"] = parent_control
                    parent_cell["updated_at"] = now
                    updates[parent_cell_rel] = parent_cell
                    planner_event_id = event["event_id"]

                # Admission mutates one or two JSON blobs. Build the new commit with
                # a temporary index instead of materializing the full repository.
                index_path = self.runtime / f"dispatch-accept-index-{dispatch_id}-{attempt}"
                temp_blobs: list[Path] = []
                index_env = {"GIT_INDEX_FILE": str(index_path)}
                identity_env = {
                    "GIT_AUTHOR_NAME": "gah-local-bridge",
                    "GIT_AUTHOR_EMAIL": "gah-local-bridge@example.invalid",
                    "GIT_COMMITTER_NAME": "gah-local-bridge",
                    "GIT_COMMITTER_EMAIL": "gah-local-bridge@example.invalid",
                }
                try:
                    index_path.unlink(missing_ok=True)
                    index_path.with_suffix(index_path.suffix + ".lock").unlink(missing_ok=True)
                    run_git(self.repo_root, "read-tree", "FETCH_HEAD", timeout=20, env_overrides=index_env)
                    for rel, value in updates.items():
                        suffix = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12]
                        blob_path = self.runtime / f"dispatch-accept-blob-{dispatch_id}-{attempt}-{suffix}.json"
                        blob_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        temp_blobs.append(blob_path)
                        blob_sha = run_git(
                            self.repo_root,
                            "hash-object", "-w", f"--path={rel}", str(blob_path),
                            timeout=20,
                        ).stdout.strip()
                        run_git(
                            self.repo_root,
                            "update-index", "--add", "--cacheinfo", "100644", blob_sha, rel,
                            timeout=20,
                            env_overrides=index_env,
                        )
                    tree_sha = run_git(
                        self.repo_root, "write-tree", timeout=20, env_overrides=index_env
                    ).stdout.strip()
                    commit_sha = run_git(
                        self.repo_root,
                        "commit-tree", tree_sha, "-p", "FETCH_HEAD",
                        "-m", f"Scheduler runtime accept {dispatch_id} [skip ci]",
                        timeout=20,
                        env_overrides=identity_env,
                    ).stdout.strip()
                    try:
                        run_git(
                            self.repo_root,
                            "push", self.git_remote, f"{commit_sha}:{self.git_branch}",
                            timeout=30,
                        )
                    except subprocess.SubprocessError:
                        # Push timeout/failure has an unknown remote outcome. The
                        # next loop iteration re-fetches canonical state before
                        # any retry and recognizes an already-admitted dispatch.
                        continue
                    return {
                        "ok": True,
                        "accepted": True,
                        "dispatch_id": dispatch_id,
                        "generation": generation,
                        "state": "RUNNING",
                        "worker_ref": worker_ref,
                        "owner_source": owner_source,
                        "owner_mirror_conflict": owner_conflict,
                        "planner_admission_event_id": planner_event_id,
                    }
                finally:
                    index_path.unlink(missing_ok=True)
                    index_path.with_suffix(index_path.suffix + ".lock").unlink(missing_ok=True)
                    for path in temp_blobs:
                        path.unlink(missing_ok=True)

        return {"ok": False, "error": "DISPATCH_ACCEPT_RETRY_EXHAUSTED"}

    def dispatch_status(self, req: dict[str, Any]) -> dict[str, Any]:
        """Return canonical scheduler state for one exact backend dispatch.

        This is a read-only suppression gate used by the browser host before it injects
        a wake into a Worker chat. Already-accepted or stale dispatches are consumed
        without interrupting the Worker.
        """
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        backend_cl = self._safe_repo_rel(req.get("backend_cl"), "backend_cl")
        dispatch_id = self._safe_id(req.get("dispatch_id"), "dispatch_id")
        generation = int(req.get("dispatch_generation") or 0)
        if generation < 1:
            raise ValueError("dispatch_generation must be positive")
        fence_token = str(req.get("fence_token") or "")
        if not 8 <= len(fence_token) <= 256:
            raise ValueError("invalid fence_token")
        task_id = str(req.get("task_id") or "").strip()

        with self.git_lock:
            try:
                self._git("fetch", "--quiet", "--no-tags", self.git_remote, self.git_branch)
                shown = self._git("show", f"FETCH_HEAD:{backend_cl}")
                cl = json.loads(shown.stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return {"ok": False, "error": "GIT_DISPATCH_STATE_FAILED"}

        if not isinstance(cl, dict) or cl.get("scope") != "backend_execution":
            return {"ok": False, "error": "GIT_DISPATCH_CL_INVALID"}
        if task_id and str(cl.get("task_id") or "") != task_id:
            return {
                "ok": True,
                "matched": False,
                "suppress": True,
                "reason": "task_mismatch",
                "state": None,
            }

        dispatch = cl.get("dispatch")
        if not isinstance(dispatch, dict):
            return {
                "ok": True,
                "matched": False,
                "suppress": False,
                "reason": "legacy_no_dispatch",
                "state": None,
            }

        current_id = str(dispatch.get("dispatch_id") or "")
        current_generation = int(dispatch.get("generation") or 0)
        current_fence = str(dispatch.get("fence_token") or "")
        state = str(dispatch.get("state") or "")
        matched = (
            current_id == dispatch_id
            and current_generation == generation
            and current_fence == fence_token
        )
        if not matched:
            return {
                "ok": True,
                "matched": False,
                "suppress": True,
                "reason": "stale_dispatch",
                "state": state or None,
                "current_dispatch_id": current_id or None,
                "current_generation": current_generation or None,
            }

        suppress_states = {
            "ACKED", "RUNNING", "WAIT_RESULT", "WAIT_RESOURCE", "WAIT_DEP",
            "HANDOFF", "DONE", "ERROR", "BLOCKED", "CANCELLED",
        }
        return {
            "ok": True,
            "matched": True,
            "suppress": state in suppress_states,
            "reason": "already_accepted" if state in suppress_states else "dispatch_pending",
            "state": state or None,
            "dispatch_id": dispatch_id,
            "generation": generation,
            "acked_at": dispatch.get("acked_at"),
            "acked_by_worker_ref": dispatch.get("acked_by_worker_ref"),
            "wait_ref": dispatch.get("wait_ref"),
            "handoff_packet_ref": cl.get("handoff_packet_ref"),
        }

    def foreground_task_status(self, req: dict[str, Any]) -> dict[str, Any]:
        """Return the most recent canonical foreground-task projection.

        This is intentionally cache-only. The Worker maintenance loop already fetches
        canonical Git state every minute, so the foreground monitor does not perform
        a second Git fetch or create a competing source of truth.
        """
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        with self.lock:
            task = dict(self.foreground_task_snapshot) if self.foreground_task_snapshot else None
            state_at = self.foreground_task_snapshot_at
        return {"ok": True, "foreground_task": task, "state_at": state_at}

    def health(self) -> dict[str, Any]:
        with self.lock:
            self._recover_expired()
            return {
                "ok": True,
                "service": "gah-local-wake-bridge",
                "v": 1,
                "root": str(self.root),
                "pending": len(list(self.inbox.glob("*.json"))),
                "claimed": len(list(self.claimed.glob("*.json"))),
                "consumed": len(list(self.consumed.glob("*.json"))),
                "event_spool": str(self.extension_events),
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "GAHLocalWake/1"

    def _json(self, code: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return not origin

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._json(403, {"ok": False, "error": "CORS_DISABLED"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in {"", "/health"}:
            self._json(404, {"ok": False, "error": "NOT_FOUND"})
            return
        self._json(200, self.server.store.health())  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in {"", "/api"}:
            self._json(404, {"ok": False, "error": "NOT_FOUND"})
            return
        if self.headers.get("X-GAH-Bridge") != "1" or not self._origin_allowed():
            self._json(403, {"ok": False, "error": "LOCAL_AUTH_REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("invalid request size")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            op = str(req.get("op") or "")
            store: WakeStore = self.server.store  # type: ignore[attr-defined]
            if op == "emit":
                result = store.emit(req.get("wake") or {})
            elif op == "claim":
                result = store.claim(req)
            elif op == "consume":
                result = store.consume(req)
            elif op == "release":
                result = store.release(req)
            elif op == "event":
                result = store.event(req)
            elif op == "worker_takeover_status":
                result = store.worker_takeover_status(req)
            elif op == "topology_request":
                result = stage_topology_request(store, req)
            elif op == "topology_status":
                result = topology_status(store, req)
            elif op == "control_status":
                result = control_status(store, req)
            elif op == "lane_clear_begin":
                result = begin_lane_clear(store, req)
            elif op == "lane_clear_complete":
                result = complete_lane_clear(store, req)
            elif op == "task_cell_prompt_complete":
                result = complete_task_cell_prompt(store, req)
            elif op == "task_cell_role_prompt_complete":
                result = complete_task_cell_role_prompt(store, req)
            elif op == "lane_pool_reset_complete":
                result = complete_lane_pool_reset(store, req)
            elif op == "task_cell_clear_complete":
                result = complete_task_cell_clear(store, req)
            elif op == "task_cell_project_clear_complete":
                result = complete_task_cell_project_clear(store, req)
            elif op == "planner_foreground_handoff_begin":
                result = begin_foreground_planner_handoff(store, req)
            elif op == "planner_foreground_handoff_attempt":
                result = record_foreground_planner_create_attempt(store, req)
            elif op == "planner_foreground_handoff_bind":
                result = complete_foreground_planner_handoff_binding(store, req)
            elif op == "planner_event_enqueue":
                result = enqueue_planner_event(store, req)
            elif op == "planner_runtime_migrate":
                result = migrate_planner_runtime(store, req)
            elif op == "semantic_turn_observe":
                result = observe_semantic_turn(store, req)
            elif op == "semantic_turn_sync":
                result = sync_semantic_turn(store, req)
            elif op == "planner_runtime_tick":
                result = planner_runtime_tick(store, req)
            elif op == "planner_final_delivery_status":
                result = planner_final_delivery_status(store, req)
            elif op == "planner_final_delivery_delivered":
                result = planner_final_delivery_update(store, req, operation="delivered")
            elif op == "planner_final_delivery_cleaned":
                result = planner_final_delivery_update(store, req, operation="cleaned")
            elif op == "planner_final_delivery_consumed":
                result = planner_final_delivery_update(store, req, operation="consumed")
            elif op == "planner_cleanup_execute":
                result = execute_planner_cleanup(store, req)
            elif op == "planner_runtime_request_rotation":
                result = request_planner_rotation(store, req)
            elif op == "planner_successor_bootstrap_complete":
                result = complete_planner_successor_bootstrap(store, req)
            elif op == "planner_successor_promote_complete":
                result = complete_planner_successor_promote(store, req)
            elif op == "planner_predecessor_retire_complete":
                result = complete_planner_predecessor_retire(store, req)
            elif op == "planner_git_cli":
                result = planner_git_cli_fallback(store, req)
            elif op == "action_submit":
                result = stage_action_submit(store, req)
            elif op == "worker_handoff_complete":
                result = complete_worker_handoff(store, req)
            elif op == "dispatch_liveness":
                result = reconcile_dispatch_liveness(store, req)
            elif op == "worker_watchdog_expired":
                result = stage_worker_watchdog_helper(store, req)
            elif op == "artifact_read":
                result = store.artifact_read(req)
            elif op == "dispatch_accept":
                result = store.dispatch_accept(req)
            elif op == "dispatch_status":
                result = store.dispatch_status(req)
            elif op == "foreground_task_status":
                result = store.foreground_task_status(req)
            elif op == "foreground_terminal_status":
                result = reconcile_foreground_terminal(store, {**req, "terminal_op": "status"})
            elif op == "foreground_terminal_delivered":
                result = reconcile_foreground_terminal(store, {**req, "terminal_op": "delivered"})
            elif op == "foreground_terminal_consumed":
                result = reconcile_foreground_terminal(store, {**req, "terminal_op": "consumed"})
            elif op == "health":
                result = store.health()
            else:
                result = {"ok": False, "error": "UNKNOWN_OP"}
            self._json(200, result)
        except Exception as exc:
            self._json(400, {"ok": False, "error": "EXCEPTION", "detail": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            store: WakeStore = self.server.store  # type: ignore[attr-defined]
            line = f"{utc_now()} {self.client_address[0]} {fmt % args}\n"
            with (store.logs / "bridge.log").open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=str(ROOT_DEFAULT))
    p.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    p.add_argument("--git-remote", default=GIT_REMOTE_DEFAULT)
    p.add_argument("--git-branch", default=GIT_BRANCH_DEFAULT)
    p.add_argument("--host", default=HOST_DEFAULT)
    p.add_argument("--port", type=int, default=PORT_DEFAULT)
    args = p.parse_args()

    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing non-loopback bind; use 127.0.0.1 or localhost")
    import subprocess, sys
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'installation/preflight.py')], check=True)
    store = WakeStore(Path(args.root), Path(args.repo_root), args.git_remote, args.git_branch)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = store  # type: ignore[attr-defined]
    atomic_json(store.runtime / "bridge.json", {
        "pid": os.getpid(),
        "host": args.host,
        "port": args.port,
        "root": str(store.root),
        "started_at": utc_now(),
    })
    print(json.dumps({"ok": True, "service": "gah-local-wake-bridge", "url": f"http://{args.host}:{args.port}", "root": str(store.root)}, ensure_ascii=False))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            (store.runtime / "bridge.json").unlink(missing_ok=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
