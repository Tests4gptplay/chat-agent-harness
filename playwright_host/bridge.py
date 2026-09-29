from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


READ_OPS = {
    "health",
    "dispatch_status",
    "worker_takeover_status",
    "foreground_task_status",
    "topology_status",
    "control_status",
    "artifact_read",
    "task_model_policy",
}


class BridgeError(RuntimeError):
    pass


@dataclass
class BridgeClient:
    url: str
    client_id: str
    project_id: str

    @staticmethod
    def timeout_for(op: str) -> float:
        if op == "event":
            return 3.0
        if op in READ_OPS:
            return 12.0
        return 45.0

    def call(self, op: str, **payload: Any) -> dict[str, Any]:
        body = {
            "op": op,
            "client_id": self.client_id,
            "project_id": self.project_id,
            **payload,
        }
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-GAH-Bridge": "1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_for(op)) as response:
                text = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BridgeError(f"bridge request failed: {op}: {exc}") from exc
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"bridge returned non-JSON for {op}") from exc
        if not isinstance(value, dict):
            raise BridgeError(f"bridge returned non-object for {op}")
        if value.get("ok") is False:
            detail = value.get("detail") or value.get("error") or "bridge rejected request"
            raise BridgeError(f"{op}: {detail}")
        return value

    def event(self, event: str, level: str = "info", **data: Any) -> None:
        try:
            self.call("event", event=event, level=level, data=data)
        except BridgeError:
            pass
