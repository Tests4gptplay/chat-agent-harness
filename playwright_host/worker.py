from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from .bridge import BridgeClient, BridgeError
from .mcp import PlaywrightMCP, MCPCallPending
from harness.tool_runtime import ToolRegistry
from .state import HostState
from .ui import ChatGPTUI, tool_call_blocks, syscall_blocks


WORKER_WATCHDOG_SECONDS = 30 * 60


from harness.worker_lineage_compat import (
    NO_TASK_LINEAGE,
    WORKER_LINEAGES_KEY,
    WORKER_REF_PREFIX,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wake_marker(wake: dict[str, Any], project_id: str) -> str:
    project = str(wake.get("project_id") or project_id).replace(" ", "_")
    return f"GAH_WAKE v=1 id={wake['wake_id']} project={project}"


def worker_owner(wake: dict[str, Any]) -> tuple[str, int]:
    task_id = str(wake.get("owner_task_id") or wake.get("task_id") or "cah-control").strip()
    epoch = int(wake.get("owner_control_epoch") or 1)
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,128}", task_id) or epoch < 1:
        raise RuntimeError("Wake Worker owner identity is invalid")
    return task_id, epoch


def wake_text(wake: dict[str, Any], project_id: str) -> str:
    repo = str(wake.get("repo") or "__CAH_REPOSITORY__")
    lines = [
        wake_marker(wake, project_id),
        "WORKER_BUDGET",
        "healthy_limit=10:00",
        "on_reach=HEALTHY_LIMIT",
        "instruction=prepare_handoff",
        f"Act as Worker. Use GitHub connector repository {repo}, branch {wake.get('git_branch') or 'main'} for ALL reads/writes; never default to another ref. "
        "read AGENTS.md and docs/task-cell/WORKER.md, then the Task and Child Reply below. "
        "Do the actual work now using the available authorized tools. Write progress, artifacts and "
        "continuation details directly to the bound CAH files; do not repeat them in chat. "
        "First fetch the named Task and Reply using GitHub tools. Never request semantic completion "
        "until the required current Reply entry and Result writes have succeeded. "
        "Write the current Result LAST and include turn_signal=continue, complete, blocked, or error to match the semantic outcome; "
        "preserve all pre-bound machine fields. Harness maps turn_signal to internal status after canonical verification. "
        "If the tools or writes fail, use turn_signal=blocked or error, never success. "
        "Treat about 10 minutes as this Worker generation's healthy work budget. Around that limit, stop starting new substantial steps and prepare handoff: persist completed work, exact artifact/evidence refs, any in-flight external operations with their exact run/job refs and current status, remaining work, and the next useful action, then use turn_signal=continue in the final Result if the Child remains incomplete. "
        "An independently running Cook, Runner job, render, workflow, or similar external operation is not a reason to keep this Worker merely to watch it; record it so the successor can inspect the same operation. "
        "After the durable Result write succeeds, stop. "
        "Harness observes the bound final Git write directly; no chat ending or syscall is required. "
        "Harness owns the metadata below; do not echo it, increment it, or send a readiness reply.",
    ]
    task_id = str(wake.get("task_id") or "")
    task_ref = f"tasks/{task_id}.json" if task_id else ""
    dispatch_fields = [
        wake.get("backend_cl"),
        wake.get("dispatch_id"),
        wake.get("dispatch_generation"),
        wake.get("fence_token"),
    ]
    if all(value is not None and str(value) != "" for value in dispatch_fields):
        lines.append(
            f"GAH_DISPATCH task_id={task_id} backend_cl={wake['backend_cl']} "
            f"dispatch_id={wake['dispatch_id']} generation={int(wake['dispatch_generation'])} "
            f"fence_token={wake['fence_token']}"
        )
        if task_id:
            lines.append(f"GAH_TASK ref={task_ref} read_before_semantic_work=true")
        owner_task_id, owner_epoch = worker_owner(wake)
        lines.append(f"GAH_OWNER owner_task_id={owner_task_id} owner_control_epoch={owner_epoch}")
        lines.append(
            "GAH_RUNTIME_ACK mode=response-start do_not_write_standalone_ack=true; "
            "the runtime records RUNNING after this response begins. Do the semantic work "
            "in this same response."
        )
    if wake.get("kind"):
        lines.append(f"GAH_EVENT kind={wake['kind']}")
    if wake.get("child_reply_ref"):
        lines.append(f"GAH_CHILD_REPLY ref={wake['child_reply_ref']} read_before_semantic_work=true")
    if wake.get("worker_reply_entry_ref"):
        lines.append(
            f"GAH_REPLY_ENTRY ref={wake['worker_reply_entry_ref']} "
            "write_semantic_work_before_result=true"
        )
    if wake.get("handoff_packet_ref"):
        lines.append(f"GAH_HANDOFF packet={wake['handoff_packet_ref']} read_before_semantic_work=true")
    if wake.get("result_ref") and str(wake["result_ref"]) != task_ref:
        lines.append(f"GAH_RESULT ref={wake['result_ref']}")
    if wake.get("attachment_ref"):
        lines.append(f"GAH_ATTACHMENT ref={wake['attachment_ref']}")
    return "\n".join(lines)


