#!/usr/bin/env python3
"""Loopback-only file-backed wake bridge for the local Chrome extension.

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
    from .control import begin_lane_clear, complete_lane_clear, complete_lane_pool_reset, complete_task_cell_clear, complete_task_cell_prompt, control_status
    from .scheduler import canonical_worker_owner, complete_worker_handoff, reconcile_dispatch_liveness, semantic_lease_expires_at, stage_action_submit
    from .topology import finalize_topology_request, stage_topology_request, topology_status
except ImportError:
    from control import begin_lane_clear, complete_lane_clear, complete_task_cell_prompt, control_status
    from scheduler import canonical_worker_owner, complete_worker_handoff, reconcile_dispatch_liveness, semantic_lease_expires_at, stage_action_submit
    from topology import finalize_topology_request, stage_topology_request, topology_status

ROOT_DEFAULT = Path(os.environ.get("GAH_LOCAL_ROOT", str(Path.home() / ".cah" / "runtime")))
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
                        "wake": wake,
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
            record = {"wake": wake, "claim": {"client_id": client_id, "expires_at": expires}}
            atomic_json(self._path(self.claimed, wake["wake_id"]), record)
            path.unlink(missing_ok=True)
            return {
                "ok": True,
                "wake": wake,
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
            atomic_json(consumed_path, {"wake": wake, "consumed_at": utc_now(), "client_id": client_id})
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
        """Fetch canonical Git state and compare one Worker handoff id exactly.

        The same read also exposes a narrowly-scoped post-compaction rollover request
        for that exact current handoff. Git access is serialized because multiple
        extension listeners may otherwise race `git fetch` against the same repo.
        """
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        handoff_id = self._safe_id(req.get("handoff_id"), "handoff_id")
        if not handoff_id.startswith("pool-"):
            raise ValueError("handoff_id must start with pool-")
        if not self.repo_root.exists():
            return {"ok": False, "error": "GIT_REPO_NOT_FOUND"}

        topology_finalize = finalize_topology_request(self, req)

        with self.git_lock:
            try:
                self._git("fetch", "--quiet", "--no-tags", self.git_remote, self.git_branch)
            except (OSError, subprocess.SubprocessError):
                return {"ok": False, "error": "GIT_TAKEOVER_FETCH_FAILED"}

            try:
                shown = self._git("show", "FETCH_HEAD:state/chatgpt.json")
                state = json.loads(shown.stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return {"ok": False, "error": "GIT_TAKEOVER_STATE_FAILED"}
            try:
                lane_shown = self._git("show", "FETCH_HEAD:state/lanes.json")
                lane_state = json.loads(lane_shown.stdout)
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                lane_state = None

        if not isinstance(state, dict) or state.get("agent") != "chatgpt":
            return {"ok": False, "error": "GIT_TAKEOVER_STATE_INVALID"}

        lane_id = str(req.get("lane_id") or "")
        worker_project_key = str(req.get("worker_project_key") or "")
        lane_record: dict[str, Any] | None = None
        if lane_id and isinstance(lane_state, dict):
            for item in lane_state.get("lanes") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("lane_id") or "") != lane_id:
                    continue
                if worker_project_key and str(item.get("project_key") or "") != worker_project_key:
                    continue
                lane_record = item
                break

        top_actual = str(state.get("last_pool_takeover_id") or "")
        top_rollover = state.get("worker_rollover_request")
        if lane_record is not None:
            actual = str(lane_record.get("last_pool_takeover_id") or "")
            rollover = lane_record.get("worker_rollover_request")
            # lane-00 migration compatibility is exact-identity based. A newer
            # top-level takeover may legitimately outrun the lane mirror; accept
            # it only when it matches the handoff being queried. Likewise prefer
            # an exact top-level rollover request over a stale lane-local request.
            if lane_id == "lane-00":
                if top_actual == handoff_id or not actual:
                    actual = top_actual or actual
                lane_req = ""
                if isinstance(rollover, dict):
                    lane_req = str(rollover.get("handoff_id") or rollover.get("outgoing_pool_id") or "")
                top_req = ""
                if isinstance(top_rollover, dict):
                    top_req = str(top_rollover.get("handoff_id") or top_rollover.get("outgoing_pool_id") or "")
                if top_req == handoff_id:
                    rollover = top_rollover
                elif not isinstance(rollover, dict) or not lane_req:
                    rollover = top_rollover
        else:
            actual = top_actual
            rollover = top_rollover
        if not isinstance(rollover, dict):
            rollover = {}
        request_handoff = str(rollover.get("handoff_id") or rollover.get("outgoing_pool_id") or "")
        request_reason = str(rollover.get("reason") or "")
        handoff_packet_ref = str(rollover.get("handoff_packet_ref") or state.get("handoff_packet_ref") or "")
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
            "lane_id": lane_id or None,
            "worker_project_key": worker_project_key or None,
            "lane_scoped": lane_record is not None,
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

                canonical_owner, owner_conflict, owner_source = canonical_worker_owner(
                    canonical_lanes,
                    canonical_state,
                    lane_id=lane_id,
                    worker_project_key=worker_project_key,
                )
                if owner_source in {"lane_missing", "lane_project_mismatch"}:
                    return {
                        "ok": False,
                        "error": "DISPATCH_LANE_MISMATCH",
                        "owner_source": owner_source,
                    }
                if not canonical_owner or canonical_owner != worker_ref:
                    return {
                        "ok": False,
                        "error": "DISPATCH_WORKER_MISMATCH",
                        "canonical_owner": canonical_owner or None,
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

                worktree = self.runtime / f"dispatch-accept-{dispatch_id}-{attempt}"
                if worktree.exists():
                    import shutil
                    shutil.rmtree(worktree, ignore_errors=True)
                try:
                    self._git("worktree", "add", "--force", "--detach", str(worktree), "FETCH_HEAD")
                    run_git(worktree, "config", "user.name", "gah-local-bridge", timeout=20)
                    run_git(worktree, "config", "user.email", "gah-local-bridge@example.invalid", timeout=20)
                    cl_path = worktree / backend_cl
                    current = json.loads(cl_path.read_text(encoding="utf-8"))
                    d = current.get("dispatch")
                    if not isinstance(d, dict):
                        return {"ok": False, "error": "DISPATCH_CHANGED_DURING_ACCEPT"}
                    if not (
                        str(d.get("dispatch_id") or "") == dispatch_id
                        and int(d.get("generation") or 0) == generation
                        and str(d.get("fence_token") or "") == fence_token
                    ):
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
                    cl_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                    run_git(worktree, "add", "--", backend_cl, timeout=20)
                    run_git(worktree, "commit", "-m", f"Scheduler runtime accept {dispatch_id} [skip ci]", timeout=20)
                    try:
                        run_git(worktree, "push", self.git_remote, f"HEAD:{self.git_branch}", timeout=30)
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
                    }
                finally:
                    try:
                        self._git("worktree", "remove", "--force", str(worktree))
                    except Exception:
                        import shutil
                        shutil.rmtree(worktree, ignore_errors=True)

        return {"ok": False, "error": "DISPATCH_ACCEPT_RETRY_EXHAUSTED"}

    def dispatch_status(self, req: dict[str, Any]) -> dict[str, Any]:
        """Return canonical scheduler state for one exact backend dispatch.

        This is a read-only suppression gate used by the extension before it injects
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

    def extension_reload_request(self, req: dict[str, Any]) -> dict[str, Any]:
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        request_id = self._safe_id(req.get("request_id"), "request_id")
        expected_version = str(req.get("expected_version") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        if not expected_version or len(expected_version) > 64:
            raise ValueError("expected_version required")

        with self.lock:
            status_path = self.runtime / "extension-status.json"
            current_status = read_json(status_path) if status_path.exists() else None
            current_version = str(current_status.get("version") or "") if isinstance(current_status, dict) else ""
            current_extension_id = str(current_status.get("extension_id") or "") if isinstance(current_status, dict) else ""
            already_live = current_version == expected_version

            record = {
                "v": 1,
                "request_id": request_id,
                "project_id": project_id,
                "expected_version": expected_version,
                "status": "DONE" if already_live else "PENDING",
                "requested_at": utc_now(),
                "completed_at": utc_now() if already_live else None,
                "observed_version": current_version if already_live else None,
                "extension_id": current_extension_id or None if already_live else None,
                "managed_tabs_ready": None,
            }
            atomic_json(self.runtime / "extension-control.json", record)
        return {"ok": True, "control": record, "already_live": already_live}

    def extension_runtime_hello(self, req: dict[str, Any]) -> dict[str, Any]:
        client_id = self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        version = str(req.get("version") or "").strip()
        extension_id = str(req.get("extension_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        if not version or len(version) > 64:
            raise ValueError("version required")
        if len(extension_id) > 128:
            raise ValueError("extension_id too long")

        desired_version = int(req.get("desired_version") or 0)
        desired_lane_count = int(req.get("desired_lane_count") or 0)
        desired_enabled_count = int(req.get("desired_enabled_count") or 0)
        if desired_version < 0 or not 0 <= desired_lane_count <= 16 or not 0 <= desired_enabled_count <= 16:
            raise ValueError("invalid extension topology summary")
        if desired_enabled_count > desired_lane_count:
            raise ValueError("enabled lane count exceeds desired lane count")

        bootstrap_parallel_status = str(req.get("bootstrap_parallel_status") or "").strip() or None
        if bootstrap_parallel_status not in {None, "PENDING", "APPLYING", "WORKERS", "DONE", "ERROR"}:
            raise ValueError("invalid bootstrap_parallel_status")

        lane_runtime_raw = req.get("lane_runtime")
        lane_runtime: list[dict[str, Any]] = []
        if lane_runtime_raw is not None:
            if not isinstance(lane_runtime_raw, list) or len(lane_runtime_raw) > 16:
                raise ValueError("lane_runtime must be an array with at most 16 entries")
            seen_lanes: set[str] = set()
            for item in lane_runtime_raw:
                if not isinstance(item, dict):
                    raise ValueError("lane_runtime item must be an object")
                lane_id = str(item.get("lane_id") or "")
                project_key = str(item.get("project_key") or "")
                if not re.fullmatch(r"lane-[0-9]{2,}", lane_id):
                    raise ValueError("invalid lane_runtime lane_id")
                if not re.fullmatch(r"g-p-[A-Za-z0-9]+", project_key):
                    raise ValueError("invalid lane_runtime project_key")
                if lane_id in seen_lanes:
                    raise ValueError("duplicate lane_runtime lane_id")
                seen_lanes.add(lane_id)
                managed_count = int(item.get("managed_count") or 0)
                if managed_count < 0 or managed_count > 64:
                    raise ValueError("invalid lane_runtime managed_count")
                handoff_status = str(item.get("handoff_status") or "").strip() or None
                if handoff_status and len(handoff_status) > 64:
                    raise ValueError("lane_runtime handoff_status too long")
                lane_runtime.append({
                    "lane_id": lane_id,
                    "project_key": project_key,
                    "enabled": bool(item.get("enabled")),
                    "current_verified": bool(item.get("current_verified")),
                    "managed_count": managed_count,
                    "handoff_status": handoff_status,
                })

        now = utc_now()
        with self.lock:
            control_path = self.runtime / "extension-control.json"
            control = read_json(control_path) if control_path.exists() else None
            reload_requested = False
            completed = False
            if isinstance(control, dict) and str(control.get("project_id") or "") == project_id:
                expected = str(control.get("expected_version") or "")
                status = str(control.get("status") or "")
                if status == "PENDING":
                    if version == expected:
                        control = {
                            **control,
                            "status": "VERIFYING",
                            "observed_version": version,
                            "extension_id": extension_id or None,
                        }
                        atomic_json(control_path, control)
                    else:
                        reload_requested = True
                elif status == "VERIFYING" and version != expected:
                    reload_requested = True

            status_record = {
                "v": 1,
                "at": now,
                "client_id": client_id,
                "project_id": project_id,
                "version": version,
                "extension_id": extension_id or None,
                "reload_requested": reload_requested,
                "control_request_id": str(control.get("request_id") or "") if isinstance(control, dict) else None,
                "desired_version": desired_version,
                "desired_lane_count": desired_lane_count,
                "desired_enabled_count": desired_enabled_count,
                "bootstrap_parallel_status": bootstrap_parallel_status,
                "lane_runtime": lane_runtime,
                "poll_interval_seconds": req.get("poll_interval_seconds"),
            }
            atomic_json(self.runtime / "extension-status.json", status_record)

        return {
            "ok": True,
            "reload_requested": reload_requested,
            "rehydrate_required": bool(isinstance(control, dict) and str(control.get("status") or "") == "VERIFYING" and version == str(control.get("expected_version") or "")),
            "completed": completed,
            "expected_version": str(control.get("expected_version") or "") if isinstance(control, dict) else None,
            "control_request_id": str(control.get("request_id") or "") if isinstance(control, dict) else None,
            "control_status": str(control.get("status") or "") if isinstance(control, dict) else None,
        }

    def extension_runtime_ready(self, req: dict[str, Any]) -> dict[str, Any]:
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        request_id = self._safe_id(req.get("request_id"), "request_id")
        version = str(req.get("version") or "").strip()
        extension_id = str(req.get("extension_id") or "").strip()
        managed_tabs_ready = int(req.get("managed_tabs_ready") or 0)
        managed_tabs_failed = int(req.get("managed_tabs_failed") or 0)
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        if not version or len(version) > 64:
            raise ValueError("version required")
        if managed_tabs_ready < 0 or managed_tabs_failed < 0:
            raise ValueError("invalid managed tab counts")

        with self.lock:
            control_path = self.runtime / "extension-control.json"
            if not control_path.exists():
                return {"ok": False, "error": "EXTENSION_CONTROL_MISSING"}
            control = read_json(control_path)
            if not isinstance(control, dict) or str(control.get("request_id") or "") != request_id:
                return {"ok": False, "error": "EXTENSION_CONTROL_ID_MISMATCH"}
            if str(control.get("project_id") or "") != project_id:
                return {"ok": False, "error": "EXTENSION_CONTROL_PROJECT_MISMATCH"}
            expected = str(control.get("expected_version") or "")
            if version != expected:
                return {"ok": False, "error": "EXTENSION_VERSION_MISMATCH"}

            failed = managed_tabs_failed > 0
            control = {
                **control,
                "status": "ERROR" if failed else "DONE",
                "completed_at": utc_now(),
                "observed_version": version,
                "extension_id": extension_id or None,
                "managed_tabs_ready": managed_tabs_ready,
                "managed_tabs_failed": managed_tabs_failed,
            }
            atomic_json(control_path, control)
        return {"ok": not failed, "control": control}

    def extension_runtime_status(self, req: dict[str, Any]) -> dict[str, Any]:
        self._safe_client(req.get("client_id"))
        project_id = str(req.get("project_id") or "").strip()
        if not project_id or len(project_id) > 256:
            raise ValueError("project_id required")
        with self.lock:
            status_path = self.runtime / "extension-status.json"
            control_path = self.runtime / "extension-control.json"
            status = read_json(status_path) if status_path.exists() else None
            control = read_json(control_path) if control_path.exists() else None
        return {
            "ok": True,
            "status": status if isinstance(status, dict) else None,
            "control": control if isinstance(control, dict) else None,
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
        return not origin or origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")

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
            elif op == "lane_pool_reset_complete":
                result = complete_lane_pool_reset(store, req)
            elif op == "task_cell_clear_complete":
                result = complete_task_cell_clear(store, req)
            elif op == "action_submit":
                result = stage_action_submit(store, req)
            elif op == "worker_handoff_complete":
                result = complete_worker_handoff(store, req)
            elif op == "dispatch_liveness":
                result = reconcile_dispatch_liveness(store, req)
            elif op == "artifact_read":
                result = store.artifact_read(req)
            elif op == "dispatch_accept":
                result = store.dispatch_accept(req)
            elif op == "dispatch_status":
                result = store.dispatch_status(req)
            elif op == "extension_reload_request":
                result = store.extension_reload_request(req)
            elif op == "extension_runtime_hello":
                result = store.extension_runtime_hello(req)
            elif op == "extension_runtime_ready":
                result = store.extension_runtime_ready(req)
            elif op == "extension_runtime_status":
                result = store.extension_runtime_status(req)
            elif op == "foreground_task_status":
                result = store.foreground_task_status(req)
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
