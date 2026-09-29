from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .bridge import BridgeClient, BridgeError
from .config import HostConfig, TASK_CELL
from .state import HostState
from .ui import ChatGPTUI, chatgpt_location, syscall_blocks, tool_call_blocks
from .worker import WorkerManager
from .mcp import MCPCallPending
from harness.tool_runtime import ToolRegistry


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BrowserRuntime:
    """Playwright replacement for the extension service-worker/content-script runtime."""

    CONTROL_HANDLERS = {
        "lane_clear": "_handle_lane_clear",
        "task_cell_project_clear": "_handle_task_cell_project_clear",
        "task_cell_role_prompt": "_handle_role_prompt",
        "task_cell_worker_helper_prompt": "_handle_role_prompt",
        "task_cell_planner_foreground_handoff": "_handle_planner_foreground_handoff",
        "task_cell_planner_successor_bootstrap": "_handle_planner_successor_bootstrap",
        "task_cell_planner_successor_promote": "_handle_planner_successor_promote",
        "task_cell_planner_predecessor_retire": "_handle_planner_predecessor_retire",
        "lane_pool_reset": "_handle_lane_pool_reset",
        "task_cell_clear": "_handle_task_cell_clear",
        "task_cell_prompt": "_handle_task_cell_prompt",
    }

    def __init__(self, cfg: HostConfig):
        self.cfg = cfg
        self.state = HostState(cfg.state_path)
        self.bridge = BridgeClient(cfg.bridge_url, self.state.client_id, cfg.project_id)
        self.ui = ChatGPTUI(cfg.cdp_url, cfg.browser_endpoint_path)
        self.workers = WorkerManager(self.bridge, self.state, self.ui, cfg.project_id)
        self.tool_registry = ToolRegistry()
        self._last_wake_poll = 0.0
        self._last_maintenance_tick = 0.0
        self._seen_role_syscalls: set[str] = set()
        self._seen_role_tool_calls: set[str] = set()

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
        lane["task_pools"] = {}
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
            result = self.ui.submit_text(
                page,
                self._role_text(marker, x, selected),
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
                self.state.set_role(role)

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
        if not control.get("bootstrap_recovery_ref"):
            self.bridge.call(
                "planner_foreground_handoff_attempt",
                source_request_id=request_id,
                task_id=task_id,
            )
        marker = self._marker(staged["request_id"])
        page, conversation_id, url = self.ui.bootstrap_conversation(
            project_key,
            TASK_CELL["project_root_url"],
            marker + "\n" + str(staged["prompt"]),
            marker,
            require_existing=bool(control.get("bootstrap_recovery_ref")),
        )
        self.state.set_role({
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
        })
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
        def prepare_successor(page: Any, _text: str) -> str:
            selected = self._select_thinking(page, x)
            return self._role_text(marker, x, selected)

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

    def _handle_lane_pool_reset(self, control: dict[str, Any]) -> dict[str, Any]:
        lane_id = str(control.get("control_lane_id") or "")
        project_key = str(control.get("worker_project_key") or "")
        lane = self.state.lane(lane_id)
        if not lane or str(lane.get("project_key") or "") != project_key:
            raise RuntimeError("lane_pool_reset target is not registered")
        lane["task_pools"] = {}
        self.state.save()
        return self.bridge.call(
            "lane_pool_reset_complete",
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
        if kind == "lane_clear":
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

    def process_control(self) -> dict[str, Any]:
        # Watchdog must run even when a control delivery keeps failing.
        self._process_final_delivery()
        planner_result: dict[str, Any] | None = None
        try:
            planner_result = self.bridge.call("planner_runtime_tick")
        except BridgeError:
            planner_result = None

        status = self.bridge.call("control_status")
        handled = self._dispatch_control_request(status.get("control_request"))
        if handled is not None:
            return handled

        return planner_result or {"ok": True, "idle": "no_control_action"}

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
            # Its recovery control plane is canonical Git/runtime state + semantic_sync.
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

    def _process_role_syscalls(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
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
            conversation_id = str(record.get("conversation_id") or "")
            if not conversation_id:
                continue
            page = self.ui.find_page(
                project_key=TASK_CELL["project_key"],
                conversation_id=conversation_id,
            )
            if page is None:
                continue
            role = str(record.get("role") or "")
            helper_removed = False
            request_id = str(record.get("active_request_id") or "")
            output_ref = str(record.get("active_output_ref") or "")
            task_id = str(record.get("task_id") or "")
            if role in {"planner", "helper"} and request_id and output_ref and task_id:
                bound_turn = self.ui.marker_state(page, self._marker(request_id))
                for text in bound_turn.get("assistant_texts") or []:
                    for syscall in syscall_blocks(text):
                        if syscall.get("v") != 1 or syscall.get("kind") != "semantic_sync":
                            continue
                        call_id = str(syscall.get("call_id") or "")
                        if not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", call_id):
                            continue
                        key = "|".join(["semantic_sync", role, task_id, request_id, call_id])
                        if key in self._seen_role_syscalls:
                            continue
                        result = self.bridge.call(
                            "semantic_turn_sync",
                            role=role,
                            task_id=task_id,
                            control_epoch=int(record.get("control_epoch") or 0),
                            conversation_id=conversation_id,
                            request_id=request_id,
                            output_ref=output_ref,
                        )
                        if result.get("accepted"):
                            if role == "planner":
                                self._seen_role_syscalls.add(key)
                                self.state.set_role({
                                    **record,
                                    "active_request_id": None,
                                    "active_output_ref": None,
                                    "active_decision_ref": None,
                                })
                            else:
                                # Helper done is durable Git state, not a chat-ending
                                # word. Once canonical semantic_sync accepts it, the
                                # single-use Helper has finished and is deleted now;
                                # do not wait for ChatGPT response-end.
                                deleted = self._delete_exact(
                                    TASK_CELL["project_key"],
                                    TASK_CELL["project_root_url"],
                                    conversation_id,
                                )
                                if deleted:
                                    self.state.clear_role(
                                        record["task_id"],
                                        record["control_epoch"],
                                        "helper",
                                        conversation_id,
                                    )
                                    self._seen_role_syscalls.add(key)
                                    helper_removed = True
                                result = {**result, "helper_deleted": deleted}
                        elif role != "helper":
                            # Preserve historical Planner syscall dedupe. Helper
                            # retries an unaccepted sync on later ticks because its
                            # completion has no response-end fallback anymore.
                            self._seen_role_syscalls.add(key)
                        results.append({
                            "kind": "semantic_sync",
                            "call_id": call_id,
                            "task_id": task_id,
                            "role": role,
                            "accepted": bool(result.get("accepted")),
                            "result": result,
                        })
                        if helper_removed:
                            break
                    if helper_removed:
                        break

            if helper_removed:
                continue

            assistant = page.locator('[data-message-author-role="assistant"]')
            start = max(0, assistant.count() - 6)
            for index in range(start, assistant.count()):
                text = assistant.nth(index).inner_text() or ""
                for syscall in syscall_blocks(text):
                    if syscall.get("v") != 1 or syscall.get("kind") != "planner_git_cli":
                        continue
                    call_id = str(syscall.get("call_id") or "")
                    task_id = str(syscall.get("task_id") or "")
                    generation = int(syscall.get("planner_generation") or 0)
                    output_ref = str(syscall.get("output_ref") or "")
                    key = "|".join(["planner_git_cli", call_id, task_id, str(generation), output_ref])
                    if not call_id or not task_id or generation < 1 or not output_ref:
                        continue
                    if key in self._seen_role_syscalls:
                        continue

                    # Existing extension semantics: Planner Git CLI is one bounded
                    # fallback attempt, not a browser retry loop.
                    self._seen_role_syscalls.add(key)
                    observed = chatgpt_location(page.url)
                    result = self.bridge.call(
                        "planner_git_cli",
                        task_id=task_id,
                        control_epoch=int(syscall.get("control_epoch") or 0),
                        planner_generation=generation,
                        planner_fence_token=str(syscall.get("planner_fence_token") or ""),
                        conversation_id=str(syscall.get("conversation_id") or ""),
                        observed_conversation_id=(observed.conversation_id if observed else ""),
                        output_ref=output_ref,
                        artifact=syscall.get("artifact"),
                    )
                    results.append({
                        "kind": "planner_git_cli",
                        "call_id": call_id,
                        "task_id": task_id,
                        "output_ref": output_ref,
                        "duplicate": bool(result.get("duplicate")),
                    })
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
                pool = self.state.get_pool(lane_id, task_id, epoch, create=False)
                if not pool:
                    continue
                for row in [pool.get("current"), *(pool.get("managed") or [])]:
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
                self.state.remove_pool(lane_id, task_id, epoch)
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
                            pool = self.state.get_pool(lane_id, task_id, epoch, create=False)
                            if not isinstance(pool, dict):
                                continue
                            managed = []
                            current_id = str((pool.get("current") or {}).get("conversation_id") or "")
                            for item in list(pool.get("managed") or []):
                                if not isinstance(item, dict):
                                    continue
                                if current_id and str(item.get("conversation_id") or "") == current_id:
                                    managed.append({**item, "status": "standby"})
                                else:
                                    managed.append(item)
                            pool["current"] = None
                            pool["handoff"] = None
                            pool["managed"] = managed
                            self.state.set_pool(lane_id, pool)
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

    def _observe_planner_output(self, control_result: dict[str, Any] | None) -> dict[str, Any] | None:
        roles = (self.state.data.get("task_cell") or {}).get("roles") or {}
        for record in list(roles.values()):
            if not isinstance(record, dict) or record.get("role") != "planner":
                continue
            request_id = str(record.get("active_request_id") or "")
            if not request_id or not record.get("active_output_ref"):
                continue
            page = self.ui.find_page(project_key=TASK_CELL["project_key"],
                                     conversation_id=str(record.get("conversation_id") or ""))
            if page is None:
                continue
            turn = self.ui.marker_state(page, self._marker(request_id))
            if not turn.get("response_ended"):
                continue
            result = self.bridge.call(
                "semantic_turn_sync", role="planner",
                task_id=record["task_id"], control_epoch=record["control_epoch"],
                conversation_id=record["conversation_id"], request_id=request_id,
                output_ref=record["active_output_ref"],
            )
            if not result.get("accepted") and turn.get("signal"):
                result = self.bridge.call(
                    "semantic_turn_observe", role="planner", signal=turn["signal"],
                    task_id=record["task_id"], control_epoch=record["control_epoch"],
                    conversation_id=record["conversation_id"], request_id=request_id,
                    output_ref=record["active_output_ref"],
                )
            if result.get("accepted"):
                # Planner done consumes work; it never takes the Helper delete path.
                self.state.set_role({**record, "active_request_id": None,
                                     "active_output_ref": None, "active_decision_ref": None})
            return result
        return None

    def _process_final_delivery(self) -> None:
        try:
            result = self.bridge.call("planner_final_delivery_status")
            event = result.get("event") or result.get("delivered_wait_response_end")
            if isinstance(event, dict):
                self._deliver_terminal_event(event, planner=True)
        except Exception as exc:
            self.bridge.event("planner.final_delivery_error", "warn", message=str(exc)[:800])

        try:
            result = self.bridge.call("foreground_terminal_status")
            event = result.get("event")
            if isinstance(event, dict):
                self._deliver_terminal_event(event, planner=False)
        except Exception as exc:
            self.bridge.event("foreground.monitor_error", "warn", message=str(exc)[:800])

    def tick(self) -> dict[str, Any]:
        current = time.monotonic()
        observed_workers = self.workers.observe()
        semantic_tools = self._process_semantic_tool_calls()
        role_syscalls = self._process_role_syscalls()
        self._observe_foreground_terminal()

        # Response-end detection is already available from the live ChatGPT DOM.
        # Consume completed Planner/Helper turns on the normal host tick instead
        # of waiting for the unrelated 60s maintenance sweep.
        planner_observation = self._observe_planner_output(None)
        if planner_observation is not None:
            role_syscalls.append(planner_observation)
        # Reduce any newly completed semantic turn immediately. This can create
        # the next bound role prompt/wake in the same host tick.
        control_result = self.process_control()
        wakes: list[dict[str, Any]] = []
        maintenance: list[dict[str, Any]] = []
        liveness: list[dict[str, Any]] = []

        if current - self._last_wake_poll >= self.cfg.wake_poll_seconds:
            self._last_wake_poll = current
            for lane_id, lane in (self.state.data.get("lanes") or {}).items():
                if isinstance(lane, dict) and lane.get("enabled", True) and not lane.get("pending_remove"):
                    try:
                        wakes.append(self.workers.poll_lane(lane_id))
                    except Exception as exc:
                        self.bridge.event(
                            "playwright.wake_error",
                            "warn",
                            lane_id=lane_id,
                            message=str(exc)[:800],
                        )

        if current - self._last_maintenance_tick >= self.cfg.maintenance_seconds:
            self._last_maintenance_tick = current
            topology = self.bridge.call("topology_status")
            self.state.reconcile_lanes(topology.get("lanes"))
            maintenance = self.workers.maintain()
            liveness = self.workers.liveness_sweep()

        return {
            "ok": True,
            "control": control_result,
            "wakes": wakes,
            "role_syscalls": role_syscalls,
            "semantic_tools": semantic_tools,
            "observed_workers": observed_workers,
            "maintenance": maintenance,
            "liveness": liveness,
        }

    def run_forever(self) -> None:
        self.connect()
        try:
            while True:
                try:
                    self.tick()
                except Exception as exc:
                    self.bridge.event("playwright.tick_error", "error", message=str(exc)[:1000])
                    # A page can close between lookup and observation. Keep the
                    # mechanical watchdog alive; reconnect only if CDP died.
                    if not self.ui.browser or not self.ui.browser.is_connected():
                        self.close()
                        try:
                            self.connect()
                        except Exception as reconnect_error:
                            self.bridge.event("playwright.reconnect_error", "error", message=str(reconnect_error)[:1000])
                time.sleep(1.0)
        finally:
            self.close()