class WorkerManager:
    def __init__(self, bridge: BridgeClient, state: HostState, ui: ChatGPTUI, project_id: str,
                 *, asynchronous_observation: bool = False):
        self.bridge = bridge
        self.state = state
        self.ui = ui
        self.project_id = project_id
        self.mcp = PlaywrightMCP(ui.cdp_url)
        self.tool_registry = ToolRegistry()
        self._seen_tool_calls: set[str] = set()
        self._seen_syscalls: set[str] = set()
        self._response_end_seen: set[str] = set()
        self._asynchronous_observation = asynchronous_observation
        self._worker_observations: dict[str, dict[str, Any]] = {}
        self._known_dispatches: dict[str, dict[str, Any]] = {}
        self._running_dispatches: set[str] = set()

    @staticmethod
    def _observation_key(lane: dict[str, Any], lineage: dict[str, Any],
                         current: dict[str, Any], ctx: dict[str, Any] | None = None) -> str:
        # Bind the read to both canonical dispatch authority and the exact
        # physical conversation. Replaced generations cannot consume old reads.
        return json.dumps([
            lane.get("lane_id"), lane.get("project_key"), lineage.get("owner_task_id"),
            lineage.get("owner_control_epoch"), current.get("conversation_id"),
            current.get("handoff_id"), current.get("wake_id"),
            *[str((ctx or {}).get(key) or "") for key in
              ("task_id", "backend_cl", "dispatch_id", "dispatch_generation", "fence_token")],
        ], separators=(",", ":"))

    def observation_requests(self) -> list[dict[str, Any]]:
        """Capture owner-thread bindings for the single Git polling worker."""
        rows = []
        live_keys = set()
        for lane_id, lane in list((self.state.data.get("lanes") or {}).items()):
            if not isinstance(lane, dict) or not lane.get("enabled", True) or lane.get("pending_remove"):
                continue
            for lineage in (lane.get(WORKER_LINEAGES_KEY) or {}).values():
                current = lineage.get("current") or {}
                if not current.get("handoff_verified") or current.get("final_signal"):
                    continue
                base_key = self._observation_key(lane, lineage, current)
                live_keys.add(base_key)
                wd = current.get("worker_watchdog") or {}
                ctx = self._known_dispatches.get(base_key)
                # A retained conversation can advance dispatches while its
                # creation-time watchdog still describes the first dispatch.
                if ctx is None and wd.get("child_task_id"):
                    ctx = {"task_id": wd["child_task_id"], **{key: wd.get(key) for key in
                        ("backend_cl", "dispatch_id", "dispatch_generation", "fence_token")}}
                identity = self._observation_key(lane, lineage, current, ctx)
                row = {"identity_key": identity, "takeover": {
                    "handoff_id": current.get("handoff_id"), "lane_id": lane_id,
                    "worker_project_key": lane["project_key"],
                    "owner_task_id": lineage.get("owner_task_id"),
                    "owner_control_epoch": lineage.get("owner_control_epoch"),
                }}
                if ctx:
                    request = {**ctx, "worker_ref": current.get("handoff_id"),
                               "lane_id": lane_id, "worker_project_key": lane["project_key"]}
                    row["dispatch"] = request
                    if base_key in self._running_dispatches:
                        row["liveness"] = {**request, "response_running": True, "response_ended": False}
                rows.append(row)
        self._known_dispatches = {key: value for key, value in self._known_dispatches.items() if key in live_keys}
        self._running_dispatches.intersection_update(live_keys)
        return rows

    def replace_observations(self, rows: list[dict[str, Any]]) -> None:
        # A completed batch is usable in this owner tick only. Pending/failing
        # polling never keeps an old authority gate alive indefinitely.
        self._worker_observations = {row["identity_key"]: row for row in rows}

    def _observation(self, lane: dict[str, Any], lineage: dict[str, Any],
                     current: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        if ctx is None:
            wd = current.get("worker_watchdog") or {}
            ctx = self._known_dispatches.get(self._observation_key(lane, lineage, current))
            if ctx is None and wd.get("child_task_id"):
                ctx = {"task_id": wd["child_task_id"], **{key: wd.get(key) for key in
                    ("backend_cl", "dispatch_id", "dispatch_generation", "fence_token")}}
        return self._worker_observations.get(self._observation_key(lane, lineage, current, ctx), {})

    @staticmethod
    def _handoff_id() -> str:
        return WORKER_REF_PREFIX + str(uuid.uuid4())

    def _lane(self, lane_id: str) -> dict[str, Any]:
        lane = self.state.lane(lane_id)
        if not lane:
            raise RuntimeError(f"Unknown lane {lane_id}")
        return lane

    def create_worker(
        self,
        lane_id: str,
        owner_task_id: str,
        owner_control_epoch: int,
        wake: dict[str, Any],
        *,
        handoff_id: str | None = None,
        handoff_packet_ref: str | None = None,
    ) -> dict[str, Any]:
        lane = self._lane(lane_id)
        if not lane.get("enabled", True) and not lane.get("pending_remove"):
            raise RuntimeError(f"Lane {lane_id} is disabled")
        existing_lineage = self.state.get_lineage(
            lane_id,
            owner_task_id,
            owner_control_epoch,
            create=False,
        )
        lineage = existing_lineage or self.state.get_lineage(
            lane_id,
            owner_task_id,
            owner_control_epoch,
            create=True,
        )
        if not lineage:
            raise RuntimeError("failed to materialize Worker lineage")

        # Retrying one wake must retain one candidate identity, not create an
        # unbounded series of competing chats after a late admission failure.
        worker_ref = handoff_id or WORKER_REF_PREFIX + str(uuid.uuid5(uuid.NAMESPACE_URL, str(wake.get("wake_id"))))
        prior = deepcopy(lineage)
        dispatch_wake = deepcopy(wake)
        if handoff_packet_ref:
            dispatch_wake["handoff_packet_ref"] = handoff_packet_ref
        marker = wake_marker(dispatch_wake, self.project_id)
        text = wake_text(dispatch_wake, self.project_id)
        attachment: dict[str, Any] | None = None

        def before_submit(page: Any, body: str) -> str:
            nonlocal attachment
            if dispatch_wake.get("attachment_ref"):
                attachment = self.bridge.call(
                    "artifact_read",
                    artifact_ref=str(dispatch_wake["attachment_ref"]),
                )
                self.ui.attach_image(page, attachment)
            return body

        try:
            page, conversation_id, url = self.ui.bootstrap_conversation(
                lane["project_key"],
                lane["project_root_url"],
                text,
                marker,
                before_submit=before_submit,
            )

            if self._dispatch_aware(dispatch_wake):
                admitted = self.bridge.call(
                    "dispatch_accept",
                    task_id=str(dispatch_wake.get("task_id") or ""),
                    backend_cl=str(dispatch_wake.get("backend_cl") or ""),
                    dispatch_id=str(dispatch_wake.get("dispatch_id") or ""),
                    dispatch_generation=int(dispatch_wake.get("dispatch_generation") or 0),
                    fence_token=str(dispatch_wake.get("fence_token") or ""),
                    worker_ref=worker_ref,
                    lane_id=lane_id,
                    worker_project_key=lane["project_key"],
                )
                if admitted.get("ok") is False:
                    raise RuntimeError(str(admitted.get("error") or "dispatch admission failed"))

            retry_meta = (
                dispatch_wake.get("_same_generation_retry")
                if isinstance(dispatch_wake.get("_same_generation_retry"), dict)
                else {}
            )
            retry_old_conversation_id = str(retry_meta.get("old_conversation_id") or "")
            managed = [
                item for item in (prior.get("managed") or [])
                if str(item.get("conversation_id") or "") != conversation_id
                and (
                    not retry_old_conversation_id
                    or str(item.get("conversation_id") or "") != retry_old_conversation_id
                )
            ]
            watchdog_started = datetime.now(timezone.utc)
            watchdog = None
            if self._dispatch_aware(dispatch_wake):
                watchdog = {
                    "kind": "WORKER_HELPER",
                    "state": "ARMED",
                    "timeout_seconds": WORKER_WATCHDOG_SECONDS,
                    "started_at": watchdog_started.isoformat(timespec="seconds"),
                    "deadline_at": (
                        watchdog_started + timedelta(seconds=WORKER_WATCHDOG_SECONDS)
                    ).isoformat(timespec="seconds"),
                    "child_task_id": str(dispatch_wake.get("task_id") or ""),
                    "backend_cl": str(dispatch_wake.get("backend_cl") or ""),
                    "dispatch_id": str(dispatch_wake.get("dispatch_id") or ""),
                    "dispatch_generation": int(dispatch_wake.get("dispatch_generation") or 0),
                    "fence_token": str(dispatch_wake.get("fence_token") or ""),
                    "wake_id": str(dispatch_wake.get("wake_id") or ""),
                }
            promoted = {
                "conversation_id": conversation_id,
                "url": url,
                "lane_id": lane_id,
                "owner_task_id": owner_task_id,
                "owner_control_epoch": owner_control_epoch,
                "created_at": now(),
                "status": "current",
                "handoff_verified": True,
                "handoff_id": worker_ref,
                "wake_id": dispatch_wake.get("wake_id"),
                "verified_at": now(),
                "worker_watchdog": watchdog,
                "same_generation_retry": deepcopy(retry_meta) if retry_meta else None,
            }
            next_managed = [
                ({**item, "status": "standby"} if item.get("status") == "current" else item)
                for item in managed
            ]
            next_managed.append(promoted)
            if retry_meta:
                next_lineage = {
                    **prior,
                    "current": promoted,
                    "managed": next_managed,
                    "physical_retry": {
                        "request_id": str(retry_meta.get("request_id") or ""),
                        "helper_request_id": str(retry_meta.get("helper_request_id") or ""),
                        "worker_ref": worker_ref,
                        "wake_id": str(dispatch_wake.get("wake_id") or ""),
                        "dispatch_id": str(dispatch_wake.get("dispatch_id") or ""),
                        "dispatch_generation": int(dispatch_wake.get("dispatch_generation") or 0),
                        "from_conversation_id": retry_old_conversation_id,
                        "to_conversation_id": conversation_id,
                        "status": "response_started",
                        "verified_at": now(),
                    },
                }
            else:
                next_lineage = {
                    **prior,
                    "current": promoted,
                    "managed": next_managed,
                    "physical_retry": None,
                    "handoff": {
                        "handoff_id": worker_ref,
                        "status": "verified",
                        "from_conversation_id": (prior.get("current") or {}).get("conversation_id"),
                        "to_conversation_id": conversation_id,
                        "handoff_packet_ref": handoff_packet_ref,
                        "verified_at": now(),
                    },
                }
            self.state.set_lineage(lane_id, next_lineage)
        except Exception as exc:
            if existing_lineage is None:
                self.state.remove_lineage(lane_id, owner_task_id, owner_control_epoch)
            else:
                self.state.set_lineage(lane_id, prior)
            self.bridge.event(
                "worker.create_error",
                "error",
                lane_id=lane_id,
                worker_project_key=lane["project_key"],
                handoff_id=worker_ref,
                wake_id=dispatch_wake.get("wake_id"),
                message=str(exc)[:1000],
            )
            return {"ok": False, "error": str(exc), "lane_id": lane_id}

        try:
            self._trim_lineage(lane_id, owner_task_id, owner_control_epoch)
        except Exception as exc:
            self.bridge.event("worker.retention_error", "error", lane_id=lane_id,
                              owner_task_id=owner_task_id, message=str(exc)[:1000])
        self.bridge.event(
            "worker.chat_created",
            owner_task_id=owner_task_id,
            owner_control_epoch=owner_control_epoch,
            lane_id=lane_id,
            worker_project_key=lane["project_key"],
            handoff_id=worker_ref,
            conversation_id=conversation_id,
            wake_id=dispatch_wake.get("wake_id"),
            attachment_ref=attachment.get("artifact_ref") if isinstance(attachment, dict) else None,
        )
        return {
            "ok": True,
            "lane_id": lane_id,
            "handoff_id": worker_ref,
            "conversation_id": conversation_id,
            "status": "current",
            "response_started": True,
            "page_url": url,
        }

    def _trim_lineage(self, lane_id: str, owner_task_id: str, owner_control_epoch: int) -> None:
        lane = self._lane(lane_id)
        lineage = self.state.get_lineage(lane_id, owner_task_id, owner_control_epoch, create=False)
        if not lineage:
            return
        managed = list(lineage.get("managed") or [])
        while len(managed) > 5:
            current_id = str((lineage.get("current") or {}).get("conversation_id") or "")
            eligible = [
                item for item in managed
                if item.get("conversation_id") != current_id
                and item.get("status") == "standby"
                and item.get("handoff_verified") is True
            ]
            if not eligible:
                break
            eligible.sort(key=lambda item: str(item.get("created_at") or ""))
            victim = eligible[0]
            root = None
            already_absent = False
            victim_id = str(victim["conversation_id"])
            try:
                root = self.ui.project_root(lane["project_key"], lane["project_root_url"], reuse=False)
                deleted = self.ui.delete_project_conversation(
                    root,
                    lane["project_key"],
                    victim_id,
                )
                if not deleted:
                    witness_ids = [
                        str(item.get("conversation_id") or "")
                        for item in managed
                        if str(item.get("conversation_id") or "") != victim_id
                    ]
                    already_absent = self.ui.confirm_project_conversation_absent(
                        root,
                        lane["project_key"],
                        victim_id,
                        witness_conversation_ids=witness_ids,
                    )
                    if not already_absent:
                        break
            except Exception:
                break
            finally:
                try:
                    root.close()
                except Exception:
                    pass
            managed = [
                item for item in managed
                if item.get("conversation_id") != victim_id
            ]
            self.bridge.event(
                "worker.chat_retired",
                lane_id=lane_id,
                owner_task_id=owner_task_id,
                owner_control_epoch=owner_control_epoch,
                conversation_id=victim_id,
                already_absent=already_absent,
            )
            for page in self.ui.matching_pages(project_key=lane["project_key"], conversation_id=str(victim["conversation_id"])):
                page.close()
        lineage["managed"] = managed
        self.state.set_lineage(lane_id, lineage)

    def maybe_auto_rollover(
        self,
        lane_id: str,
        owner_task_id: str,
        owner_control_epoch: int,
        *,
        fresh: bool = False,
    ) -> dict[str, Any]:
        lane = self._lane(lane_id)
        lineage = self.state.get_lineage(lane_id, owner_task_id, owner_control_epoch, create=False)
        if not lineage:
            return {"ok": True, "idle": NO_TASK_LINEAGE}
        current = lineage.get("current")
        if (
            not isinstance(current, dict)
            or current.get("status") != "current"
            or current.get("handoff_verified") is not True
            or not str(current.get("handoff_id") or "")
        ):
            return {"ok": True, "idle": "no_verified_current_worker"}

        current_ref = str(current.get("handoff_id") or "")
        if getattr(self, "_asynchronous_observation", False) and not fresh:
            lifecycle = self._observation(lane, lineage, current).get("takeover") or {}
        else:
            lifecycle = self.bridge.call(
                "worker_takeover_status", handoff_id=current_ref, lane_id=lane_id,
                worker_project_key=lane["project_key"], owner_task_id=owner_task_id,
                owner_control_epoch=owner_control_epoch,
            )
        requested = (
            lifecycle.get("rollover_requested") is True
            and str(lifecycle.get("rollover_request_handoff_id") or "") == current_ref
            and str(lifecycle.get("rollover_reason") or "") == "context_compacted"
        )
        if not requested:
            return {"ok": True, "idle": "no_rollover_request"}

        packet_ref = str(lifecycle.get("handoff_packet_ref") or "")
        successor_ref = self._handoff_id()
        self.bridge.event(
            "worker.compaction_rollover_triggered",
            lane_id=lane_id,
            owner_task_id=owner_task_id,
            owner_control_epoch=owner_control_epoch,
            handoff_id=current_ref,
            conversation_id=current.get("conversation_id"),
            reason=str(lifecycle.get("rollover_reason") or ""),
        )
        handoff_result = self.bridge.call(
            "worker_handoff_complete",
            lane_id=lane_id,
            worker_project_key=lane["project_key"],
            successor_worker_ref=successor_ref,
            handoff_packet_ref=packet_ref,
        )
        wake = handoff_result.get("wake")
        if not isinstance(wake, dict):
            raise RuntimeError("Worker handoff did not produce a successor wake")
        return self.create_worker(
            lane_id,
            owner_task_id,
            owner_control_epoch,
            wake,
            handoff_id=successor_ref,
            handoff_packet_ref=packet_ref,
        )

    def _defer_if_current_is_outgoing(
        self,
        lane: dict[str, Any],
        lineage: dict[str, Any],
        message_id: str,
        wake_id: str,
    ) -> dict[str, Any] | None:
        current = lineage.get("current") if isinstance(lineage, dict) else None
        if not isinstance(current, dict) or not current.get("handoff_verified"):
            return None
        worker_ref = str(current.get("handoff_id") or "")
        if not worker_ref:
            return None
        lifecycle = self.bridge.call(
            "worker_takeover_status",
            handoff_id=worker_ref,
            lane_id=lane["lane_id"],
            worker_project_key=lane["project_key"],
            owner_task_id=str(lineage.get("owner_task_id") or ""),
            owner_control_epoch=int(lineage.get("owner_control_epoch") or 0),
        )
        outgoing = (
            lifecycle.get("rollover_requested") is True
            and str(lifecycle.get("rollover_request_handoff_id") or "") == worker_ref
            and str(lifecycle.get("rollover_reason") or "") == "context_compacted"
        )
        if not outgoing:
            return None

        action = self.maybe_auto_rollover(
            lane["lane_id"],
            str(lineage.get("owner_task_id") or ""),
            int(lineage.get("owner_control_epoch") or 0),
            fresh=True,
        )
        self.bridge.call("release", message_id=message_id)
        return {
            "ok": bool(action.get("ok")),
            "deferred_for_handoff": True,
            "wake_id": wake_id,
            "lane_id": lane["lane_id"],
            "outgoing_worker_ref": worker_ref,
            "handoff_action": action,
        }

    def _dispatch_aware(self, wake: dict[str, Any]) -> bool:
        values = [wake.get("backend_cl"), wake.get("dispatch_id"), wake.get("dispatch_generation"), wake.get("fence_token")]
        present = [value is not None and str(value) != "" for value in values]
        if any(present) and not all(present):
            raise RuntimeError("Dispatch-aware wake has incomplete scheduler identity")
        return all(present)

    @staticmethod
    def _dispatch_identity_matches(payload: dict[str, Any], ctx: dict[str, Any]) -> bool:
        # The current observed dispatch supplies omitted machine fields. An
        # explicitly conflicting legacy field still cannot replace that binding.
        return all(
            key not in payload or str(payload[key]) == str(ctx.get(key))
            for key in ("task_id", "backend_cl", "dispatch_id", "dispatch_generation", "fence_token")
        )

    def _ensure_admitted(
        self,
        ctx: dict[str, Any],
        lane: dict[str, Any],
        current: dict[str, Any],
        *,
        lineage: dict[str, Any] | None = None,
        observation: bool = False,
    ) -> dict[str, Any]:
        if observation and getattr(self, "_asynchronous_observation", False):
            status = self._observation(lane, lineage or {}, current, ctx).get("dispatch") or {}
            if status.get("matched") is None:
                return {"ok": True, "matched": None, "idle": "git_snapshot_unavailable"}
        else:
            status = self.bridge.call(
                "dispatch_status", task_id=str(ctx.get("task_id") or ""),
                backend_cl=str(ctx.get("backend_cl") or ""), dispatch_id=str(ctx.get("dispatch_id") or ""),
                dispatch_generation=int(ctx.get("dispatch_generation") or 0),
                fence_token=str(ctx.get("fence_token") or ""),
            )
        if not status.get("matched"):
            return {"ok": True, "matched": False, "idle": "stale_dispatch"}
        if str(status.get("state") or "") in {"READY", "DISPATCHED", "ACKED"}:
            return self.bridge.call(
                "dispatch_accept",
                task_id=str(ctx.get("task_id") or ""),
                backend_cl=str(ctx.get("backend_cl") or ""),
                dispatch_id=str(ctx.get("dispatch_id") or ""),
                dispatch_generation=int(ctx.get("dispatch_generation") or 0),
                fence_token=str(ctx.get("fence_token") or ""),
                worker_ref=str(current.get("handoff_id") or ""),
                lane_id=str(lane.get("lane_id") or ""),
                worker_project_key=str(lane.get("project_key") or ""),
            )
        return status

    def _complete_same_generation_retry(
        self,
        wake: dict[str, Any],
        *,
        new_conversation_id: str,
    ) -> dict[str, Any] | None:
        retry = wake.get("_same_generation_retry")
        if not isinstance(retry, dict):
            return None
        return self.bridge.call(
            "worker_same_generation_retry_complete",
            request_id=str(retry.get("request_id") or ""),
            helper_request_id=str(retry.get("helper_request_id") or ""),
            task_id=str(wake.get("owner_task_id") or ""),
            child_task_id=str(wake.get("task_id") or ""),
            backend_cl=str(wake.get("backend_cl") or ""),
            dispatch_id=str(wake.get("dispatch_id") or ""),
            dispatch_generation=int(wake.get("dispatch_generation") or 0),
            fence_token=str(wake.get("fence_token") or ""),
            wake_id=str(wake.get("wake_id") or ""),
            worker_ref=str(retry.get("worker_ref") or ""),
            old_conversation_id=str(retry.get("old_conversation_id") or ""),
            new_conversation_id=new_conversation_id,
        )

    def poll_lane(self, lane_id: str) -> dict[str, Any]:
        lane = self._lane(lane_id)
        if not lane.get("enabled", True) or lane.get("pending_remove"):
            return {"ok": True, "idle": "lane_disabled"}

        claim = self.bridge.call("claim", lane_id=lane_id, lease_seconds=120)
        wake = claim.get("wake")
        if not isinstance(wake, dict):
            return {"ok": True, "idle": "no_wake"}
        message_id = str(claim.get("message_id") or wake.get("wake_id") or "")

        try:
            return self._route_claimed_wake(lane_id, lane, message_id, wake)
        except Exception:
            try:
                self.bridge.call("release", message_id=message_id)
            except BridgeError:
                pass
            raise

    def _route_claimed_wake(
        self,
        lane_id: str,
        lane: dict[str, Any],
        message_id: str,
        wake: dict[str, Any],
    ) -> dict[str, Any]:

        retry_meta = (
            wake.get("_same_generation_retry")
            if isinstance(wake.get("_same_generation_retry"), dict)
            else {}
        )
        owner_task_id, owner_epoch = worker_owner(wake)
        lineage = self.state.get_lineage(lane_id, owner_task_id, owner_epoch, create=False)
        current = lineage.get("current") if isinstance(lineage, dict) else None

        if retry_meta and isinstance(current, dict) and current.get("handoff_verified") is True:
            current_retry = (
                current.get("same_generation_retry")
                if isinstance(current.get("same_generation_retry"), dict)
                else {}
            )
            same_retry = (
                str(current_retry.get("request_id") or "")
                == str(retry_meta.get("request_id") or "")
                and str(current.get("handoff_id") or "")
                == str(retry_meta.get("worker_ref") or "")
                and str(current.get("wake_id") or "")
                == str(wake.get("wake_id") or "")
                and str(current.get("conversation_id") or "")
                != str(retry_meta.get("old_conversation_id") or "")
            )
            if same_retry:
                completion = self._complete_same_generation_retry(
                    wake,
                    new_conversation_id=str(current.get("conversation_id") or ""),
                )
                if not isinstance(completion, dict) or not completion.get("ok"):
                    self.bridge.call("release", message_id=message_id)
                    return {
                        "ok": False,
                        "error": "WORKER_SAME_GENERATION_RETRY_COMPLETE_FAILED",
                        "same_generation_retry": True,
                        "deduped_physical_worker": True,
                        "wake_id": wake.get("wake_id"),
                        "lane_id": lane_id,
                        "conversation_id": current.get("conversation_id"),
                        "retry_complete": completion,
                    }
                self.bridge.call("consume", message_id=message_id)
                return {
                    "ok": True,
                    "same_generation_retry": True,
                    "deduped_physical_worker": True,
                    "wake_id": wake.get("wake_id"),
                    "lane_id": lane_id,
                    "conversation_id": current.get("conversation_id"),
                    "retry_complete": completion,
                }

        if self._dispatch_aware(wake):
            status = self.bridge.call(
                "dispatch_status",
                task_id=str(wake.get("task_id") or ""),
                backend_cl=str(wake.get("backend_cl") or ""),
                dispatch_id=str(wake.get("dispatch_id") or ""),
                dispatch_generation=int(wake.get("dispatch_generation") or 0),
                fence_token=str(wake.get("fence_token") or ""),
            )
            if retry_meta:
                if not status.get("matched") or str(status.get("state") or "") != "RUNNING":
                    self.bridge.call("consume", message_id=message_id)
                    return {
                        "ok": True,
                        "suppressed": True,
                        "same_generation_retry": True,
                        "state": status.get("state"),
                    }
            elif status.get("suppress"):
                self.bridge.call("consume", message_id=message_id)
                return {"ok": True, "suppressed": True, "state": status.get("state")}


        if (
            not retry_meta
            and isinstance(lineage, dict)
            and isinstance(current, dict)
            and current.get("handoff_verified")
        ):
            deferred = self._defer_if_current_is_outgoing(
                lane, lineage, message_id, str(wake.get("wake_id") or "")
            )
            if deferred is not None:
                return deferred

        if (
            not isinstance(current, dict)
            or not current.get("handoff_verified")
            or wake.get("replace_conversation")
            or bool(retry_meta)
        ):
            if lineage and len(lineage.get("managed") or []) > 5:
                self._trim_lineage(lane_id, owner_task_id, owner_epoch)
                lineage = self.state.get_lineage(lane_id, owner_task_id, owner_epoch, create=False)
                if lineage and len(lineage.get("managed") or []) > 5:
                    self.bridge.call("release", message_id=message_id)
                    return {"ok": False, "error": "WORKER_RETENTION_DELETE_PENDING", "wake_id": wake.get("wake_id")}
            action = self.create_worker(
                lane_id,
                owner_task_id,
                owner_epoch,
                wake,
                handoff_id=(
                    str(retry_meta.get("worker_ref") or "") or None
                    if retry_meta
                    else None
                ),
                handoff_packet_ref=str(wake.get("handoff_packet_ref") or "") or None,
            )
            if action.get("ok"):
                completion = self._complete_same_generation_retry(
                    wake,
                    new_conversation_id=str(action.get("conversation_id") or ""),
                )
                if retry_meta and (
                    not isinstance(completion, dict) or not completion.get("ok")
                ):
                    self.bridge.call("release", message_id=message_id)
                    return {
                        "ok": False,
                        "error": "WORKER_SAME_GENERATION_RETRY_COMPLETE_FAILED",
                        "created_worker": True,
                        "same_generation_retry": True,
                        "wake_id": wake.get("wake_id"),
                        "create_result": action,
                        "retry_complete": completion,
                    }
                self.bridge.call("consume", message_id=message_id)
            else:
                self.bridge.call("release", message_id=message_id)
            return {
                "ok": bool(action.get("ok")),
                "created_worker": bool(action.get("ok")),
                "wake_id": wake.get("wake_id"),
                "create_result": action,
            }

        conversation_id = str(current.get("conversation_id") or "")
        try:
            page = self.ui.conversation_page(
                project_key=lane["project_key"],
                conversation_id=conversation_id,
                url=str(
                    current.get("url")
                    or f"https://chatgpt.com/g/{lane['project_key']}/c/{conversation_id}"
                ),
            )
        except Exception as exc:
            replacement = self.create_worker(
                lane_id,
                owner_task_id,
                owner_epoch,
                wake,
                handoff_packet_ref=str(wake.get("handoff_packet_ref") or "") or None,
            )
            if replacement.get("ok"):
                self.bridge.call("consume", message_id=message_id)
            else:
                self.bridge.call("release", message_id=message_id)
            self.bridge.event(
                "wake.worker_replacement_requested",
                "warn",
                lane_id=lane_id,
                worker_project_key=lane["project_key"],
                wake_id=wake.get("wake_id"),
                reason=str(exc)[:800],
                create_result=replacement,
            )
            return {
                "ok": bool(replacement.get("ok")),
                "replacement": True,
                "wake_id": wake.get("wake_id"),
                "lane_id": lane_id,
                "create_result": replacement,
            }

        marker = wake_marker(wake, self.project_id)
        observed = self.ui.marker_state(page, marker)
        if observed.get("marker_visible"):
            if not observed.get("response_started"):
                self.bridge.call("release", message_id=message_id)
                return {
                    "ok": True,
                    "pending": True,
                    "wake_id": wake.get("wake_id"),
                    "lane_id": lane_id,
                }

            if self._dispatch_aware(wake):
                observed_ctx = observed.get("dispatch_context")
                expected_ctx = {
                    "task_id": str(wake.get("task_id") or ""),
                    "backend_cl": str(wake.get("backend_cl") or ""),
                    "dispatch_id": str(wake.get("dispatch_id") or ""),
                    "dispatch_generation": int(wake.get("dispatch_generation") or 0),
                    "fence_token": str(wake.get("fence_token") or ""),
                }
                if not isinstance(observed_ctx, dict) or not self._dispatch_identity_matches(observed_ctx, expected_ctx):
                    raise RuntimeError("DISPATCH_STALE_OBSERVED_MARKER_CONTEXT")
                self.bridge.call(
                    "dispatch_accept",
                    task_id=expected_ctx["task_id"],
                    backend_cl=expected_ctx["backend_cl"],
                    dispatch_id=expected_ctx["dispatch_id"],
                    dispatch_generation=expected_ctx["dispatch_generation"],
                    fence_token=expected_ctx["fence_token"],
                    worker_ref=str(current.get("handoff_id") or ""),
                    lane_id=lane_id,
                    worker_project_key=lane["project_key"],
                )

            self._complete_same_generation_retry(
                wake,
                new_conversation_id=conversation_id,
            )
            self.bridge.call("consume", message_id=message_id)
            return {
                "ok": True,
                "deduped": True,
                "wake_id": wake.get("wake_id"),
                "lane_id": lane_id,
                "response_started": True,
            }

        if self.ui.response_running(page):
            self.bridge.call("release", message_id=message_id)
            return {"ok": True, "idle": "worker_response_running", "wake_id": wake.get("wake_id")}

        attachment = None
        if wake.get("attachment_ref"):
            attachment = self.bridge.call(
                "artifact_read",
                artifact_ref=str(wake["attachment_ref"]),
            )
            self.ui.attach_image(page, attachment)

        result = self.ui.submit_text(
            page,
            wake_text(wake, self.project_id),
            marker=marker,
            wait_response_start=True,
        )
        if not result.get("response_started"):
            self.bridge.call("release", message_id=message_id)
            raise RuntimeError("Worker wake submitted but assistant response did not start")

        if self._dispatch_aware(wake):
            self.bridge.call(
                "dispatch_accept",
                task_id=str(wake.get("task_id") or ""),
                backend_cl=str(wake.get("backend_cl") or ""),
                dispatch_id=str(wake.get("dispatch_id") or ""),
                dispatch_generation=int(wake.get("dispatch_generation") or 0),
                fence_token=str(wake.get("fence_token") or ""),
                worker_ref=str(current.get("handoff_id") or ""),
                lane_id=lane_id,
                worker_project_key=lane["project_key"],
            )
        self._complete_same_generation_retry(
            wake,
            new_conversation_id=conversation_id,
        )
        self.bridge.call("consume", message_id=message_id)
        self.bridge.event(
            "wake.submitted",
            wake_id=wake.get("wake_id"),
            lane_id=lane_id,
            worker_project_key=lane["project_key"],
            conversation_id=conversation_id,
            attachment_ref=attachment.get("artifact_ref") if isinstance(attachment, dict) else None,
        )
        return {
            "ok": True,
            "wake_id": wake.get("wake_id"),
            "lane_id": lane_id,
            "response_started": True,
        }

    def _resolve_browser_tool_target(self, call: dict[str, Any], source_page: Any) -> Any:
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        target = args.get("target") if isinstance(args.get("target"), dict) else {}
        if target.get("self") is True:
            return source_page
        if target.get("lane_id"):
            raise RuntimeError("CAH_BROWSER_TOOL_LANE_TARGET_NOT_MIGRATED")
        target_url = str(target.get("url") or "")
        if target_url:
            matches = [
                page
                for page in self.ui.pages()
                if page.url == target_url
            ]
        else:
            matches = self.ui.matching_pages(
                project_key=str(target.get("project_key") or "") or None,
                conversation_id=str(target.get("conversation_id") or "") or None,
            )
        if len(matches) != 1:
            raise RuntimeError(
                "CAH_BROWSER_TOOL_TARGET_AMBIGUOUS"
                if matches else "CAH_BROWSER_TOOL_TARGET_NOT_FOUND"
            )
        return matches[0]

    def _browser_tool_key(self, call: dict, source_page: Any) -> str:
        # Python Page identity stays stable even if a generic tool navigates it.
        return f"{id(source_page)}|{call.get('dispatch_id', '')}|{call.get('dispatch_generation', '')}|{call.get('call_id')}"

    def _execute_browser_tool(self, call: dict[str, Any], source_page: Any, *, role: str = "worker") -> Any:
        tool = str(call.get("tool") or "")
        meta = self.tool_registry.get(tool)
        if not meta or role not in meta["roles"]:
            raise RuntimeError("CAH_TOOL_NOT_AVAILABLE_FOR_ROLE")
        args = call.get("args") or {}
        if meta["provider"] == "playwright_mcp":
            key = self._browser_tool_key(call, source_page)
            target_url = None
            if key not in self.mcp._pending and args.get("target"):
                target_url = self._resolve_browser_tool_target(call, source_page).url
            return self.mcp.execute(key, call, target_url)
        if tool == "browser.read_conversation":
            page = self._resolve_browser_tool_target(call, source_page)
            return self.ui.read_conversation(page, int(args.get("last_n") or 8))
        raise RuntimeError("CAH_TOOL_NOT_ACTIVE")

    def _release_browser_tool(self, call: dict, source_page: Any) -> None:
        self.mcp._pending.pop(self._browser_tool_key(call, source_page), None)

    @staticmethod
    def _tool_result_marker(call_id: str, tool: str, status: str) -> str:
        return f"CAH_TOOL_RESULT v=1 call_id={call_id} tool={tool} status={status}"

    def _submit_tool_result(
        self,
        page: Any,
        call: dict[str, Any],
        status: str,
        result: Any,
        error: str | None,
        *,
        context_line: str | None = None,
    ) -> None:
        call_id = str(call.get("call_id") or "")
        tool = str(call.get("tool") or "")
        # Attach actual MCP images, never paste base64 into the semantic prompt.
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            result = deepcopy(result)
            for index, item in enumerate(result["content"]):
                if item.get("type") == "image" and item.get("data"):
                    mime = item.get("mimeType", "image/png")
                    filename = f"mcp-{call_id}-{index}." + mime.split("/")[-1]
                    self.ui.attach_image(page, {"file_name": filename, "mime_type": mime, "base64": item.pop("data")})
                    item["attached_file"] = filename
        payload = {
            "v": 1,
            "call_id": call_id,
            "tool": tool,
            "status": status,
            "result": result,
            "error": error[:1200] if error else None,
        }
        marker = self._tool_result_marker(call_id, tool, status)
        continuation = context_line or (
            "CAH_TOOL_RESULT_CONTEXT same_dispatch=true; continue the existing task from this ephemeral "
            "tool result. This is runtime data, not a new user-authored task."
        )
        text = marker + "\n" + json.dumps(payload, ensure_ascii=False) + "\n" + continuation
        submitted = self.ui.submit_text(
            page,
            text,
            marker=marker,
            wait_response_start=True,
        )
        if not submitted.get("response_started"):
            raise RuntimeError("CAH Tool result was submitted but assistant response did not start")

    def process_worker_page(
        self,
        lane_id: str,
        owner_task_id: str,
        owner_control_epoch: int,
    ) -> dict[str, Any]:
        lane = self._lane(lane_id)
        lineage = self.state.get_lineage(lane_id, owner_task_id, owner_control_epoch, create=False)
        current = lineage.get("current") if isinstance(lineage, dict) else None
        if not isinstance(current, dict) or not current.get("handoff_verified"):
            return {"ok": True, "idle": "no_current"}
        if current.get("final_signal"):
            return {"ok": True, "idle": "git_turn_consumed"}
        page = self.ui.find_page(
            project_key=lane["project_key"],
            conversation_id=str(current.get("conversation_id") or ""),
        )
        if page is None:
            return {"ok": True, "idle": "current_page_absent"}

        turn = self.ui.dispatch_turn_state(page)
        if not turn:
            # ChatGPT virtualizes older messages. When the original GAH_DISPATCH
            # user message falls out of the loaded DOM, the latest in-band
            # CAH_TOOL_RESULT is still a trustworthy continuation delimiter
            # ONLY for this already-admitted, exactly bound Worker conversation.
            watchdog = current.get("worker_watchdog") or {}
            if (
                isinstance(watchdog, dict)
                and watchdog.get("state") in {"ARMED", "WORKER_HELPER_REQUESTED"}
                and str(watchdog.get("wake_id") or "") == str(current.get("wake_id") or "")
                and str(watchdog.get("child_task_id") or "")
                and str(watchdog.get("backend_cl") or "")
                and str(watchdog.get("dispatch_id") or "")
                and int(watchdog.get("dispatch_generation") or 0) > 0
                and str(watchdog.get("fence_token") or "")
            ):
                continuation_ctx = None
                if getattr(self, "_asynchronous_observation", False):
                    continuation_ctx = self._known_dispatches.get(self._observation_key(lane, lineage, current))
                if continuation_ctx is None:
                    continuation_ctx = {
                        "task_id": str(watchdog["child_task_id"]),
                        "backend_cl": str(watchdog["backend_cl"]),
                        "dispatch_id": str(watchdog["dispatch_id"]),
                        "dispatch_generation": int(watchdog["dispatch_generation"]),
                        "fence_token": str(watchdog["fence_token"]),
                    }
                nodes = self.ui.message_nodes(page)
                for index in range(nodes.count() - 1, -1, -1):
                    node = nodes.nth(index)
                    if self.ui.message_role(node) != "user":
                        continue
                    message = self.ui.message_text(node, "user").lstrip()
                    if message.startswith("CAH_TOOL_RESULT v=1 "):
                        continuation = self.ui._turn_after(page, nodes, index)
                        if continuation.get("response_started") and not continuation.get("superseded"):
                            turn = {
                                "dispatch_context": continuation_ctx,
                                **continuation,
                            }
                    # A different latest user message is not an authorized
                    # continuation of the known dispatch.
                    break
        if not turn:
            return {"ok": True, "idle": "no_dispatch_context"}
        if not turn.get("response_started"):
            return {"ok": True, "idle": "response_not_started"}

        ctx = turn["dispatch_context"]
        if getattr(self, "_asynchronous_observation", False):
            base_key = self._observation_key(lane, lineage, current)
            self._known_dispatches[base_key] = dict(ctx)
            if turn.get("response_ended"):
                self._running_dispatches.discard(base_key)
            else:
                self._running_dispatches.add(base_key)
        admission = self._ensure_admitted(ctx, lane, current, lineage=lineage, observation=True)
        if admission.get("matched") is None:
            return {"ok": True, "idle": "git_snapshot_unavailable"}
        if admission.get("matched") is False or turn.get("superseded"):
            return {"ok": True, "idle": "stale_dispatch"}

        texts = turn.get("assistant_texts") or []
        if turn.get("response_ended"):
            for text in reversed(texts):
                for call in tool_call_blocks(text):
                    call_id = str(call.get("call_id") or "")
                    if (
                        call.get("v") != 1
                        or not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", call_id)
                        or not self._dispatch_identity_matches(call, ctx)
                    ):
                        continue
                    key = f"{ctx['dispatch_id']}|{ctx['dispatch_generation']}|{call_id}"
                    if key in self._seen_tool_calls:
                        continue

                    # Preserve DOM-visible Tool-result dedupe.
                    visible_result = False
                    for status in ("PASS", "ERROR"):
                        marker = self._tool_result_marker(call_id, str(call.get("tool") or ""), status)
                        try:
                            if self.ui.marker_state(page, marker).get("marker_visible"):
                                visible_result = True
                                break
                        except Exception:
                            pass
                    if visible_result:
                        self._seen_tool_calls.add(key)
                        return {"ok": True, "tool_call": call_id, "deduped": True}

                    tool_status = "PASS"
                    tool_result = None
                    tool_error = None
                    try:
                        tool_result = self._execute_browser_tool(call, page)
                        if isinstance(tool_result, dict) and tool_result.get("isError"):
                            tool_status = "ERROR"
                            tool_error = "Official Playwright MCP returned isError; inspect result content"
                    except MCPCallPending:
                        return {"ok": True, "tool_call": call_id, "pending": True}
                    except Exception as exc:
                        tool_status = "ERROR"
                        tool_error = str(exc)
                    self._submit_tool_result(
                        page,
                        call,
                        tool_status,
                        tool_result,
                        tool_error,
                    )
                    self._release_browser_tool(call, page)
                    self._seen_tool_calls.add(key)
                    return {"ok": True, "tool_call": call_id, "deduped": False}

            for text in reversed(texts):
                for syscall in syscall_blocks(text):
                    if syscall.get("v") != 1 or syscall.get("kind") != "action_submit":
                        continue
                    action = syscall.get("action") if isinstance(syscall.get("action"), dict) else None
                    if action is None:
                        continue
                    syscall_identity = {key: syscall[key] for key in ctx if key in syscall}
                    if not self._dispatch_identity_matches(syscall_identity, ctx):
                        continue
                    action_id = str(action.get("action_id") or "")
                    if not action_id:
                        continue
                    key = "|".join([
                        str(syscall.get("task_id") or ""),
                        str(syscall.get("dispatch_id") or ""),
                        str(syscall.get("dispatch_generation") or ""),
                        action_id,
                    ])
                    if key in self._seen_syscalls:
                        continue
                    self.bridge.call(
                        "action_submit",
                        task_id=str(ctx["task_id"]),
                        backend_cl=str(ctx["backend_cl"]),
                        foreground_cl=str(syscall.get("foreground_cl") or action.get("foreground_cl") or ""),
                        dispatch_id=str(ctx["dispatch_id"]),
                        dispatch_generation=int(ctx["dispatch_generation"]),
                        fence_token=str(ctx["fence_token"]),
                        worker_ref=str(current.get("handoff_id") or ""),
                        lane_id=lane_id,
                        worker_project_key=lane["project_key"],
                        action=action,
                    )
                    self._seen_syscalls.add(key)
                    return {"ok": True, "syscall": action_id}

            liveness_key = "|".join([
                str(ctx["backend_cl"]),
                str(ctx["dispatch_id"]),
                str(ctx["dispatch_generation"]),
                str(ctx["fence_token"]),
                str(current.get("handoff_id") or ""),
            ])
            if liveness_key not in self._response_end_seen:
                result = self.bridge.call(
                    "dispatch_liveness",
                    task_id=str(ctx["task_id"]),
                    backend_cl=str(ctx["backend_cl"]),
                    dispatch_id=str(ctx["dispatch_id"]),
                    dispatch_generation=int(ctx["dispatch_generation"]),
                    fence_token=str(ctx["fence_token"]),
                    worker_ref=str(current.get("handoff_id") or ""),
                    lane_id=lane_id,
                    worker_project_key=lane["project_key"],
                    response_running=False,
                    response_ended=True,
                    observed_at=now(),
                )
                self._response_end_seen.add(liveness_key)
                return {"ok": True, "response_ended": True, "liveness": result}
        return {
            "ok": True,
            "response_running": not bool(turn.get("response_ended")),
        }

    def observe(self) -> list[dict[str, Any]]:
        out = []
        for lane_id, lane in list((self.state.data.get("lanes") or {}).items()):
            if not isinstance(lane, dict):
                continue
            for lineage in list((lane.get(WORKER_LINEAGES_KEY) or {}).values()):
                if not isinstance(lineage, dict):
                    continue
                task_id = str(lineage.get("owner_task_id") or "")
                epoch = int(lineage.get("owner_control_epoch") or 0)
                if task_id and epoch > 0:
                    try:
                        out.append(self.process_worker_page(lane_id, task_id, epoch))
                    except Exception as exc:
                        self.bridge.event("worker.observe_error", "error", lane_id=lane_id,
                                          task_id=task_id, control_epoch=epoch,
                                          exception_type=type(exc).__name__, message=str(exc)[:1000])
        return out

    @staticmethod
    def _watchdog_deadline(value: Any) -> datetime | None:
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

    def _set_worker_watchdog_state(
        self,
        lane_id: str,
        lineage: dict[str, Any],
        current: dict[str, Any],
        watchdog: dict[str, Any],
    ) -> None:
        conversation_id = str(current.get("conversation_id") or "")
        updated_current = {**current, "worker_watchdog": deepcopy(watchdog)}
        managed = []
        for item in list(lineage.get("managed") or []):
            if (
                isinstance(item, dict)
                and str(item.get("conversation_id") or "") == conversation_id
            ):
                managed.append({**item, "worker_watchdog": deepcopy(watchdog)})
            else:
                managed.append(item)
        next_lineage = {**lineage, "current": updated_current, "managed": managed}
        self.state.set_lineage(lane_id, next_lineage)

    def restore_after_helper(self, request_id: str, recovery: dict[str, Any]) -> None:
        """Post-accept bookkeeping; this is not an additional Helper exit gate."""
        lane_id = str(recovery.get("lane_id") or "")
        lineage = self.state.get_lineage(lane_id, str(recovery.get("parent_task_id") or ""),
                                   int(recovery.get("parent_control_epoch") or 0), create=False)
        current = lineage.get("current") if isinstance(lineage, dict) else None
        if not isinstance(current, dict):
            return
        watchdog = current.get("worker_watchdog") or {}
        if (watchdog.get("state") != "WORKER_HELPER_REQUESTED"
                or watchdog.get("helper_request_id") != request_id
                or current.get("conversation_id") != recovery.get("old_conversation_id")
                or current.get("handoff_id") != recovery.get("worker_ref")
                or current.get("wake_id") != recovery.get("wake_id")):
            return
        started = self._watchdog_deadline(recovery.get("resumed_at")) or datetime.now(timezone.utc)
        self._set_worker_watchdog_state(lane_id, lineage, current, {
            **watchdog, "state": recovery["watchdog_state"],
            "started_at": started.isoformat(timespec="seconds"),
            "deadline_at": (started + timedelta(seconds=WORKER_WATCHDOG_SECONDS)).isoformat(timespec="seconds"),
            "helper_request_id": None, "helper_output_ref": None,
        })

    def finish_git_turn(self, lane_id: str, lineage: dict[str, Any],
                        current: dict[str, Any], signal: str) -> None:
        """Local projection of an already accepted canonical result."""
        completed = {**current, "final_signal": signal,
                     "worker_watchdog": {**(current.get("worker_watchdog") or {}), "state": "DISARMED"}}
        self.state.set_lineage(lane_id, {**lineage, "current": completed,
            "managed": [completed if item.get("conversation_id") == current.get("conversation_id") else item
                        for item in lineage.get("managed") or []]})

    def liveness_sweep(self) -> list[dict[str, Any]]:
        out = []
        for lane_id, lane in list((self.state.data.get("lanes") or {}).items()):
            if not isinstance(lane, dict) or not lane.get("enabled", True) or lane.get("pending_remove"):
                continue
            for lineage in list((lane.get(WORKER_LINEAGES_KEY) or {}).values()):
                if not isinstance(lineage, dict):
                    continue
                current = lineage.get("current")
                if not isinstance(current, dict) or current.get("status") != "current" or current.get("handoff_verified") is not True or current.get("final_signal"):
                    continue

                watchdog = current.get("worker_watchdog")
                if isinstance(watchdog, dict) and str(watchdog.get("state") or "") == "ARMED":
                    deadline = self._watchdog_deadline(watchdog.get("deadline_at"))
                    if deadline is not None and datetime.now(timezone.utc) >= deadline:
                        result = self.bridge.call(
                            "worker_watchdog_expired",
                            parent_task_id=str(lineage.get("owner_task_id") or ""),
                            parent_control_epoch=int(lineage.get("owner_control_epoch") or 0),
                            child_task_id=str(watchdog.get("child_task_id") or ""),
                            backend_cl=str(watchdog.get("backend_cl") or ""),
                            dispatch_id=str(watchdog.get("dispatch_id") or ""),
                            dispatch_generation=int(watchdog.get("dispatch_generation") or 0),
                            fence_token=str(watchdog.get("fence_token") or ""),
                            worker_ref=str(current.get("handoff_id") or ""),
                            lane_id=lane_id,
                            worker_project_key=lane["project_key"],
                            conversation_id=str(current.get("conversation_id") or ""),
                            wake_id=str(watchdog.get("wake_id") or current.get("wake_id") or ""),
                            watchdog_started_at=str(watchdog.get("started_at") or ""),
                            reason="worker_watchdog_30m",
                            observed_at=now(),
                        )
                        watchdog_result = {
                            "ok": bool(result.get("ok")),
                            "worker_watchdog": True,
                            "conversation_id": str(current.get("conversation_id") or ""),
                            "wake_id": str(watchdog.get("wake_id") or ""),
                            "result": result,
                        }
                        out.append(watchdog_result)
                        if result.get("ok"):
                            next_watchdog = {
                                **watchdog,
                                "state": (
                                    "WORKER_HELPER_REQUESTED"
                                    if result.get("staged") or result.get("duplicate")
                                    else "DISARMED"
                                ),
                                "expired_at": now(),
                                "helper_request_id": result.get("helper_request_id"),
                                "helper_output_ref": result.get("helper_output_ref"),
                                "terminal_reason": result.get("idle"),
                            }
                            self._set_worker_watchdog_state(
                                lane_id, lineage, current, next_watchdog
                            )
                            continue

                conversation_id = str(current.get("conversation_id") or "")
                page = self.ui.conversation_page(
                    project_key=lane["project_key"],
                    conversation_id=conversation_id,
                    url=str(
                        current.get("url")
                        or f"https://chatgpt.com/g/{lane['project_key']}/c/{conversation_id}"
                    ),
                )

                turn = self.ui.dispatch_turn_state(page)
                if not turn or not turn.get("response_started"):
                    continue
                ctx = turn["dispatch_context"]
                admission = self._ensure_admitted(ctx, lane, current, lineage=lineage, observation=True)
                if getattr(self, "_asynchronous_observation", False):
                    if admission.get("matched") is not True:
                        continue
                    result = self._observation(lane, lineage, current, ctx).get("liveness")
                    if result is not None:
                        out.append(result)
                    continue
                result = self.bridge.call(
                    "dispatch_liveness",
                    task_id=str(ctx["task_id"]),
                    backend_cl=str(ctx["backend_cl"]),
                    dispatch_id=str(ctx["dispatch_id"]),
                    dispatch_generation=int(ctx["dispatch_generation"]),
                    fence_token=str(ctx["fence_token"]),
                    worker_ref=str(current.get("handoff_id") or ""),
                    lane_id=lane_id,
                    worker_project_key=lane["project_key"],
                    response_running=not bool(turn.get("response_ended")),
                    response_ended=False,
                )
                out.append(result)
        return out

    def maintain(self) -> list[dict[str, Any]]:
        out = []
        for lane_id, lane in list((self.state.data.get("lanes") or {}).items()):
            if not isinstance(lane, dict):
                continue
            for lineage in list((lane.get(WORKER_LINEAGES_KEY) or {}).values()):
                if not isinstance(lineage, dict):
                    continue
                task_id = str(lineage.get("owner_task_id") or "")
                epoch = int(lineage.get("owner_control_epoch") or 0)
                if not task_id or epoch < 1:
                    continue
                try:
                    self._trim_lineage(lane_id, task_id, epoch)
                    out.append(self.maybe_auto_rollover(lane_id, task_id, epoch))
                except Exception as exc:
                    self.bridge.event(
                        "worker.maintenance_error",
                        "error",
                        lane_id=lane_id,
                        owner_task_id=task_id,
                        owner_control_epoch=epoch,
                        message=str(exc)[:800],
                    )
        return out

