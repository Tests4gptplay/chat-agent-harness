from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from .bridge import BridgeClient, BridgeError
from .error_helper import ERROR_HELPER_OWNER, helper_control, record_error
from .config import HostConfig, TASK_CELL
from .state import HostState
from .ui import ChatGPTUI, chatgpt_location, tool_call_blocks
from .worker import WorkerManager
from .mcp import MCPCallPending
from harness.tool_runtime import ToolRegistry


from harness.worker_lineage_compat import (
    LANE_LINEAGE_RESET_COMPLETE_OP,
    LANE_LINEAGE_RESET_KIND,
    WORKER_LINEAGES_KEY,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


PLANNER_WATCHDOG_SECONDS = 30 * 60


class BrowserRuntime:
    """Playwright replacement for the extension service-worker/content-script runtime."""

    CONTROL_HANDLERS = {
        "lane_clear": "_handle_lane_clear",
        "task_cell_project_clear": "_handle_task_cell_project_clear",
        "task_cell_role_prompt": "_handle_role_prompt",
        "task_cell_worker_helper_prompt": "_handle_role_prompt",
        "worker_same_generation_retry": "_handle_worker_same_generation_retry",
        "task_cell_planner_foreground_handoff": "_handle_planner_foreground_handoff",
        "task_cell_planner_successor_bootstrap": "_handle_planner_successor_bootstrap",
        "task_cell_planner_successor_promote": "_handle_planner_successor_promote",
        "task_cell_planner_predecessor_retire": "_handle_planner_predecessor_retire",
        LANE_LINEAGE_RESET_KIND: "_handle_lane_lineage_reset",
        "task_cell_clear": "_handle_task_cell_clear",
        "task_cell_prompt": "_handle_task_cell_prompt",
    }

    def __init__(self, cfg: HostConfig):
        self.cfg = cfg
        self.state = HostState(cfg.state_path)
        self._delivering_error_helper = False
        self.bridge = BridgeClient(cfg.bridge_url, self.state.client_id, cfg.project_id, self._on_harness_error)
        self.ui = ChatGPTUI(cfg.cdp_url, cfg.browser_endpoint_path)
        self.workers = WorkerManager(self.bridge, self.state, self.ui, cfg.project_id)
        self.tool_registry = ToolRegistry()
        self._last_wake_poll = 0.0
        self._last_maintenance_tick = 0.0
        self._seen_role_tool_calls: set[str] = set()

    def _on_harness_error(self, event: str, data: dict[str, Any]) -> None:
        record_error(self.state, event, data)
        self._service_error_helper()

    def _service_error_helper(self) -> None:
        # Reuse one live incident, including if its own delivery fails. This is
        # re-entrancy/idempotency, not a threshold for the first error.
        if self._delivering_error_helper:
            return
        control = helper_control(self.state, TASK_CELL["project_key"])
        if control is None:
            return
        if self.state.get_role(ERROR_HELPER_OWNER, 1, "helper") is not None:
            return
        self._delivering_error_helper = True
        try:
            self._handle_role_prompt(control)
        except Exception as exc:
            record_error(self.state, "playwright.error_helper_delivery", {
                "exception_type": type(exc).__name__, "message": str(exc)[:1000],
            })
        finally:
            self._delivering_error_helper = False

    def _poll_harness_errors(self) -> dict[str, Any]:
        result = self.bridge.call("harness_errors", ack=self.state.data.get("harness_error_acks") or [])
        self.state.data["harness_git_branch"] = result.get("git_branch") or "main"
        rows = result.get("errors") or []
        for row in rows:
            self._on_harness_error(str(row["event"]), row.get("data") or {})
        self.state.data["harness_error_acks"] = [row["error_id"] for row in rows]
        self.state.save()
        return result

    def _run_stage(self, stage: str, operation: Any, default: Any = None) -> Any:
        try:
            result = operation()
        except Exception as exc:
            self.bridge.event("playwright." + stage + "_error", "error",
                              operation=stage, exception_type=type(exc).__name__,
                              message=str(exc)[:1000])
            return default
        for item in result if isinstance(result, list) else [result]:
            if isinstance(item, dict) and item.get("ok") is False:
                self.bridge.event("playwright." + stage + "_error", "error",
                                  operation=stage, message=str(item.get("error") or item)[:1000])
        return result

    def connect(self) -> None:
        self.ui.connect()
        topology = self.bridge.call("topology_status")
        self.state.reconcile_lanes(topology.get("lanes"))

    def close(self) -> None:
        self.workers.mcp.close()
        self.ui.close()

    def _task_cell_binding(self) -> dict[str, Any]:
        return deepcopy(self.state.data["task_cell"])

    def _role_fields(self, control: dict[str, Any]) -> dict[str, Any]:
        value = {
            "request_id": str(control.get("request_id") or "").strip(),
            "task_id": str(control.get("task_id") or "").strip(),
            "role": str(control.get("role") or "").strip().lower(),
            "epoch": int(control.get("control_epoch") or 0),
            "challenge": str(control.get("challenge") or "").strip(),
            "prompt": str(control.get("prompt") or "").strip(),
            "project_key": str(control.get("task_cell_project_key") or "").strip(),
            "thinking_endpoint": str(control.get("thinking_endpoint") or "").strip().lower(),
        }
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", value["request_id"]):
            raise RuntimeError("invalid Task Cell role request id")
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,128}", value["task_id"]):
            raise RuntimeError("invalid Task Cell role task_id")
        if value["role"] not in {"planner", "helper"}:
            raise RuntimeError("invalid Task Cell role")
        if value["epoch"] < 1:
            raise RuntimeError("invalid Task Cell control_epoch")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", value["challenge"]):
            raise RuntimeError("invalid Task Cell role challenge")
        if not value["project_key"].startswith("g-p-"):
            raise RuntimeError("Task Cell project key required")
        if not value["prompt"] or len(value["prompt"]) > 16000:
            raise RuntimeError("Task Cell role prompt must be 1..16000 characters")
        if value["thinking_endpoint"] and value["thinking_endpoint"] not in {"min", "max"}:
            raise RuntimeError("invalid Task Cell thinking_endpoint")
        return value

    def _marker(self, request_id: str) -> str:
        return f"GAH_WAKE v=1 id={request_id} project={self.cfg.project_id.replace(' ', '_')}"

    def _role_text(self, marker: str, fields: dict[str, Any], selected: dict[str, Any] | None) -> str:
        if not selected:
            return marker + "\n" + fields["prompt"]
        return "\n".join([
            marker,
            (
                "CAH_THINKING_ENDPOINT "
                f"requested={fields['thinking_endpoint']} method={selected.get('selection_method') or 'playwright'}"
            ),
            fields["prompt"],
        ])

    def _select_thinking(self, page: Any, fields: dict[str, Any]) -> dict[str, Any] | None:
        endpoint = fields.get("thinking_endpoint")
        return self.ui.select_thinking_endpoint(page, endpoint) if endpoint else None

    def _delete_exact(self, project_key: str, project_root_url: str, conversation_id: str) -> bool:
        receipts = self.state.data.setdefault("chat_deletion_receipts", {})
        key = project_key + "/" + conversation_id
        if receipts.get(key, {}).get("deleted") is True:
            return True
        root = self.ui.project_root(project_key, project_root_url, reuse=False)
        try:
            deleted = self.ui.delete_project_conversation(root, project_key, conversation_id)
            if deleted:
                receipts[key] = {"deleted": True, "observed_at": now()}
                self.state.save()
                for page in self.ui.matching_pages(project_key=project_key, conversation_id=conversation_id):
                    page.close()
            return deleted
        finally:
            try:
                root.close()
            except Exception:
                pass

    def _handle_lane_clear(self, control: dict[str, Any]) -> dict[str, Any]:
        lane_id = str(control.get("control_lane_id") or "")
        project_key = str(control.get("worker_project_key") or "")
        if str(control.get("scope") or "") != "all_project_conversations":
            raise RuntimeError("unsupported lane_clear scope")
        lane = self.state.lane(lane_id)
        if not lane or lane.get("project_key") != project_key:
            raise RuntimeError("lane_clear target mismatch")
        request_id = str(control.get("request_id") or "")
        if str(control.get("status") or "") == "PENDING":
            self.bridge.call(
                "lane_clear_begin",
                request_id=request_id,
                lane_id=lane_id,
                worker_project_key=project_key,
            )
        drained = self.ui.drain_project(project_key, str(lane["project_root_url"]))
        lane[WORKER_LINEAGES_KEY] = {}
        self.state.save()
        return self.bridge.call(
            "lane_clear_complete",
            request_id=request_id,
            lane_id=lane_id,
            worker_project_key=project_key,
            deleted_count=drained["deleted_count"],
            remaining_count=0,
        )

    def _handle_task_cell_project_clear(self, control: dict[str, Any]) -> dict[str, Any]:
        binding = self._task_cell_binding()
        project_key = str(control.get("task_cell_project_key") or binding["project_key"])
        if project_key != binding["project_key"]:
            raise RuntimeError("Task Cell Project clear target mismatch")
        scope = str(control.get("scope") or "")
        if scope not in {"all_project_conversations", "hot_state_reset"}:
            raise RuntimeError("unsupported Task Cell Project clear scope")
        root = self.ui.project_root(project_key, binding["project_root_url"], reuse=False)
        try:
            composer = self.ui.clear_composer(root)
            deleted = 0
            role_count = 0
            successor_count = 0
            if scope == "hot_state_reset":
                task_id = str(control.get("task_id") or "").strip()
                epoch = int(control.get("control_epoch") or 0)
                if not task_id or epoch < 1:
                    raise RuntimeError("Task Cell hot-state reset identity is incomplete")
                successor_count = 1 if self.state.get_successor(task_id, epoch) else 0
                role_count = self.state.clear_task_roles(task_id, epoch)
            else:
                drained = self.ui.drain_project_page(root, project_key)
                deleted = drained["deleted_count"]
                roles = self.state.data["task_cell"].setdefault("roles", {})
                successors = self.state.data["task_cell"].setdefault("planner_successors", {})
                role_count = len(roles)
                successor_count = len(successors)
                roles.clear()
                successors.clear()
                self.state.save()
            return self.bridge.call(
                "task_cell_project_clear_complete",
                request_id=str(control.get("request_id") or ""),
                task_cell_project_key=project_key,
                scope=scope,
                deleted_count=deleted,
                remaining_count=0,
                cleared_role_bindings=role_count,
                cleared_planner_successors=successor_count,
                composer_cleared=bool(composer.get("cleared")),
            )
        finally:
            try:
                root.close()
            except Exception:
                pass

    @staticmethod
    def _planner_watchdog_deadline(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _arm_planner_watchdog(
        self,
        role: dict[str, Any],
        *,
        request_id: str,
        output_ref: str,
        generation: int,
        fence_token: str,
        wake_text: str,
        replay_attempt: int = 0,
    ) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        return {
            **role,
            "planner_generation": int(generation),
            "planner_fence_token": str(fence_token),
            "planner_watchdog": {
                "kind": "PLANNER_PHYSICAL_WATCHDOG",
                "state": "ARMED",
                "timeout_seconds": PLANNER_WATCHDOG_SECONDS,
                "started_at": started.isoformat(timespec="seconds"),
                "deadline_at": (
                    started + timedelta(seconds=PLANNER_WATCHDOG_SECONDS)
                ).isoformat(timespec="seconds"),
                "request_id": str(request_id),
                "output_ref": str(output_ref),
                "planner_generation": int(generation),
                "planner_fence_token": str(fence_token),
                "conversation_id": str(role.get("conversation_id") or ""),
                "wake_text": str(wake_text),
                "replay_attempt": int(replay_attempt),
            },
        }

    def _recover_planner_watchdog(
        self,
        record: dict[str, Any],
        watchdog: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = str(record.get("task_id") or "")
        epoch = int(record.get("control_epoch") or 0)
        old_conversation_id = str(record.get("conversation_id") or "")
        request_id = str(watchdog.get("request_id") or "")
        output_ref = str(watchdog.get("output_ref") or "")
        generation = int(watchdog.get("planner_generation") or 0)
        fence = str(watchdog.get("planner_fence_token") or "")
        wake_text = str(watchdog.get("wake_text") or "")
        if not wake_text:
            return {"ok": False, "error": "PLANNER_WATCHDOG_WAKE_TEXT_MISSING"}

        status = self.bridge.call(
            "planner_watchdog_status",
            task_id=task_id,
            control_epoch=epoch,
            conversation_id=old_conversation_id,
            request_id=request_id,
            output_ref=output_ref,
            planner_generation=generation,
            planner_fence_token=fence,
        )
        if not status.get("ok"):
            return status
        if not status.get("active"):
            return {"ok": True, "idle": status.get("idle") or "planner_watchdog_target_not_current"}

        marker = self._marker(request_id)
        replay_attempt = int(watchdog.get("replay_attempt") or 0) + 1
        replay_id = (
            f"planner-watchdog-replay-{task_id}-{epoch}-{generation}-"
            f"{request_id}-{replay_attempt}"
        )

        page = None
        try:
            page = self.ui.conversation_page(
                project_key=TASK_CELL["project_key"],
                conversation_id=old_conversation_id,
                url=str(record.get("url") or ""),
            )
            self.ui.stop_response(page)
            result = self.ui.submit_text(
                page,
                wake_text,
                marker=marker,
                wait_response_start=True,
                replace_existing=True,
                require_new_turn=True,
            )
            if result.get("response_started"):
                recorded = self.bridge.call(
                    "planner_watchdog_replay_started",
                    replay_id=replay_id,
                    task_id=task_id,
                    control_epoch=epoch,
                    request_id=request_id,
                    output_ref=output_ref,
                    old_conversation_id=old_conversation_id,
                    new_conversation_id=old_conversation_id,
                    new_conversation_url=str(record.get("url") or ""),
                    planner_generation=generation,
                    planner_fence_token=fence,
                    replacement=False,
                )
                if not recorded.get("ok"):
                    raise RuntimeError(str(recorded.get("error") or "PLANNER_WATCHDOG_REPLAY_RECORD_FAILED"))
                next_role = {
                    **record,
                    "request_id": request_id,
                    "active_request_id": request_id,
                    "active_output_ref": output_ref,
                    "response_started": True,
                    "observed_at": now(),
                    "status": "BOUND",
                    "watchdog_recovery": {
                        "replay_id": replay_id,
                        "mode": "same_conversation_replay",
                        "prompt_count": recorded.get("prompt_count"),
                    },
                }
                next_role = self._arm_planner_watchdog(
                    next_role,
                    request_id=request_id,
                    output_ref=output_ref,
                    generation=generation,
                    fence_token=fence,
                    wake_text=wake_text,
                    replay_attempt=replay_attempt,
                )
                self.state.set_role(next_role)
                return {
                    "ok": True,
                    "recovered": True,
                    "mode": "same_conversation_replay",
                    "conversation_id": old_conversation_id,
                    "prompt_count": recorded.get("prompt_count"),
                }
        except Exception as replay_exc:
            replay_error = str(replay_exc)
        else:
            replay_error = "PLANNER_WATCHDOG_REPLAY_DID_NOT_START"

        try:
            deleted = self._delete_exact(
                TASK_CELL["project_key"],
                TASK_CELL["project_root_url"],
                old_conversation_id,
            )
            if not deleted:
                raise RuntimeError("PLANNER_WATCHDOG_EXACT_DELETE_FAILED")
            new_page, new_conversation_id, new_url = self.ui.bootstrap_conversation(
                TASK_CELL["project_key"],
                TASK_CELL["project_root_url"],
                wake_text,
                marker,
            )
            recorded = self.bridge.call(
                "planner_watchdog_replay_started",
                replay_id=replay_id,
                task_id=task_id,
                control_epoch=epoch,
                request_id=request_id,
                output_ref=output_ref,
                old_conversation_id=old_conversation_id,
                new_conversation_id=new_conversation_id,
                new_conversation_url=new_url,
                planner_generation=generation,
                planner_fence_token=fence,
                replacement=True,
            )
            if not recorded.get("ok"):
                raise RuntimeError(str(recorded.get("error") or "PLANNER_WATCHDOG_REPLACEMENT_RECORD_FAILED"))
            next_role = {
                **record,
                "conversation_id": new_conversation_id,
                "url": new_url,
                "request_id": request_id,
                "active_request_id": request_id,
                "active_output_ref": output_ref,
                "response_started": True,
                "observed_at": now(),
                "status": "BOUND",
                "watchdog_recovery": {
                    "replay_id": replay_id,
                    "mode": "physical_conversation_replacement",
                    "old_conversation_id": old_conversation_id,
                    "prompt_count": recorded.get("prompt_count"),
                    "replay_error": replay_error,
                },
            }
            next_role = self._arm_planner_watchdog(
                next_role,
                request_id=request_id,
                output_ref=output_ref,
                generation=generation,
                fence_token=fence,
                wake_text=wake_text,
                replay_attempt=replay_attempt,
            )
            self.state.set_role(next_role)
            return {
                "ok": True,
                "recovered": True,
                "mode": "physical_conversation_replacement",
                "old_conversation_id": old_conversation_id,
                "new_conversation_id": new_conversation_id,
                "prompt_count": recorded.get("prompt_count"),
                "replay_error": replay_error,
            }
        except Exception as replacement_exc:
            return {
                "ok": False,
                "error": "PLANNER_WATCHDOG_RECOVERY_FAILED",
                "replay_error": replay_error,
                "replacement_error": str(replacement_exc),
            }

    def _planner_watchdog_sweep(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        roles = list((self.state.data.get("task_cell") or {}).get("roles", {}).values())
        for record in roles:
            if not isinstance(record, dict) or str(record.get("role") or "") != "planner":
                continue
            watchdog = record.get("planner_watchdog")
            if not isinstance(watchdog, dict) or str(watchdog.get("state") or "") != "ARMED":
                continue
            deadline = self._planner_watchdog_deadline(watchdog.get("deadline_at"))
            if deadline is None or datetime.now(timezone.utc) < deadline:
                continue
            result = self._recover_planner_watchdog(record, watchdog)
            results.append({"ok": bool(result.get("ok")), "planner_watchdog": True, "result": result})
            if result.get("ok") and result.get("idle"):
                self.state.set_role({
                    **record,
                    "planner_watchdog": {
                        **watchdog,
                        "state": "DISARMED",
                        "expired_at": now(),
                        "terminal_reason": result.get("idle"),
                    },
                })
            elif not result.get("ok"):
                restarted = datetime.now(timezone.utc)
                self.state.set_role({
                    **record,
                    "planner_watchdog": {
                        **watchdog,
                        "state": "ARMED",
                        "started_at": restarted.isoformat(timespec="seconds"),
                        "deadline_at": (
                            restarted + timedelta(seconds=PLANNER_WATCHDOG_SECONDS)
                        ).isoformat(timespec="seconds"),
                        "last_recovery_failed_at": restarted.isoformat(timespec="seconds"),
                        "recovery_failure_count": int(watchdog.get("recovery_failure_count") or 0) + 1,
                        "last_error": result.get("error"),
                        "last_replay_error": result.get("replay_error"),
                        "last_replacement_error": result.get("replacement_error"),
                    },
                })
        return results

    def _handle_role_prompt(self, control: dict[str, Any]) -> dict[str, Any]:
        x = self._role_fields(control)
        binding = self._task_cell_binding()
        if x["project_key"] != binding["project_key"]:
            raise RuntimeError("Task Cell role prompt project mismatch")

        role = self.state.get_role(x["task_id"], x["epoch"], x["role"])
        marker = self._marker(x["request_id"])
        if role is not None and x["role"] == "helper" and role.get("request_id") != x["request_id"]:
            return {"ok": True, "idle": "previous_helper_cleanup_pending"}

        if role is None:
            if x["role"] != "helper":
                raise RuntimeError("Task Cell role binding mismatch")

            def prepare_helper(page: Any, _text: str) -> str:
                selected = self._select_thinking(page, x)
                return self._role_text(marker, x, selected)

            _page, conversation_id, url = self.ui.bootstrap_conversation(
                binding["project_key"],
                binding["project_root_url"],
                marker + "\n" + x["prompt"],
                marker,
                before_submit=prepare_helper,
            )
            role = {
                "v": 1,
                "task_id": x["task_id"],
                "control_epoch": x["epoch"],
                "role": "helper",
                "conversation_id": conversation_id,
                "url": url,
                "request_id": x["request_id"],
                "challenge": x["challenge"],
                "response_started": True,
                "observed_at": now(),
                "status": "BOUND",
                "active_request_id": x["request_id"],
                "active_output_ref": str(control.get("required_output_ref") or ""),
                "active_decision_ref": str(control.get("planner_decision_ref") or ""),
                "helper_source": str(control.get("helper_source") or "planner"),
                "control_kind": str(control.get("kind") or "task_cell_role_prompt"),
            }
            self.state.set_role(role)
        else:
            if role.get("challenge") != x["challenge"]:
                raise RuntimeError("Task Cell role binding mismatch")
            page = self.ui.conversation_page(
                project_key=binding["project_key"],
                conversation_id=str(role["conversation_id"]),
                url=str(role["url"]),
            )
            if self.ui.response_running(page):
                return {"ok": True, "idle": "role_response_running"}

            selected = self._select_thinking(page, x)
            submitted_text = self._role_text(marker, x, selected)
            result = self.ui.submit_text(
                page,
                submitted_text,
                marker=marker,
                wait_response_start=True,
                replace_existing=True,
            )
            if not result.get("response_started"):
                raise RuntimeError("Task Cell role response did not start")
            if x["role"] in {"planner", "helper"}:
                role = {
                    **role,
                    "request_id": x["request_id"],
                    "active_request_id": x["request_id"],
                    "active_output_ref": str(control.get("required_output_ref") or ""),
                    "active_decision_ref": str(control.get("planner_decision_ref") or ""),
                    "response_started": True,
                    "observed_at": now(),
                }
                if x["role"] == "planner":
                    role = self._arm_planner_watchdog(
                        role,
                        request_id=x["request_id"],
                        output_ref=str(control.get("required_output_ref") or ""),
                        generation=int(control.get("planner_generation") or role.get("planner_generation") or 0),
                        fence_token=str(control.get("planner_fence_token") or role.get("planner_fence_token") or ""),
                        wake_text=submitted_text,
                    )
                self.state.set_role(role)

        if control.get("helper_source") == "harness_error":
            # This direct-bounded Helper has no business Task Cell to admit and
            # must start even when Git or the control slot is the broken part.
            return {"ok": True, "response_started": True, "conversation_id": role["conversation_id"]}
        return self.bridge.call(
            "task_cell_role_prompt_complete",
            request_id=x["request_id"],
            task_id=x["task_id"],
            control_epoch=x["epoch"],
            role=x["role"],
            challenge=x["challenge"],
            task_cell_project_key=binding["project_key"],
            conversation_id=role["conversation_id"],
            response_started=True,
        )

    def _handle_planner_foreground_handoff(self, control: dict[str, Any]) -> dict[str, Any]:
        request_id = str(control.get("request_id") or "")
        task_id = str(control.get("task_id") or "")
        epoch = int(control.get("control_epoch") or 0)
        project_key = str(control.get("task_cell_project_key") or "")
        if (
            not request_id
            or not task_id
            or epoch < 1
            or project_key != TASK_CELL["project_key"]
        ):
            raise RuntimeError("invalid Foreground to Planner handoff request")
        if str(control.get("phase") or "") == "CREATE_INITIAL_PLANNER":
            staged = {
                "request_id": str(control.get("planner_request_id") or ""),
                "challenge": str(control.get("planner_challenge") or ""),
                "planner_generation": int(control.get("planner_generation") or 0),
                "planner_fence_token": str(control.get("planner_fence_token") or ""),
                "required_output_ref": str(control.get("required_output_ref") or ""),
                "prompt": str(control.get("prompt") or ""),
            }
            if (
                not staged["request_id"]
                or not staged["challenge"]
                or staged["planner_generation"] != 1
                or len(staged["planner_fence_token"]) < 8
                or not staged["required_output_ref"]
                or not staged["prompt"]
            ):
                raise RuntimeError("initial Planner handoff material is incomplete")
        else:
            staged = self.bridge.call(
                "planner_foreground_handoff_begin",
                source_request_id=request_id,
                task_id=task_id,
                control_epoch=epoch,
                task_cell_project_key=project_key,
                task_contract_ref=str(control.get("task_contract_ref") or f"tasks/{task_id}.json"),
                plan_ref=str(control.get("plan_ref") or f"tasks/{task_id}.plan.json"),
            )
        attempt_result = self.bridge.call(
            "planner_foreground_handoff_attempt",
            source_request_id=request_id,
            task_id=task_id,
        )
        if attempt_result.get("ok") is False:
            raise RuntimeError(
                str(attempt_result.get("error") or "initial Planner creation attempt rejected")
            )
        marker = self._marker(staged["request_id"])
        page, conversation_id, url = self.ui.bootstrap_conversation(
            project_key,
            TASK_CELL["project_root_url"],
            marker + "\n" + str(staged["prompt"]),
            marker,
        )
        initial_role = self._arm_planner_watchdog({
            "v": 1,
            "task_id": task_id,
            "control_epoch": epoch,
            "role": "planner",
            "conversation_id": conversation_id,
            "url": url,
            "request_id": staged["request_id"],
            "challenge": staged["challenge"],
            "response_started": True,
            "observed_at": now(),
            "status": "BOUND",
            "active_request_id": staged["request_id"],
            "active_output_ref": str(staged.get("required_output_ref") or ""),
            "active_decision_ref": str(staged.get("required_output_ref") or ""),
        },
            request_id=staged["request_id"],
            output_ref=str(staged.get("required_output_ref") or ""),
            generation=int(staged["planner_generation"]),
            fence_token=str(staged["planner_fence_token"]),
            wake_text=marker + "\n" + str(staged["prompt"]),
        )
        self.state.set_role(initial_role)
        return self.bridge.call(
            "planner_foreground_handoff_bind",
            source_request_id=request_id,
            planner_request_id=staged["request_id"],
            task_id=task_id,
            control_epoch=epoch,
            task_cell_project_key=project_key,
            conversation_id=conversation_id,
            conversation_url=url,
            response_started=True,
        )

    def _handle_planner_successor_bootstrap(self, control: dict[str, Any]) -> dict[str, Any]:
        x = self._role_fields(control)
        handoff_id = str(control.get("handoff_id") or "")
        to_generation = int(control.get("to_generation") or 0)
        pending_fence = str(control.get("pending_fence_token") or "")
        if x["role"] != "planner":
            raise RuntimeError("Planner successor bootstrap requires planner role")
        if x["project_key"] != TASK_CELL["project_key"]:
            raise RuntimeError("Planner successor project mismatch")
        if not handoff_id or to_generation < 2 or len(pending_fence) < 8:
            raise RuntimeError("invalid Planner successor handoff identity")
        prior = self.state.get_successor(x["task_id"], x["epoch"])
        if prior:
            exact = (
                str(prior.get("request_id") or "") == x["request_id"]
                and str(prior.get("challenge") or "") == x["challenge"]
                and str(prior.get("handoff_id") or "") == handoff_id
                and int(prior.get("planner_generation") or 0) == to_generation
                and str(prior.get("planner_fence_token") or "") == pending_fence
            )
            if not exact:
                raise RuntimeError("Planner successor binding conflicts with canonical handoff")
            return self.bridge.call(
                "planner_successor_bootstrap_complete",
                request_id=x["request_id"],
                task_id=x["task_id"],
                control_epoch=x["epoch"],
                task_cell_project_key=x["project_key"],
                conversation_id=str(prior.get("conversation_id") or ""),
                conversation_url=str(prior.get("url") or ""),
                handoff_id=handoff_id,
                response_started=bool(prior.get("response_started")),
            )

        marker = self._marker(x["request_id"])
        prepared_wake: dict[str, str] = {}
        def prepare_successor(page: Any, _text: str) -> str:
            selected = self._select_thinking(page, x)
            text = self._role_text(marker, x, selected)
            prepared_wake["text"] = text
            return text

        page, conversation_id, url = self.ui.bootstrap_conversation(
            x["project_key"],
            TASK_CELL["project_root_url"],
            marker + "\n" + x["prompt"],
            marker,
            before_submit=prepare_successor,
        )
        successor = {
            "v": 1,
            "task_id": x["task_id"],
            "control_epoch": x["epoch"],
            "role": "planner",
            "conversation_id": conversation_id,
            "url": url,
            "request_id": x["request_id"],
            "challenge": x["challenge"],
            "response_started": True,
            "observed_at": now(),
            "status": "BOUND_NON_AUTHORITATIVE",
            "active_request_id": x["request_id"],
            "active_output_ref": str(control.get("required_output_ref") or ""),
            "planner_generation": to_generation,
            "planner_fence_token": pending_fence,
            "handoff_id": handoff_id,
            "semantic_authority": False,
        }
        successor = self._arm_planner_watchdog(
            successor,
            request_id=x["request_id"],
            output_ref=str(control.get("required_output_ref") or ""),
            generation=to_generation,
            fence_token=pending_fence,
            wake_text=prepared_wake.get("text") or (marker + "\n" + x["prompt"]),
        )
        self.state.set_successor(x["task_id"], x["epoch"], successor)
        return self.bridge.call(
            "planner_successor_bootstrap_complete",
            request_id=x["request_id"],
            task_id=x["task_id"],
            control_epoch=x["epoch"],
            task_cell_project_key=x["project_key"],
            conversation_id=conversation_id,
            conversation_url=url,
            handoff_id=handoff_id,
            response_started=True,
        )

    def _handle_planner_successor_promote(self, control: dict[str, Any]) -> dict[str, Any]:
        request_id = str(control.get("request_id") or "")
        task_id = str(control.get("task_id") or "")
        epoch = int(control.get("control_epoch") or 0)
        project_key = str(control.get("task_cell_project_key") or "")
        conversation_id = str(control.get("conversation_id") or "")
        if (
            not request_id
            or not task_id
            or epoch < 1
            or project_key != TASK_CELL["project_key"]
            or not conversation_id
        ):
            raise RuntimeError("invalid Planner successor promotion request")

        self.state.promote_successor(task_id, epoch, conversation_id)
        return self.bridge.call(
            "planner_successor_promote_complete",
            request_id=request_id,
            task_id=task_id,
            control_epoch=epoch,
            task_cell_project_key=project_key,
            conversation_id=conversation_id,
            handoff_id=str(control.get("handoff_id") or ""),
        )

    def _handle_planner_predecessor_retire(self, control: dict[str, Any]) -> dict[str, Any]:
        request_id = str(control.get("request_id") or "")
        task_id = str(control.get("task_id") or "")
        epoch = int(control.get("control_epoch") or 0)
        conversation_id = str(control.get("conversation_id") or "")
        project_key = str(control.get("task_cell_project_key") or "")
        if not request_id or not task_id or epoch < 1 or not conversation_id:
            raise RuntimeError("invalid Planner predecessor retirement request")
        if control.get("exact_id_only") is not True:
            raise RuntimeError("Planner predecessor retirement must be exact-id only")
        if project_key != TASK_CELL["project_key"]:
            raise RuntimeError("Planner predecessor retirement project mismatch")
        active = self.state.get_role(task_id, epoch, "planner")
        if not active:
            raise RuntimeError("Active Planner binding missing during predecessor retirement")
        if active.get("conversation_id") == conversation_id:
            raise RuntimeError("refusing to delete active Planner")
        successor = self.state.get_successor(task_id, epoch)
        if successor and successor.get("conversation_id") == conversation_id:
            raise RuntimeError("refusing to delete pending Planner successor")
        handoff_id = str(control.get("handoff_id") or "")
        deleted = False
        already_absent = False
        error = None
        try:
            deleted = self._delete_exact(
                project_key,
                TASK_CELL["project_root_url"],
                conversation_id,
            )
        except Exception as exc:
            error = str(exc)
        return self.bridge.call(
            "planner_predecessor_retire_complete",
            request_id=request_id,
            task_id=task_id,
            control_epoch=epoch,
            task_cell_project_key=project_key,
            conversation_id=conversation_id,
            handoff_id=handoff_id,
            deleted=deleted,
            already_absent=already_absent,
            error=None if deleted else (error or "exact_conversation_delete_failed"),
        )

    def _handle_worker_same_generation_retry(self, control: dict[str, Any]) -> dict[str, Any]:
        request_id = str(control.get("request_id") or "")
        helper_request_id = str(control.get("helper_request_id") or "")
        task_id = str(control.get("task_id") or "")
        epoch = int(control.get("control_epoch") or 0)
        child_task_id = str(control.get("child_task_id") or "")
        backend_cl = str(control.get("backend_cl") or "")
        dispatch_id = str(control.get("dispatch_id") or "")
        generation = int(control.get("dispatch_generation") or 0)
        fence_token = str(control.get("fence_token") or "")
        wake_id = str(control.get("wake_id") or "")
        worker_ref = str(control.get("worker_ref") or "")
        lane_id = str(control.get("control_lane_id") or "")
        project_key = str(control.get("worker_project_key") or "")
        old_conversation_id = str(control.get("old_conversation_id") or "")
        if (
            not request_id
            or not helper_request_id
            or not task_id
            or epoch < 1
            or not child_task_id
            or not backend_cl
            or not dispatch_id
            or generation < 1
            or len(fence_token) < 8
            or not wake_id
            or not worker_ref
            or not old_conversation_id
            or control.get("exact_id_only") is not True
            or str(control.get("retry_mode") or "") != "same_generation"
        ):
            raise RuntimeError("invalid same-generation Worker retry control")

        lane = self.state.lane(lane_id)
        if not lane or str(lane.get("project_key") or "") != project_key:
            raise RuntimeError("same-generation Worker retry lane mismatch")
        lineage = self.state.get_lineage(lane_id, task_id, epoch, create=False)
        current = lineage.get("current") if isinstance(lineage, dict) else None
        if not isinstance(current, dict):
            raise RuntimeError("same-generation Worker retry current pointer missing")
        # Re-enter a physical attempt already saved locally before doing any UI
        # work. Canonical completion may lag behind semantic completion.
        for candidate in [current, *list(lineage.get("managed") or [])]:
            if not isinstance(candidate, dict):
                continue
            retry = candidate.get("same_generation_retry") or {}
            if (retry.get("request_id") == request_id
                    and candidate.get("handoff_id") == worker_ref
                    and candidate.get("wake_id") == wake_id
                    and candidate.get("conversation_id") != old_conversation_id
                    and candidate.get("handoff_verified") is True):
                if control.get("status") == "PENDING":
                    self.bridge.call("worker_same_generation_retry_begin",
                        **{**control, "lane_id": lane_id})
                return self.workers._complete_same_generation_retry({
                    "owner_task_id": task_id, "task_id": child_task_id,
                    "backend_cl": backend_cl, "dispatch_id": dispatch_id,
                    "dispatch_generation": generation, "fence_token": fence_token,
                    "wake_id": wake_id, "_same_generation_retry": retry,
                }, new_conversation_id=candidate["conversation_id"])
        watchdog = current.get("worker_watchdog") if isinstance(current.get("worker_watchdog"), dict) else {}
        if not (
            str(current.get("conversation_id") or "") == old_conversation_id
            and str(current.get("handoff_id") or "") == worker_ref
            and str(current.get("wake_id") or "") == wake_id
            and str(watchdog.get("child_task_id") or "") == child_task_id
            and str(watchdog.get("backend_cl") or "") == backend_cl
            and str(watchdog.get("dispatch_id") or "") == dispatch_id
            and int(watchdog.get("dispatch_generation") or 0) == generation
            and str(watchdog.get("fence_token") or "") == fence_token
            and (str(watchdog.get("state") or "") == "WORKER_HELPER_REQUESTED"
                 or (watchdog.get("state") == "SAME_GENERATION_RETRY_QUEUED"
                     and watchdog.get("retry_request_id") == request_id))
        ):
            raise RuntimeError("same-generation Worker retry local pointer mismatch")

        # Helper is responsible for exact deletion via browser.cli. Harness does
        # not silently delete here; it verifies absence before requeueing.
        witnesses = [
            str(item.get("conversation_id") or "")
            for item in list(lineage.get("managed") or [])
            if isinstance(item, dict)
            and str(item.get("conversation_id") or "")
            and str(item.get("conversation_id") or "") != old_conversation_id
        ]
        root = self.ui.project_root(project_key, str(lane.get("project_root_url") or ""), reuse=False)
        try:
            if not self.ui.confirm_project_conversation_absent(
                root,
                project_key,
                old_conversation_id,
                witness_conversation_ids=witnesses,
            ):
                raise RuntimeError("WORKER_HELPER_OLD_CONVERSATION_NOT_CONFIRMED_DELETED")
        finally:
            try:
                root.close()
            except Exception:
                pass

        result = self.bridge.call(
            "worker_same_generation_retry_begin",
            request_id=request_id,
            helper_request_id=helper_request_id,
            task_id=task_id,
            control_epoch=epoch,
            child_task_id=child_task_id,
            backend_cl=backend_cl,
            dispatch_id=dispatch_id,
            dispatch_generation=generation,
            fence_token=fence_token,
            wake_id=wake_id,
            worker_ref=worker_ref,
            lane_id=lane_id,
            worker_project_key=project_key,
            old_conversation_id=old_conversation_id,
        )
        if not result.get("ok"):
            return result

        next_watchdog = {
            **watchdog,
            "state": "SAME_GENERATION_RETRY_QUEUED",
            "retry_request_id": request_id,
            "retry_queued_at": now(),
        }
        current = {**current, "worker_watchdog": next_watchdog}
        managed = [
            ({**item, "worker_watchdog": next_watchdog}
             if isinstance(item, dict) and str(item.get("conversation_id") or "") == old_conversation_id
             else item)
            for item in list(lineage.get("managed") or [])
        ]
        self.state.set_lineage(lane_id, {**lineage, "current": current, "managed": managed})
        return result


    def _handle_lane_lineage_reset(self, control: dict[str, Any]) -> dict[str, Any]:
        lane_id = str(control.get("control_lane_id") or "")
        project_key = str(control.get("worker_project_key") or "")
        lane = self.state.lane(lane_id)
        if not lane or str(lane.get("project_key") or "") != project_key:
            raise RuntimeError("lane_pool_reset target is not registered")
        lane[WORKER_LINEAGES_KEY] = {}
        self.state.save()
        return self.bridge.call(
            LANE_LINEAGE_RESET_COMPLETE_OP,
            request_id=str(control.get("request_id") or ""),
            lane_id=lane_id,
            worker_project_key=project_key,
        )

    def _handle_task_cell_clear(self, control: dict[str, Any]) -> dict[str, Any]:
        target_request_id = str(control.get("target_request_id") or "")
        if not target_request_id:
            raise RuntimeError("task_cell_clear requires target_request_id")
        marker = self._marker(target_request_id)
        matches = []
        for page in self.ui.pages():
            loc = chatgpt_location(page.url)
            if not loc or loc.project_key != TASK_CELL["project_key"] or not loc.conversation_id:
                continue
            if self.ui.marker_state(page, marker)["marker_visible"]:
                matches.append((page, loc))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one marked Task Cell conversation, found {len(matches)}")
        page, loc = matches[0]
        self._delete_exact(TASK_CELL["project_key"], TASK_CELL["project_root_url"], str(loc.conversation_id))
        try:
            page.close()
        except Exception:
            pass
        return self.bridge.call(
            "task_cell_clear_complete",
            request_id=str(control.get("request_id") or ""),
            task_cell_project_key=TASK_CELL["project_key"],
            target_request_id=target_request_id,
            deleted_conversation_id=str(loc.conversation_id),
        )

    def _handle_task_cell_prompt(self, control: dict[str, Any]) -> dict[str, Any]:
        prompt = str(control.get("prompt") or "").strip()
        request_id = str(control.get("request_id") or "")
        if not request_id or not prompt:
            raise RuntimeError("task_cell_prompt requires request_id and prompt")
        if len(prompt) > 3600:
            raise RuntimeError("task_cell_prompt is too long")

        marker = self._marker(request_id)
        for page in self.ui.pages():
            location = chatgpt_location(page.url)
            if not location or location.project_key != TASK_CELL["project_key"]:
                continue
            prior = self.ui.marker_state(page, marker)
            if not prior.get("marker_visible"):
                continue
            if not prior.get("response_started"):
                return {
                    "ok": True,
                    "pending": True,
                    "request_id": request_id,
                    "reason": "marker_visible_response_pending",
                }
            return self.bridge.call(
                "task_cell_prompt_complete",
                request_id=request_id,
                task_cell_project_key=TASK_CELL["project_key"],
                response_started=True,
            )

        page = self.ui.project_root(
            TASK_CELL["project_key"],
            TASK_CELL["project_root_url"],
            reuse=True,
        )
        result = self.ui.submit_text(
            page,
            marker + "\n" + prompt,
            marker=marker,
            wait_response_start=True,
        )
        if not result.get("response_started"):
            observed = self.ui.marker_state(page, marker)
            if observed.get("marker_visible"):
                return {
                    "ok": True,
                    "pending": True,
                    "request_id": request_id,
                    "reason": "marker_visible_response_pending",
                }
            raise RuntimeError("Task Cell prompt response did not start")
        return self.bridge.call(
            "task_cell_prompt_complete",
            request_id=request_id,
            task_cell_project_key=TASK_CELL["project_key"],
            response_started=True,
        )

    def _dispatch_control_request(self, control: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(control, dict):
            return None
        kind = str(control.get("kind") or "")
        status = str(control.get("status") or "")
        handler_name = self.CONTROL_HANDLERS.get(kind)
        if not handler_name:
            return None
        if kind in {"lane_clear", "worker_same_generation_retry"}:
            if status not in {"PENDING", "APPLYING"}:
                return None
        elif status != "PENDING":
            return None
        try:
            return getattr(self, handler_name)(control)
        except Exception as exc:
            self.bridge.event(
                "playwright.control_error",
                "error",
                request_id=str(control.get("request_id") or ""),
                kind=kind,
                message=str(exc)[:1000],
            )
            return {"ok": False, "error": str(exc), "kind": kind}

    def _bound_semantic_turns(self) -> list[dict[str, Any]]:
        turns = []
        for record in list((self.state.data.get("task_cell") or {}).get("roles", {}).values()):
            if not isinstance(record, dict) or not record.get("active_output_ref"):
                continue
            turns.append({"role": record["role"], "task_id": record["task_id"],
                          "control_epoch": record["control_epoch"], "conversation_id": record["conversation_id"],
                          "request_id": record.get("active_request_id"), "output_ref": record["active_output_ref"],
                          "helper_source": record.get("helper_source")})
        for lane_id, lane in self.state.data.get("lanes", {}).items():
            for lineage in (lane.get(WORKER_LINEAGES_KEY) or {}).values():
                current = lineage.get("current") or {}
                wd = current.get("worker_watchdog") or {}
                if not current.get("handoff_verified") or current.get("final_signal") or not wd.get("child_task_id"):
                    continue
                turns.append({"role": "worker", "task_id": wd["child_task_id"], "backend_cl": wd["backend_cl"],
                              "dispatch_id": wd["dispatch_id"], "dispatch_generation": wd["dispatch_generation"],
                              "fence_token": wd["fence_token"], "worker_ref": current["handoff_id"],
                              "lane_id": lane_id, "owner_task_id": lineage["owner_task_id"],
                              "owner_control_epoch": lineage["owner_control_epoch"],
                              "conversation_id": current["conversation_id"]})
        return turns

    def _stop_completed_response(self, project_key: str, conversation_id: str) -> None:
        # Semantic state is already committed. Missing/failed UI cannot undo it.
        try:
            page = self.ui.find_page(project_key=project_key, conversation_id=conversation_id)
            if page is not None:
                self.ui.stop_response(page)
        except Exception as exc:
            self.bridge.event("playwright.completed_response_stop_error", "error",
                              conversation_id=conversation_id, message=str(exc)[:1000])

    def _finish_helper_output(self, record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        request_id = str(record.get("active_request_id") or record.get("request_id") or "")
        output_ref = str(record.get("active_output_ref") or "")
        conversation_id = record["conversation_id"]
        if isinstance(result.get("worker_watchdog_restore"), dict):
            self.workers.restore_after_helper(request_id, result["worker_watchdog_restore"])
        pending = record.get("canonical_cleanup_pending") is True
        deleted = pending or self._delete_exact(TASK_CELL["project_key"], TASK_CELL["project_root_url"], conversation_id)
        cleaned = None
        if deleted:
            try:
                if record.get("helper_source") == "harness_error":
                    cleaned = {"ok": True, "cleaned": True}
                else:
                    cleaned = self.bridge.call("helper_cleanup_complete", task_id=record["task_id"],
                        control_epoch=record["control_epoch"], conversation_id=conversation_id,
                        request_id=request_id, output_ref=output_ref)
            except BridgeError as exc:
                cleaned = {"ok": False, "error": str(exc)}
            if cleaned.get("ok") and cleaned.get("cleaned"):
                self.state.clear_role(record["task_id"], record["control_epoch"], "helper", conversation_id)
                if record.get("helper_source") == "harness_error":
                    self.state.data.pop("harness_error", None)
                    self.state.save()
            else:
                self.state.set_role({**record, "canonical_cleanup_pending": True,
                                     "physical_deleted_at": now(), "cleanup_error": cleaned.get("error")})
        return {**result, "helper_deleted": deleted,
                "canonical_helper_cleaned": bool(cleaned and cleaned.get("ok") and cleaned.get("cleaned"))}

    def _finish_git_turns(self, turns: list[dict[str, Any]]) -> None:
        for row in turns:
            binding, result = row["binding"], row["result"]
            if not result.get("accepted"):
                error = result if result.get("ok") is False else result.get("result") or {}
                if error.get("ok") is False:
                    self.bridge.event("playwright.semantic_reconcile_error", "error", role=binding.get("role"),
                                      task_id=binding.get("task_id"), message=str(error)[:1000])
                continue
            try:
                if binding["role"] == "worker":
                    lane_id = binding.get("lane_id")
                    lineage = self.state.get_lineage(str(lane_id or ""), str(binding.get("owner_task_id") or ""),
                                                     int(binding.get("owner_control_epoch") or 0), create=False)
                    current = lineage.get("current") if lineage else None
                    if not current or current.get("conversation_id") != binding.get("conversation_id"):
                        continue
                    self.workers.finish_git_turn(lane_id, lineage, current, row["turn_signal"])
                    self._stop_completed_response(self.state.lane(lane_id)["project_key"], current["conversation_id"])
                    continue
                record = self.state.get_role(binding["task_id"], binding["control_epoch"], binding["role"])
                if not record or record.get("conversation_id") != binding.get("conversation_id"):
                    continue
                if (record.get("active_request_id") != binding.get("request_id")
                        or record.get("active_output_ref") != binding.get("output_ref")):
                    continue
                if binding["role"] == "helper":
                    self._finish_helper_output(record, result)
                else:
                    wd = record.get("planner_watchdog") or {}
                    self.state.set_role({**record, "active_request_id": None, "active_output_ref": None,
                                         "active_decision_ref": None,
                                         "planner_watchdog": {**wd, "state": "DISARMED"}})
                    self._stop_completed_response(TASK_CELL["project_key"], record["conversation_id"])
            except Exception as exc:
                self.bridge.event("playwright.semantic_cleanup_error", "error", role=binding.get("role"),
                                  task_id=binding.get("task_id"), message=str(exc)[:1000])
        # A deleted Helper needs no browser page to finish its existing receipt.
        for record in list((self.state.data.get("task_cell") or {}).get("roles", {}).values()):
            if record.get("role") == "helper" and record.get("canonical_cleanup_pending") is True:
                try:
                    self._finish_helper_output(record, {"ok": True, "accepted": True})
                except Exception as exc:
                    self.bridge.event("playwright.helper_cleanup_error", "error", message=str(exc)[:1000])

    def process_control(self) -> dict[str, Any]:
        planner_result = None
        try:
            planner_result = self.bridge.call("planner_runtime_tick", bound_turns=self._bound_semantic_turns())
            self._finish_git_turns(planner_result.get("semantic_turns") or [])
        except BridgeError:
            pass
        # Consume Git facts before physical watchdog/replay or a new role wake.
        self._process_final_delivery()
        self._planner_watchdog_sweep()
        status = self.bridge.call("control_status")
        handled = self._dispatch_control_request(status.get("control_request"))
        return handled if handled is not None else planner_result or {"ok": True, "idle": "no_control_action"}

    def _process_bound_tool_call(
        self,
        page: Any,
        *,
        role: str,
        identity: str,
        marker: str | None = None,
    ) -> dict[str, Any] | None:
        if self.ui.response_running(page):
            return None

        if marker:
            turn = self.ui.marker_state(page, marker)
            if not turn.get("response_ended"):
                return None
            texts = turn.get("assistant_texts") or []
        else:
            assistant = page.locator('[data-message-author-role="assistant"]')
            texts = [assistant.last.inner_text() or ""] if assistant.count() else []
        for text in reversed(texts):
            for call in reversed(tool_call_blocks(text)):
                call_id = str(call.get("call_id") or "")
                tool = str(call.get("tool") or "")
                if (
                    call.get("v") != 1
                    or not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", call_id)
                    or not tool
                ):
                    continue

                key = "|".join([role, identity, call_id, tool])
                if key in self._seen_role_tool_calls:
                    continue

                for visible_status in ("PASS", "ERROR"):
                    result_marker = self.workers._tool_result_marker(call_id, tool, visible_status)
                    try:
                        if self.ui.marker_state(page, result_marker).get("marker_visible"):
                            self._seen_role_tool_calls.add(key)
                            return {
                                "ok": True,
                                "role": role,
                                "tool_call": call_id,
                                "tool": tool,
                                "deduped": True,
                            }
                    except Exception:
                        pass

                status = "PASS"
                result = None
                error = None
                try:
                    tool_meta = self.tool_registry.get(tool)
                    if (
                        not isinstance(tool_meta, dict)
                        or role not in (tool_meta.get("roles") or [])
                        or str(tool_meta.get("provider") or "") not in {"playwright_host", "playwright_mcp"}
                    ):
                        raise RuntimeError("CAH_TOOL_NOT_AVAILABLE_FOR_ROLE")
                    result = self.workers._execute_browser_tool(call, page, role=role)
                    if isinstance(result, dict) and result.get("isError"):
                        status = "ERROR"
                        error = "Official Playwright MCP returned isError; inspect result content"
                except MCPCallPending:
                    return {"ok": True, "role": role, "tool_call": call_id, "pending": True}
                except Exception as exc:
                    status = "ERROR"
                    error = str(exc)

                self.workers._submit_tool_result(
                    page,
                    call,
                    status,
                    result,
                    error,
                    context_line=(
                        f"CAH_TOOL_RESULT_CONTEXT same_role_turn=true role={role}; "
                        "continue the existing semantic work from this ephemeral tool result. "
                        "This is runtime data, not a new user-authored task."
                    ),
                )
                self.workers._release_browser_tool(call, page)
                self._seen_role_tool_calls.add(key)
                return {
                    "ok": True,
                    "role": role,
                    "tool_call": call_id,
                    "tool": tool,
                    "deduped": False,
                    "status": status,
                }
        return None

    def _process_semantic_tool_calls(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen_conversations: set[str] = set()
        task_cell = self.state.data.get("task_cell") or {}

        records = [
            value for value in (task_cell.get("roles") or {}).values()
            if isinstance(value, dict)
        ]
        records.extend(
            value for value in (task_cell.get("planner_successors") or {}).values()
            if isinstance(value, dict)
        )

        for record in records:
            role = str(record.get("role") or "").strip().lower()
            # Helper is intentionally excluded from conversational Tool transport.
            # Its recovery control plane is canonical Git/runtime state and bound result writes.
            if role != "planner":
                continue
            conversation_id = str(record.get("conversation_id") or "")
            if not conversation_id or conversation_id in seen_conversations:
                continue
            page = self.ui.find_page(
                project_key=TASK_CELL["project_key"],
                conversation_id=conversation_id,
            )
            if page is None:
                continue
            seen_conversations.add(conversation_id)
            handled = self._process_bound_tool_call(
                page,
                role=role,
                identity=conversation_id,
                marker=self._marker(str(record.get("active_request_id") or record.get("request_id") or "")),
            )
            if handled is not None:
                results.append(handled)

        foreground = self._foreground_page()
        if foreground is not None:
            location = chatgpt_location(foreground.url)
            identity = location.conversation_id if location and location.conversation_id else foreground.url
            if str(identity) not in seen_conversations:
                handled = self._process_bound_tool_call(
                    foreground,
                    role="foreground",
                    identity=str(identity),
                )
                if handled is not None:
                    results.append(handled)

        return results

    def _foreground_page(self) -> Any | None:
        url = str((self.state.data.get("foreground") or {}).get("conversation_url") or "")
        if not url:
            return None
        location = chatgpt_location(url)
        if not location or not location.conversation_id:
            return None
        return self.ui.conversation_page(
            project_key=location.project_key,
            conversation_id=location.conversation_id,
            url=url,
        )

    @staticmethod
    def _planner_final_text(event: dict[str, Any]) -> str:
        task_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(event.get("task_id") or ""))[:128]
        if str(event.get("kind") or "") == "planner_user_query":
            return "\n".join([
                "GAH_FOREGROUND v=1 task=" + task_id
                + " event=user-query status=NEED_USER"
                + " event_id=" + str(event.get("event_id") or ""),
                "CAH Planner requests user input.",
                "question=" + str(event.get("question") or ""),
                "task_cell_ref=" + str(event.get("task_cell_ref") or ""),
                "decision_ref=" + str(event.get("decision_ref") or ""),
                (
                    "Present this question to the user. When the user replies, update the existing Task Contract "
                    "with the new intent; Harness will derive the foreground_intent event from that committed write."
                ),
            ])
        lines = [
            "GAH_FOREGROUND v=1 task=" + task_id
            + " event=terminal status=" + str(event.get("terminal_status") or "PASS")
            + " event_id=" + str(event.get("event_id") or ""),
            "CAH Planner final delivery. Canonical Git is semantic authority.",
            "task_cell_ref=" + str(event.get("task_cell_ref") or ""),
            "decision_ref=" + str(event.get("decision_ref") or ""),
            "result_ref=" + str(event.get("result_ref") or ""),
        ]
        if str(event.get("terminal_status") or "") == "ERROR":
            lines.append("error_code=" + str(event.get("error_code") or "PLANNER_ERROR"))
        lines.append("Re-read the exact Task Cell final-delivery state and durable result, then report the outcome.")
        return "\n".join(lines)

    @staticmethod
    def _foreground_terminal_text(event: dict[str, Any]) -> str:
        task_id = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(event.get("task_id") or ""))[:128]
        return "\n".join([
            "GAH_FOREGROUND v=1 task=" + task_id
            + " event=terminal status=" + str(event.get("terminal_status") or "")
            + " event_id=" + str(event.get("event_id") or ""),
            "GAH terminal transport only. Canonical Git is semantic authority.",
            "foreground_cl=" + str(event.get("foreground_cl") or ""),
            "result_ref=" + str(event.get("result_ref") or ""),
            (
                "Re-read the exact foreground CL terminal_event and referenced durable result before reporting "
                "the outcome. Do not infer success from this doorbell and do not schedule a Worker continuation."
            ),
        ])

    def _clean_task_conversations(self, event: dict[str, Any]) -> dict[str, Any]:
        task_id, epoch = str(event["task_id"]), int(event["control_epoch"])
        receipts = self.state.data.setdefault("terminal_cleanup", {})
        receipt = receipts.get(event["event_id"])
        if receipt is None:
            targets = {}
            for lane_id, lane in self.state.data.get("lanes", {}).items():
                lineage = self.state.get_lineage(lane_id, task_id, epoch, create=False)
                if not lineage:
                    continue
                for row in [lineage.get("current"), *(lineage.get("managed") or [])]:
                    if isinstance(row, dict) and row.get("conversation_id"):
                        key = lane["project_key"] + ":" + row["conversation_id"]
                        targets[key] = {"project_key": lane["project_key"], "project_root_url": lane["project_root_url"], "conversation_id": row["conversation_id"]}
            for row in list(self.state.data["task_cell"].get("roles", {}).values()) + [self.state.get_successor(task_id, epoch)]:
                if isinstance(row, dict) and row.get("task_id") == task_id and int(row.get("control_epoch") or 0) == epoch:
                    cid = str(row.get("conversation_id") or "")
                    if cid:
                        targets[TASK_CELL["project_key"] + ":" + cid] = {"project_key": TASK_CELL["project_key"], "project_root_url": TASK_CELL["project_root_url"], "conversation_id": cid}
            receipt = {"targets": targets, "deleted": [], "all_deleted": False}
            receipts[event["event_id"]] = receipt
            self.state.save()
        for key, target in receipt["targets"].items():
            if key in receipt["deleted"]:
                continue
            if not self._delete_exact(**target):
                return {"ok": False, "error": "task_chat_delete_unconfirmed", "target": target}
            receipt["deleted"].append(key)
            self.state.save()
        receipt["all_deleted"] = True
        self.state.save()
        result = self.bridge.call("planner_final_delivery_cleaned", task_cell_ref=event["task_cell_ref"],
            event_id=event["event_id"], claim_id=event["claim_id"], cleanup_receipt=receipt)
        if result.get("ok"):
            self.state.clear_task_roles(task_id, epoch)
            for lane_id in list(self.state.data.get("lanes", {})):
                self.state.remove_lineage(lane_id, task_id, epoch)
        return result

    def _deliver_terminal_event(self, event: dict[str, Any], *, planner: bool) -> dict[str, Any]:
        if planner and event.get("kind") == "planner_final_delivery" and not event.get("cleanup_complete"):
            cleaned = self._clean_task_conversations(event)
            if not cleaned.get("ok"):
                return cleaned
            event = cleaned.get("event") or event
        page = self._foreground_page()
        if page is None:
            return {"ok": True, "idle": "foreground_page_unresolved", "cleanup_complete": event.get("cleanup_complete", False)}

        if planner:
            claim_id = str(event.get("claim_id") or "")
            state = str(event.get("state") or "")
            text = self._planner_final_text(event)
        else:
            delivery = event.get("delivery") if isinstance(event.get("delivery"), dict) else {}
            claim_id = str(delivery.get("claim_id") or "")
            state = str(delivery.get("state") or "")
            text = self._foreground_terminal_text(event)

        event_id = str(event.get("event_id") or "")
        if not event_id or not claim_id.startswith("claim-"):
            return {"ok": False, "error": "terminal event claim identity incomplete"}

        marker = text.splitlines()[0].strip()
        marker_state = self.ui.marker_state(page, marker)

        if state in {"CLAIMED", "DELIVERED"} and not marker_state.get("marker_visible"):
            submitted = self.ui.submit_text(
                page,
                text,
                marker=marker,
                wait_response_start=True,
            )
            if not submitted.get("response_started"):
                return {"ok": True, "idle": "terminal_response_not_started"}
            marker_state = self.ui.marker_state(page, marker)

        if state == "CLAIMED":
            if planner:
                self.bridge.call(
                    "planner_final_delivery_delivered",
                    task_cell_ref=str(event.get("task_cell_ref") or ""),
                    event_id=event_id,
                    claim_id=claim_id,
                )
            else:
                self.bridge.call(
                    "foreground_terminal_delivered",
                    foreground_cl=str(event.get("foreground_cl") or ""),
                    event_id=event_id,
                    claim_id=claim_id,
                )
            state = "DELIVERED"

        if state == "DELIVERED" and marker_state.get("response_ended"):
            if planner:
                result = self.bridge.call(
                    "planner_final_delivery_consumed",
                    task_cell_ref=str(event.get("task_cell_ref") or ""),
                    event_id=event_id,
                    claim_id=claim_id,
                )
                if str(event.get("kind") or "") == "planner_final_delivery":
                    task_id = str(event.get("task_id") or "")
                    epoch = int(event.get("control_epoch") or 0)
                    if task_id and epoch > 0:
                        self.state.clear_task_roles(task_id, epoch)
                        for lane_id in list((self.state.data.get("lanes") or {}).keys()):
                            lineage = self.state.get_lineage(lane_id, task_id, epoch, create=False)
                            if not isinstance(lineage, dict):
                                continue
                            managed = []
                            current_id = str((lineage.get("current") or {}).get("conversation_id") or "")
                            for item in list(lineage.get("managed") or []):
                                if not isinstance(item, dict):
                                    continue
                                if current_id and str(item.get("conversation_id") or "") == current_id:
                                    managed.append({**item, "status": "standby"})
                                else:
                                    managed.append(item)
                            lineage["current"] = None
                            lineage["handoff"] = None
                            lineage["managed"] = managed
                            self.state.set_lineage(lane_id, lineage)
            else:
                result = self.bridge.call(
                    "foreground_terminal_consumed",
                    foreground_cl=str(event.get("foreground_cl") or ""),
                    event_id=event_id,
                    claim_id=claim_id,
                )
                self.state.data.setdefault("foreground", {}).pop("pending_terminal", None)
                self.state.save()
            return {"ok": True, "consumed": True, "result": result}

        if not planner and state == "DELIVERED":
            self.state.data.setdefault("foreground", {})["pending_terminal"] = {
                "event_id": event_id,
                "task_id": str(event.get("task_id") or ""),
                "terminal_status": str(event.get("terminal_status") or ""),
                "foreground_cl": str(event.get("foreground_cl") or ""),
                "claim_id": claim_id,
            }
            self.state.save()
        return {"ok": True, "delivered": state == "DELIVERED"}

    def _observe_foreground_terminal(self) -> None:
        pending = (self.state.data.get("foreground") or {}).get("pending_terminal")
        if not isinstance(pending, dict):
            return
        page = self._foreground_page()
        if page is None:
            return
        marker = self._foreground_terminal_text(pending).splitlines()[0].strip()
        if not self.ui.marker_state(page, marker).get("response_ended"):
            return
        self.bridge.call(
            "foreground_terminal_delivered",
            foreground_cl=str(pending.get("foreground_cl") or ""),
            event_id=str(pending.get("event_id") or ""),
            claim_id=str(pending.get("claim_id") or ""),
        )
        self.bridge.call(
            "foreground_terminal_consumed",
            foreground_cl=str(pending.get("foreground_cl") or ""),
            event_id=str(pending.get("event_id") or ""),
            claim_id=str(pending.get("claim_id") or ""),
        )
        self.state.data.setdefault("foreground", {}).pop("pending_terminal", None)
        self.state.save()

    def _process_final_delivery(self) -> None:
        try:
            result = self.bridge.call("planner_final_delivery_status")
            event = result.get("event") or result.get("delivered_wait_response_end")
            if isinstance(event, dict):
                self._deliver_terminal_event(event, planner=True)
        except Exception as exc:
            self.bridge.event("planner.final_delivery_error", "error", message=str(exc)[:800])

        try:
            result = self.bridge.call("foreground_terminal_status")
            event = result.get("event")
            if isinstance(event, dict):
                self._deliver_terminal_event(event, planner=False)
        except Exception as exc:
            self.bridge.event("foreground.monitor_error", "error", message=str(exc)[:800])

    def tick(self) -> dict[str, Any]:
        current = time.monotonic()
        self._run_stage("error_poll", self._poll_harness_errors)
        self._service_error_helper()
        control_result = self._run_stage("control", self.process_control)
        observed_workers = self._run_stage("worker_observe", self.workers.observe, [])
        semantic_tools = self._run_stage("semantic_tools", self._process_semantic_tool_calls, [])
        self._run_stage("foreground_observe", self._observe_foreground_terminal)
        wakes: list[dict[str, Any]] = []
        maintenance: list[dict[str, Any]] = []
        liveness: list[dict[str, Any]] = []

        if current - self._last_wake_poll >= self.cfg.wake_poll_seconds:
            self._last_wake_poll = current
            for lane_id, lane in (self.state.data.get("lanes") or {}).items():
                if isinstance(lane, dict) and lane.get("enabled", True) and not lane.get("pending_remove"):
                    wakes.append(self._run_stage("wake_poll", lambda: self.workers.poll_lane(lane_id)))

        if current - self._last_maintenance_tick >= self.cfg.maintenance_seconds:
            self._last_maintenance_tick = current
            topology = self._run_stage("topology", lambda: self.bridge.call("topology_status"), {})
            self._run_stage("topology_reconcile", lambda: self.state.reconcile_lanes(topology.get("lanes")))
            maintenance = self._run_stage("maintenance", self.workers.maintain, [])
            liveness = self._run_stage("liveness", self.workers.liveness_sweep, [])

        self._service_error_helper()
        return {
            "ok": True, "control": control_result, "wakes": wakes,
            "semantic_tools": semantic_tools,
            "observed_workers": observed_workers, "maintenance": maintenance,
            "liveness": liveness,
        }

    def run_forever(self) -> None:
        try:
            try:
                self.connect()
            except Exception as exc:
                self.bridge.event("playwright.connect_error", "error", message=str(exc)[:1000])
            while True:
                try:
                    self.tick()
                except Exception as exc:
                    self.bridge.event("playwright.tick_error", "error", message=str(exc)[:1000])
                # Stage isolation must not suppress the existing CDP reconnect.
                if not self.ui.browser or not self.ui.browser.is_connected():
                    try:
                        self.close()
                        self.connect()
                    except Exception as reconnect_error:
                        self.bridge.event("playwright.reconnect_error", "error", message=str(reconnect_error)[:1000])
                time.sleep(1.0)
        finally:
            self.close()
