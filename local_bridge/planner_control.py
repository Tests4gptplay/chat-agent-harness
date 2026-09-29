#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable


class PlannerControlError(ValueError):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


EVENT_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "worker_result": (
        "child_task_id", "backend_cl", "dispatch_id", "dispatch_generation",
        "fence_token", "result_ref", "result_blob_sha",
    ),
    "executor_result": (
        "action_id", "round", "result_id", "result_ref", "result_blob_sha",
        "terminal_status",
    ),
    "helper_result": (
        "helper_request_id", "challenge", "output_ref", "output_blob_sha",
    ),
    "foreground_intent": (
        "task_contract_ref", "task_contract_blob_sha", "intent_revision",
    ),
    "dependency_resolved": ("dependency_key", "resolved_ref", "resolved_blob_sha"),
    "resource_resolved": ("resource_key", "resolved_revision"),
    "barrier_satisfied": ("barrier_id", "sorted_member_terminal_refs_and_shas"),
    "admission_changed": ("admission_ref", "admission_blob_sha", "new_state"),
    "install_result": ("install_run_id", "result_ref", "result_blob_sha"),
    "planner_continuation": (
        "reason_code", "canonical_task_state_blob_sha", "last_decision_ref",
    ),
}

SOURCE_ROLES = {"worker", "executor", "helper", "foreground", "harness"}
EVENT_STATES = {"PENDING", "CLAIMED", "DELIVERED", "CONSUMED", "SUPERSEDED", "REJECTED"}
WAIT_KINDS = {
    "WAIT_RESULT", "WAIT_RESOURCE", "WAIT_DEP", "WAIT_HELPER",
    "WAIT_ADMISSION", "WAIT_INSTALL", "WAIT_BARRIER", "WAIT_USER", "NEED_USER",
}
ACTIVITY_STATES = {
    "ACTIVE", "PARKED_WAIT_EVENT", "WAKING", "REPLANNING", "WAIT_USER",
    "HANDOFF", "ROTATING", "DONE", "ERROR",
}
HANDOFF_REASONS = {"context_compacted", "broken_binding", "explicit_lifecycle_rollover"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def state_digest(control: dict[str, Any]) -> str:
    return sha256_hex(control)


def _require_nonempty(value: Any, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PlannerControlError(code)
    return text


def _require_int(value: Any, code: str, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PlannerControlError(code) from None
    if number < minimum:
        raise PlannerControlError(code)
    return number


def _require_expected_digest(control: dict[str, Any], expected_digest: str | None) -> None:
    if expected_digest is not None and state_digest(control) != expected_digest:
        raise PlannerControlError("PLANNER_CONTROL_CAS_MISMATCH")


def _ensure_inbox(control: dict[str, Any]) -> dict[str, Any]:
    inbox = control.setdefault("inbox", {})
    if not isinstance(inbox, dict):
        raise PlannerControlError("PLANNER_INBOX_INVALID")
    events = inbox.setdefault("events", {})
    if not isinstance(events, dict):
        raise PlannerControlError("PLANNER_EVENTS_INVALID")
    inbox.setdefault("active_doorbell", None)
    return inbox


def validate_event_source(kind: str, source_identity: dict[str, Any]) -> None:
    required = EVENT_SOURCE_FIELDS.get(kind)
    if required is None:
        raise PlannerControlError("PLANNER_EVENT_KIND_INVALID", kind)
    if not isinstance(source_identity, dict):
        raise PlannerControlError("PLANNER_EVENT_SOURCE_IDENTITY_INVALID")
    for field in required:
        if field not in source_identity:
            raise PlannerControlError("PLANNER_EVENT_SOURCE_IDENTITY_MISSING", field)


def planner_event_id(
    task_id: str,
    control_epoch: int,
    kind: str,
    source_role: str,
    source_identity: dict[str, Any],
) -> str:
    task_id = _require_nonempty(task_id, "PLANNER_TASK_ID_REQUIRED")
    epoch = _require_int(control_epoch, "PLANNER_CONTROL_EPOCH_INVALID")
    source_role = _require_nonempty(source_role, "PLANNER_EVENT_SOURCE_ROLE_REQUIRED")
    if source_role not in SOURCE_ROLES:
        raise PlannerControlError("PLANNER_EVENT_SOURCE_ROLE_INVALID", source_role)
    validate_event_source(kind, source_identity)
    identity = {
        "v": 1,
        "task_id": task_id,
        "control_epoch": epoch,
        "kind": kind,
        "source_role": source_role,
        "source_identity": source_identity,
    }
    return "event-" + sha256_hex(identity)[:32]


def make_planner_event(
    *,
    task_id: str,
    control_epoch: int,
    kind: str,
    source_role: str,
    source_identity: dict[str, Any],
    refs: Iterable[str] = (),
) -> dict[str, Any]:
    event_id = planner_event_id(task_id, control_epoch, kind, source_role, source_identity)
    return {
        "v": 1,
        "event_id": event_id,
        "kind": kind,
        "task_id": task_id,
        "control_epoch": int(control_epoch),
        "source_role": source_role,
        "source_identity": copy.deepcopy(source_identity),
        "refs": [str(x) for x in refs],
        "state": "PENDING",
        "claim": None,
        "delivered_at": None,
        "consumed_at": None,
        "reject_code": None,
    }


def validate_authority(
    control: dict[str, Any],
    *,
    task_id: str,
    control_epoch: int,
    planner_generation: int,
    planner_fence_token: str,
) -> None:
    if str(control.get("task_id") or task_id) != task_id:
        raise PlannerControlError("PLANNER_TASK_ID_MISMATCH")
    if int(control.get("control_epoch") or 0) != int(control_epoch):
        raise PlannerControlError("PLANNER_CONTROL_EPOCH_MISMATCH")
    authority = control.get("authority")
    if not isinstance(authority, dict):
        raise PlannerControlError("PLANNER_AUTHORITY_MISSING")
    active_generation = int(authority.get("planner_generation") or 0)
    active_fence = str(authority.get("planner_fence_token") or "")
    if active_generation == int(planner_generation) and active_fence == str(planner_fence_token):
        if authority.get("semantic_authority") is False or authority.get("fenced_for_rotation") is True:
            raise PlannerControlError("PLANNER_STALE_GENERATION_OR_FENCE")
        return

    successor = control.get("successor")
    if isinstance(successor, dict):
        if (
            int(successor.get("to_generation") or 0) == int(planner_generation)
            and str(successor.get("pending_fence_token") or "") == str(planner_fence_token)
            and str(successor.get("state") or "NONE") in {"REQUESTED", "BOUND", "TAKEOVER_VERIFIED"}
        ):
            raise PlannerControlError("PLANNER_NOT_YET_ACTIVE")

    retired = control.get("retired_planners")
    if isinstance(retired, list):
        for record in retired:
            if not isinstance(record, dict):
                continue
            if (
                int(record.get("planner_generation") or 0) == int(planner_generation)
                and str(record.get("planner_fence_token") or "") == str(planner_fence_token)
            ):
                raise PlannerControlError("PLANNER_STALE_GENERATION_OR_FENCE")

    raise PlannerControlError("PLANNER_STALE_GENERATION_OR_FENCE")


def insert_planner_event(
    control: dict[str, Any],
    event: dict[str, Any],
    *,
    expected_digest: str | None = None,
) -> tuple[dict[str, Any], bool]:
    _require_expected_digest(control, expected_digest)
    result = copy.deepcopy(control)
    inbox = _ensure_inbox(result)

    if int(event.get("v") or 0) != 1:
        raise PlannerControlError("PLANNER_EVENT_SCHEMA_INVALID")
    event_id = _require_nonempty(event.get("event_id"), "PLANNER_EVENT_ID_REQUIRED")
    validate_event_source(str(event.get("kind") or ""), event.get("source_identity"))
    computed = planner_event_id(
        str(event.get("task_id") or ""),
        int(event.get("control_epoch") or 0),
        str(event.get("kind") or ""),
        str(event.get("source_role") or ""),
        event.get("source_identity"),
    )
    if event_id != computed:
        raise PlannerControlError("PLANNER_EVENT_ID_MISMATCH")
    if str(event.get("task_id") or "") != str(result.get("task_id") or ""):
        raise PlannerControlError("PLANNER_EVENT_TASK_MISMATCH")
    if int(event.get("control_epoch") or 0) != int(result.get("control_epoch") or 0):
        raise PlannerControlError("PLANNER_EVENT_EPOCH_MISMATCH")

    existing = inbox["events"].get(event_id)
    if existing is not None:
        immutable_keys = (
            "v", "event_id", "kind", "task_id", "control_epoch",
            "source_role", "source_identity",
        )
        if any(canonical_json(existing.get(key)) != canonical_json(event.get(key)) for key in immutable_keys):
            raise PlannerControlError("PLANNER_EVENT_ID_COLLISION")
        return result, False

    inbox["events"][event_id] = copy.deepcopy(event)
    return result, True


def planner_doorbell_id(
    task_id: str,
    control_epoch: int,
    planner_generation: int,
    planner_fence_token: str,
    event_ids: Iterable[str],
) -> str:
    identity = {
        "v": 1,
        "task_id": task_id,
        "control_epoch": int(control_epoch),
        "planner_generation": int(planner_generation),
        "planner_fence_token": planner_fence_token,
        "event_ids": sorted(str(x) for x in event_ids),
    }
    return "doorbell-" + sha256_hex(identity)[:32]


def claim_planner_events(
    control: dict[str, Any],
    *,
    task_id: str,
    control_epoch: int,
    planner_generation: int,
    planner_fence_token: str,
    claimed_at: str,
    compatible_kinds: set[str] | None = None,
    expected_digest: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    _require_expected_digest(control, expected_digest)
    validate_authority(
        control,
        task_id=task_id,
        control_epoch=control_epoch,
        planner_generation=planner_generation,
        planner_fence_token=planner_fence_token,
    )
    result = copy.deepcopy(control)
    inbox = _ensure_inbox(result)
    active = inbox.get("active_doorbell")
    if isinstance(active, dict) and str(active.get("delivery_state") or "") not in {"", "REDUCED"}:
        raise PlannerControlError("PLANNER_DOORBELL_ALREADY_ACTIVE")

    pending = []
    for event_id, event in inbox["events"].items():
        if not isinstance(event, dict) or event.get("state") != "PENDING":
            continue
        if compatible_kinds is not None and str(event.get("kind") or "") not in compatible_kinds:
            continue
        pending.append(event_id)
    pending.sort()
    if not pending:
        return result, None

    doorbell_id = planner_doorbell_id(
        task_id, control_epoch, planner_generation, planner_fence_token, pending
    )
    for event_id in pending:
        event = inbox["events"][event_id]
        event["state"] = "CLAIMED"
        event["claim"] = {
            "planner_generation": int(planner_generation),
            "planner_fence_token": planner_fence_token,
            "doorbell_id": doorbell_id,
            "claimed_at": claimed_at,
        }
    doorbell = {
        "doorbell_id": doorbell_id,
        "event_ids": pending,
        "planner_generation": int(planner_generation),
        "planner_fence_token": planner_fence_token,
        "decision_ref": f"evidence/{task_id}/roles/planner/decisions/{doorbell_id}.json",
        "delivery_state": "CLAIMED",
    }
    inbox["active_doorbell"] = doorbell
    result["activity"] = "WAKING"
    return result, copy.deepcopy(doorbell)


def complete_planner_turn(
    control: dict[str, Any],
    *,
    doorbell_id: str,
    consumed_at: str,
) -> dict[str, Any]:
    result = copy.deepcopy(control)
    inbox = _ensure_inbox(result)
    active = inbox.get("active_doorbell")
    if not isinstance(active, dict) or str(active.get("doorbell_id") or "") != str(doorbell_id):
        raise PlannerControlError("PLANNER_TURN_DOORBELL_MISMATCH")
    for event_id in active.get("event_ids") or []:
        event = inbox["events"].get(str(event_id))
        if isinstance(event, dict):
            event["state"] = "CONSUMED"
            event["consumed_at"] = consumed_at
    inbox["active_doorbell"] = None
    result["wait"] = None
    return result


def has_exact_durable_wait(control: dict[str, Any]) -> bool:
    wait = control.get("wait")
    return (
        isinstance(wait, dict)
        and str(wait.get("kind") or "") in WAIT_KINDS
        and wait.get("selector") is not None
        and wait.get("selector") != ""
        and wait.get("selector") != {}
    )


def is_quiet_state(
    control: dict[str, Any],
    *,
    paused: bool = False,
    cancelled: bool = False,
    blocker_ref: str | None = None,
    bounded_child_or_role_owner: bool = False,
) -> bool:
    activity = str(control.get("activity") or "")
    if paused or cancelled or blocker_ref or bounded_child_or_role_owner:
        return True
    if activity == "PARKED_WAIT_EVENT":
        return has_exact_durable_wait(control)
    if activity == "WAIT_USER":
        return has_exact_durable_wait(control)
    return activity in {"HANDOFF", "ROTATING", "DONE", "ERROR"}


def needs_orphan_continuation(
    control: dict[str, Any],
    *,
    task_terminal: bool,
    actionable_work_remains: bool,
    paused: bool = False,
    cancelled: bool = False,
    blocker_ref: str | None = None,
    bounded_child_or_role_owner: bool = False,
) -> bool:
    if task_terminal or not actionable_work_remains:
        return False
    activity = str(control.get("activity") or "")
    if activity in {"ACTIVE", "WAKING", "REPLANNING"}:
        return False
    return not is_quiet_state(
        control,
        paused=paused,
        cancelled=cancelled,
        blocker_ref=blocker_ref,
        bounded_child_or_role_owner=bounded_child_or_role_owner,
    )


def ensure_orphan_continuation(
    control: dict[str, Any],
    *,
    canonical_task_state_blob_sha: str,
    reason_code: str = "ORPHAN_ACTIONABLE_STATE",
    expected_digest: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if not needs_orphan_continuation(
        control, task_terminal=False, actionable_work_remains=True
    ):
        return copy.deepcopy(control), False
    last = control.get("last_decision")
    source_identity = {
        "reason_code": reason_code,
        "canonical_task_state_blob_sha": canonical_task_state_blob_sha,
        "last_decision_ref": str(last.get("decision_ref") or "") if isinstance(last, dict) else "",
    }
    event = make_planner_event(
        task_id=str(control.get("task_id") or ""),
        control_epoch=int(control.get("control_epoch") or 0),
        kind="planner_continuation",
        source_role="harness",
        source_identity=source_identity,
        refs=[canonical_task_state_blob_sha],
    )
    return insert_planner_event(control, event, expected_digest=expected_digest)


def deterministic_planner_fence(
    task_id: str,
    control_epoch: int,
    conversation_id: str,
    request_id: str,
    challenge: str,
    generation: int = 1,
) -> str:
    identity = {
        "v": 1,
        "task_id": task_id,
        "control_epoch": int(control_epoch),
        "planner_generation": int(generation),
        "conversation_id": conversation_id,
        "request_id": request_id,
        "challenge": challenge,
    }
    return "planner-fence-" + sha256_hex(identity)[:32]


def migrate_flat_planner_binding(
    task_cell: dict[str, Any],
    *,
    admission_ok: bool,
    task_contract_ref: str,
    task_contract_revision: int,
    task_contract_blob_sha: str,
    plan_ref: str,
    plan_blob_sha: str,
    initial_wait: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not admission_ok:
        raise PlannerControlError("PLANNER_MIGRATION_ADMISSION_REQUIRED")
    result = copy.deepcopy(task_cell)
    task_id = _require_nonempty(result.get("task_id"), "PLANNER_MIGRATION_TASK_REQUIRED")
    epoch = _require_int(result.get("control_epoch"), "PLANNER_MIGRATION_EPOCH_REQUIRED")
    roles = result.get("roles")
    planner = roles.get("planner") if isinstance(roles, dict) else None
    if not isinstance(planner, dict):
        raise PlannerControlError("PLANNER_MIGRATION_BINDING_MISSING")
    conversation_id = _require_nonempty(
        planner.get("conversation_id"), "PLANNER_MIGRATION_CONVERSATION_REQUIRED"
    )
    request_id = _require_nonempty(planner.get("request_id"), "PLANNER_MIGRATION_REQUEST_REQUIRED")
    challenge = _require_nonempty(planner.get("challenge"), "PLANNER_MIGRATION_CHALLENGE_REQUIRED")
    project_key = _require_nonempty(
        result.get("task_cell_project_key") or planner.get("project_key"),
        "PLANNER_MIGRATION_PROJECT_REQUIRED",
    )
    if "planner_control" in result:
        raise PlannerControlError("PLANNER_MIGRATION_ALREADY_PRESENT")

    fence = deterministic_planner_fence(
        task_id, epoch, conversation_id, request_id, challenge, generation=1
    )
    status = str(result.get("status") or "")
    if status in {"DONE", "SUCCESS", "COMPLETE_ACCEPTED"}:
        activity = "DONE"
    elif status in {"ERROR", "FAILURE", "CANCELLED"}:
        activity = "ERROR"
    elif initial_wait is not None:
        if str(initial_wait.get("kind") or "") not in WAIT_KINDS:
            raise PlannerControlError("PLANNER_MIGRATION_WAIT_INVALID")
        activity = "PARKED_WAIT_EVENT"
    elif planner.get("response_started") is True or planner.get("semantic_ready") is True:
        activity = "ACTIVE"
    else:
        raise PlannerControlError("PLANNER_MIGRATION_OWNER_UNPROVEN")

    planner.update({
        "planner_generation": 1,
        "planner_fence_token": fence,
        "successor": None,
        "retired": [],
    })
    result["roles"]["planner"] = planner
    result["planner_control"] = {
        "v": 1,
        "feature": "HYBRID_ACTIVE",
        "enabled": True,
        "task_id": task_id,
        "control_epoch": epoch,
        "activity": activity,
        "authority": {
            "control_epoch": epoch,
            "planner_generation": 1,
            "planner_fence_token": fence,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "challenge": challenge,
            "takeover_handoff_id": None,
            "project_key": project_key,
        },
        "wait": copy.deepcopy(initial_wait),
        "inbox": {"events": {}, "active_doorbell": None},
        "last_decision": None,
        "successor": {
            "state": "NONE",
            "handoff_id": None,
            "from_generation": None,
            "to_generation": None,
            "packet_ref": None,
            "candidate_conversation_id": None,
            "candidate_request_id": None,
            "candidate_challenge": None,
            "pending_fence_token": None,
        },
        "retired_planners": [],
        "semantic_authority_closed": activity in {"DONE", "ERROR"},
        "migration": {
            "task_contract_ref": task_contract_ref,
            "task_contract_revision": int(task_contract_revision),
            "task_contract_blob_sha": task_contract_blob_sha,
            "plan_ref": plan_ref,
            "plan_blob_sha": plan_blob_sha,
            "created_new_conversation": False,
        },
    }
    return result


def stage_planner_successor(
    control: dict[str, Any],
    *,
    handoff_id: str,
    reason: str,
    pending_fence_token: str,
) -> dict[str, Any]:
    if reason not in HANDOFF_REASONS:
        raise PlannerControlError("PLANNER_HANDOFF_REASON_INVALID", reason)
    result = copy.deepcopy(control)
    authority = result.get("authority")
    if not isinstance(authority, dict):
        raise PlannerControlError("PLANNER_AUTHORITY_MISSING")
    successor = result.get("successor")
    if not isinstance(successor, dict):
        successor = {"state": "NONE"}
    if str(successor.get("state") or "NONE") != "NONE":
        raise PlannerControlError("PLANNER_SUCCESSOR_ALREADY_EXISTS")

    generation = _require_int(
        authority.get("planner_generation"), "PLANNER_ACTIVE_GENERATION_INVALID"
    )
    successor = {
        "state": "REQUESTED",
        "handoff_id": handoff_id,
        "reason": reason,
        "from_generation": generation,
        "to_generation": generation + 1,
        "packet_ref": None,
        "candidate_conversation_id": None,
        "candidate_request_id": None,
        "candidate_challenge": None,
        "pending_fence_token": pending_fence_token,
        "takeover_ref": None,
    }
    result["successor"] = successor
    authority = copy.deepcopy(authority)
    authority["semantic_authority"] = False
    authority["fenced_for_rotation"] = True
    result["authority"] = authority
    result["activity"] = "ROTATING"
    return result


def bind_planner_successor(
    control: dict[str, Any],
    *,
    handoff_id: str,
    candidate_conversation_id: str,
    candidate_request_id: str,
    candidate_challenge: str,
    packet_ref: str,
) -> dict[str, Any]:
    result = copy.deepcopy(control)
    successor = result.get("successor")
    if not isinstance(successor, dict) or successor.get("state") != "REQUESTED":
        raise PlannerControlError("PLANNER_SUCCESSOR_NOT_REQUESTED")
    if successor.get("handoff_id") != handoff_id:
        raise PlannerControlError("PLANNER_SUCCESSOR_HANDOFF_MISMATCH")
    successor.update({
        "state": "BOUND",
        "candidate_conversation_id": candidate_conversation_id,
        "candidate_request_id": candidate_request_id,
        "candidate_challenge": candidate_challenge,
        "packet_ref": packet_ref,
    })
    result["successor"] = successor
    return result
