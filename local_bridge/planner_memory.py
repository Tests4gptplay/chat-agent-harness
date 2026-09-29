#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


class PlannerMemoryError(ValueError):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        super().__init__(code if detail is None else f"{code}: {detail}")


SCHEMA_VERSION = 1
RETENTION_CLASSES = {"EPHEMERAL", "HANDOFF_ONLY", "ROLLBACK_WINDOW", "AUDIT_FINAL"}
STORAGE_POLICY_CLASSES = {"SOURCE", "EVIDENCE", "CACHE", "SCRATCH"}
RESOURCE_KINDS = {"GIT_PATH", "MANAGED_PATH", "TASK_CELL_CONVERSATION", "RUNTIME_RECORD"}
HANDOFF_REASONS = {"context_compacted", "broken_binding", "explicit_lifecycle_rollover"}
MEANINGFUL_CHECKPOINT_TRIGGERS = {
    "TASK_CONTRACT_CHANGED", "PLAN_CHANGED", "DECISION_CHANGED", "RESULT_CHANGED",
    "WAIT_CHANGED", "ROLE_RESULT_CHANGED", "ADMISSION_CHANGED", "BEFORE_ROLLOVER",
    "AFTER_PROMOTION", "BEFORE_COMPLETE",
}
MEANINGFUL_FIELDS = {
    "task_contract_ref", "task_contract_revision", "task_contract_blob_sha",
    "plan_ref", "plan_blob_sha",
}

CURRENT_REQUIRED = {
    "schema_version", "role", "task_id", "control_epoch", "memory_version",
    "planner_generation", "planner_fence_token", "task_contract_ref",
    "task_contract_revision", "task_contract_blob_sha", "plan_ref", "plan_blob_sha",
    "last_checkpoint", "written_at", "terminal", "read_only", "retention_class",
}

GENERATION_REQUIRED = {
    "schema_version", "role", "task_id", "control_epoch", "planner_generation",
    "planner_fence_token", "conversation_identity", "started_at", "sealed_at",
    "seal_reason", "source_current_ref", "source_current_blob_sha",
    "source_memory_version", "task_contract_ref", "task_contract_revision",
    "task_contract_blob_sha", "plan_ref", "plan_blob_sha",
    "successor_handoff_id_or_null", "sealed", "retention_class",
}

HANDOFF_REQUIRED = {
    "schema_version", "role", "handoff_id", "handoff_revision", "handoff_state",
    "task_id", "control_epoch", "reason", "from_generation", "from_fence_token",
    "predecessor_project_key", "predecessor_conversation_id", "to_generation",
    "pending_successor_fence_token", "sealed_generation_ref",
    "sealed_generation_blob_sha", "current_memory_ref", "current_memory_blob_sha",
    "memory_version", "canonical_task_cell_ref", "canonical_task_cell_blob_sha",
    "successor_binding", "promotion", "predecessor_retirement", "retention_class",
}

CLEANUP_REQUIRED = {
    "schema_version", "role", "task_id", "control_epoch", "cleanup_generation",
    "manifest_id", "manifest_digest", "terminal_decision_ref",
    "terminal_decision_blob_sha", "final_planner_generation",
    "final_planner_fence_token", "final_terminal_memory_ref",
    "final_terminal_memory_blob_sha", "delete_candidates", "preserve_candidates",
    "protected_refs_snapshot", "rollback_window_refs", "dependency_checks",
    "created_at", "cleanup_status",
}

CLEANUP_CANDIDATE_REQUIRED = {
    "candidate_id", "action", "resource_kind", "path_or_exact_resource_identity",
    "task_owner", "retention_class", "storage_policy_class",
    "managed_root_id_or_task_cell_project_key", "reason", "dependency_refs",
    "expected_blob_sha_or_identity_digest", "delete_after_gate",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def memory_blob_sha(value: dict[str, Any]) -> str:
    return sha256_hex(value)


def _safe_task_id(task_id: str) -> str:
    task_id = str(task_id or "").strip()
    if not task_id or not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
        raise PlannerMemoryError("PLANNER_MEMORY_TASK_ID_INVALID")
    return task_id


def planner_memory_root(task_id: str) -> str:
    return f"memory/planner/{_safe_task_id(task_id)}"


def planner_current_path(task_id: str) -> str:
    return f"{planner_memory_root(task_id)}/current.json"


def planner_generation_path(task_id: str, generation: int) -> str:
    if int(generation) < 1:
        raise PlannerMemoryError("PLANNER_MEMORY_GENERATION_INVALID")
    return f"{planner_memory_root(task_id)}/generations/planner-g{int(generation):04d}.json"


def planner_handoff_path(task_id: str, handoff_id: str) -> str:
    handoff_id = str(handoff_id or "").strip()
    if not handoff_id or "/" in handoff_id or "\\" in handoff_id or handoff_id in {".", ".."}:
        raise PlannerMemoryError("PLANNER_HANDOFF_ID_INVALID")
    return f"{planner_memory_root(task_id)}/handoffs/handoff-{handoff_id}.json"


def planner_cleanup_path(task_id: str) -> str:
    return f"{planner_memory_root(task_id)}/cleanup.json"


def _normal_repo_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        raise PlannerMemoryError("PLANNER_MEMORY_PATH_SCOPE_VIOLATION")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise PlannerMemoryError("PLANNER_MEMORY_PATH_SCOPE_VIOLATION")
    return str(PurePosixPath(*parts))


def validate_planner_memory_write(
    path: str,
    *,
    task_id: str,
    writer_role: str,
    content_task_id: str | None = None,
) -> str:
    task_id = _safe_task_id(task_id)
    normal = _normal_repo_path(path)
    planner_prefix = "memory/planner/"
    exact_prefix = planner_memory_root(task_id) + "/"
    role = str(writer_role or "").lower()

    if normal.startswith(planner_prefix) and role != "planner":
        raise PlannerMemoryError("PLANNER_MEMORY_ROLE_MISMATCH")
    if role == "planner" and not normal.startswith(exact_prefix):
        raise PlannerMemoryError("PLANNER_MEMORY_PATH_SCOPE_VIOLATION")
    if normal.startswith(planner_prefix) and not normal.startswith(exact_prefix):
        raise PlannerMemoryError("PLANNER_MEMORY_TASK_MISMATCH")
    if content_task_id is not None and str(content_task_id) != task_id:
        raise PlannerMemoryError("PLANNER_MEMORY_TASK_MISMATCH")
    return normal


def validate_worker_memory_target(path: str, *, writer_role: str = "worker") -> str:
    normal = _normal_repo_path(path)
    if normal.startswith("memory/planner/"):
        raise PlannerMemoryError("PLANNER_MEMORY_ROLE_MISMATCH")
    if str(writer_role or "").lower() == "planner":
        raise PlannerMemoryError("WORKER_MEMORY_ROLE_MISMATCH")
    return normal


def _require_fields(record: dict[str, Any], required: set[str], code: str) -> None:
    missing = sorted(required - set(record))
    if missing:
        raise PlannerMemoryError(code, ",".join(missing))


def validate_current_capsule(current: dict[str, Any], *, task_id: str | None = None) -> None:
    if not isinstance(current, dict):
        raise PlannerMemoryError("PLANNER_MEMORY_CURRENT_INVALID")
    _require_fields(current, CURRENT_REQUIRED, "PLANNER_MEMORY_CURRENT_FIELDS_MISSING")
    if int(current.get("schema_version") or 0) != SCHEMA_VERSION:
        raise PlannerMemoryError("PLANNER_MEMORY_SCHEMA_UNSUPPORTED")
    if current.get("role") != "planner":
        raise PlannerMemoryError("PLANNER_MEMORY_ROLE_MISMATCH")
    if task_id is not None and str(current.get("task_id") or "") != _safe_task_id(task_id):
        raise PlannerMemoryError("PLANNER_MEMORY_TASK_MISMATCH")
    if int(current.get("control_epoch") or 0) < 1:
        raise PlannerMemoryError("PLANNER_MEMORY_CONTROL_EPOCH_INVALID")
    if int(current.get("memory_version") or 0) < 1:
        raise PlannerMemoryError("PLANNER_MEMORY_VERSION_INVALID")
    if int(current.get("planner_generation") or 0) < 1:
        raise PlannerMemoryError("PLANNER_MEMORY_GENERATION_INVALID")
    if current.get("retention_class") not in RETENTION_CLASSES:
        raise PlannerMemoryError("PLANNER_MEMORY_RETENTION_INVALID")


def make_planner_current(
    *,
    task_id: str,
    control_epoch: int,
    planner_generation: int,
    planner_fence_token: str,
    task_contract_ref: str,
    task_contract_revision: int,
    task_contract_blob_sha: str,
    plan_ref: str,
    plan_blob_sha: str,
    written_at: str,
) -> dict[str, Any]:
    task_id = _safe_task_id(task_id)
    current = {
        "schema_version": 1,
        "role": "planner",
        "task_id": task_id,
        "control_epoch": int(control_epoch),
        "memory_version": 1,
        "planner_generation": int(planner_generation),
        "planner_fence_token": str(planner_fence_token),
        "task_contract_ref": str(task_contract_ref),
        "task_contract_revision": int(task_contract_revision),
        "task_contract_blob_sha": str(task_contract_blob_sha),
        "plan_ref": str(plan_ref),
        "plan_blob_sha": str(plan_blob_sha),
        "last_checkpoint": {"trigger": "MIGRATION", "memory_version": 1},
        "written_at": written_at,
        "terminal": False,
        "read_only": False,
        "retention_class": "EPHEMERAL",
    }
    validate_current_capsule(current, task_id=task_id)
    return current


def checkpoint_planner_current(
    current: dict[str, Any],
    patch: dict[str, Any],
    *,
    expected_memory_version: int,
    trigger: str,
    written_at: str,
    writer_role: str = "planner",
    path: str | None = None,
) -> dict[str, Any]:
    validate_current_capsule(current)
    task_id = str(current["task_id"])
    validate_planner_memory_write(
        path or planner_current_path(task_id),
        task_id=task_id,
        writer_role=writer_role,
        content_task_id=str(patch.get("task_id") or task_id),
    )
    if current.get("read_only") is True:
        raise PlannerMemoryError("PLANNER_MEMORY_READ_ONLY")
    if int(current["memory_version"]) != int(expected_memory_version):
        raise PlannerMemoryError("PLANNER_MEMORY_CAS_MISMATCH")
    if trigger not in MEANINGFUL_CHECKPOINT_TRIGGERS:
        raise PlannerMemoryError("PLANNER_MEMORY_CHECKPOINT_TRIGGER_INVALID")

    result = copy.deepcopy(current)
    changed = False
    for key, value in patch.items():
        if key in {"schema_version", "role", "task_id", "memory_version"}:
            if result.get(key) != value:
                raise PlannerMemoryError("PLANNER_MEMORY_IMMUTABLE_FIELD_CHANGE", key)
            continue
        if result.get(key) != value:
            result[key] = copy.deepcopy(value)
            if key in MEANINGFUL_FIELDS:
                changed = True
    if not changed and trigger not in {"BEFORE_ROLLOVER", "AFTER_PROMOTION", "BEFORE_COMPLETE"}:
        raise PlannerMemoryError("PLANNER_MEMORY_CHECKPOINT_NOT_MEANINGFUL")

    result["memory_version"] = int(expected_memory_version) + 1
    result["last_checkpoint"] = {
        "trigger": trigger,
        "from_memory_version": int(expected_memory_version),
        "memory_version": result["memory_version"],
    }
    result["written_at"] = written_at
    validate_current_capsule(result, task_id=task_id)
    return result


def seal_planner_generation(
    current: dict[str, Any],
    *,
    conversation_identity: dict[str, Any],
    started_at: str,
    sealed_at: str,
    seal_reason: str,
    source_current_ref: str,
    source_current_blob_sha: str,
    successor_handoff_id_or_null: str | None = None,
) -> dict[str, Any]:
    validate_current_capsule(current)
    if seal_reason not in {"ROLLOVER", "TERMINAL"}:
        raise PlannerMemoryError("PLANNER_MEMORY_SEAL_REASON_INVALID")
    project_key = str(conversation_identity.get("project_key") or "")
    conversation_id = str(conversation_identity.get("conversation_id") or "")
    if not project_key.startswith("g-p-") or not conversation_id:
        raise PlannerMemoryError("PLANNER_MEMORY_CONVERSATION_IDENTITY_INVALID")
    capsule = {
        "schema_version": 1,
        "role": "planner",
        "task_id": current["task_id"],
        "control_epoch": current["control_epoch"],
        "planner_generation": current["planner_generation"],
        "planner_fence_token": current["planner_fence_token"],
        "conversation_identity": {
            "project_key": project_key,
            "conversation_id": conversation_id,
        },
        "started_at": started_at,
        "sealed_at": sealed_at,
        "seal_reason": seal_reason,
        "source_current_ref": source_current_ref,
        "source_current_blob_sha": source_current_blob_sha,
        "source_memory_version": current["memory_version"],
        "task_contract_ref": current["task_contract_ref"],
        "task_contract_revision": current["task_contract_revision"],
        "task_contract_blob_sha": current["task_contract_blob_sha"],
        "plan_ref": current["plan_ref"],
        "plan_blob_sha": current["plan_blob_sha"],
        "successor_handoff_id_or_null": successor_handoff_id_or_null,
        "sealed": True,
        "retention_class": "AUDIT_FINAL" if seal_reason == "TERMINAL" else "HANDOFF_ONLY",
    }
    _require_fields(capsule, GENERATION_REQUIRED, "PLANNER_MEMORY_GENERATION_FIELDS_MISSING")
    return capsule


def prepare_planner_handoff(
    current: dict[str, Any],
    sealed_generation: dict[str, Any],
    *,
    handoff_id: str,
    reason: str,
    pending_successor_fence_token: str,
    current_memory_ref: str,
    current_memory_blob_sha: str,
    sealed_generation_ref: str,
    sealed_generation_blob_sha: str,
    canonical_task_cell_ref: str,
    canonical_task_cell_blob_sha: str,
    predecessor_project_key: str,
    predecessor_conversation_id: str,
) -> dict[str, Any]:
    validate_current_capsule(current)
    _require_fields(sealed_generation, GENERATION_REQUIRED, "PLANNER_MEMORY_GENERATION_FIELDS_MISSING")
    if reason not in HANDOFF_REASONS:
        raise PlannerMemoryError("PLANNER_HANDOFF_REASON_INVALID")
    if sealed_generation.get("sealed") is not True:
        raise PlannerMemoryError("PLANNER_GENERATION_NOT_SEALED")
    if sealed_generation.get("seal_reason") != "ROLLOVER":
        raise PlannerMemoryError("PLANNER_GENERATION_NOT_ROLLOVER")
    if int(sealed_generation.get("planner_generation") or 0) != int(current["planner_generation"]):
        raise PlannerMemoryError("PLANNER_HANDOFF_GENERATION_MISMATCH")
    if str(sealed_generation.get("planner_fence_token") or "") != str(current["planner_fence_token"]):
        raise PlannerMemoryError("PLANNER_HANDOFF_FENCE_MISMATCH")
    if int(sealed_generation.get("source_memory_version") or 0) != int(current["memory_version"]):
        raise PlannerMemoryError("PLANNER_HANDOFF_MEMORY_VERSION_MISMATCH")

    handoff = {
        "schema_version": 1,
        "role": "planner",
        "handoff_id": str(handoff_id),
        "handoff_revision": 1,
        "handoff_state": "PREPARED",
        "task_id": current["task_id"],
        "control_epoch": current["control_epoch"],
        "reason": reason,
        "from_generation": current["planner_generation"],
        "from_fence_token": current["planner_fence_token"],
        "predecessor_project_key": predecessor_project_key,
        "predecessor_conversation_id": predecessor_conversation_id,
        "to_generation": int(current["planner_generation"]) + 1,
        "pending_successor_fence_token": pending_successor_fence_token,
        "sealed_generation_ref": sealed_generation_ref,
        "sealed_generation_blob_sha": sealed_generation_blob_sha,
        "current_memory_ref": current_memory_ref,
        "current_memory_blob_sha": current_memory_blob_sha,
        "memory_version": current["memory_version"],
        "canonical_task_cell_ref": canonical_task_cell_ref,
        "canonical_task_cell_blob_sha": canonical_task_cell_blob_sha,
        "successor_binding": None,
        "promotion": None,
        "predecessor_retirement": None,
        "retention_class": "HANDOFF_ONLY",
    }
    _require_fields(handoff, HANDOFF_REQUIRED, "PLANNER_HANDOFF_FIELDS_MISSING")
    return handoff


def record_successor_binding(
    handoff: dict[str, Any],
    *,
    conversation_id: str,
    request_id: str,
    challenge: str,
    project_key: str,
) -> dict[str, Any]:
    _require_fields(handoff, HANDOFF_REQUIRED, "PLANNER_HANDOFF_FIELDS_MISSING")
    if handoff.get("handoff_state") != "PREPARED":
        raise PlannerMemoryError("PLANNER_HANDOFF_STATE_INVALID")
    if handoff.get("successor_binding") is not None:
        raise PlannerMemoryError("PLANNER_SUCCESSOR_ALREADY_BOUND")
    result = copy.deepcopy(handoff)
    result["successor_binding"] = {
        "project_key": project_key,
        "conversation_id": conversation_id,
        "request_id": request_id,
        "challenge": challenge,
        "planner_generation": handoff["to_generation"],
        "planner_fence_token": handoff["pending_successor_fence_token"],
        "semantic_authority": False,
    }
    result["handoff_revision"] = int(result["handoff_revision"]) + 1
    return result


def promote_planner_authority_with_memory(
    current: dict[str, Any],
    handoff: dict[str, Any],
    planner_control: dict[str, Any],
    *,
    expected_memory_version: int,
    promotion_ref: str,
    promoted_at: str,
) -> dict[str, Any]:
    validate_current_capsule(current)
    _require_fields(handoff, HANDOFF_REQUIRED, "PLANNER_HANDOFF_FIELDS_MISSING")
    if handoff.get("handoff_state") != "PREPARED":
        raise PlannerMemoryError("PLANNER_REPLACEMENT_BINDING_NOT_READY")
    if int(current["memory_version"]) != int(expected_memory_version):
        raise PlannerMemoryError("PLANNER_MEMORY_CAS_MISMATCH")
    authority = planner_control.get("authority") if isinstance(planner_control, dict) else None
    if not isinstance(authority, dict):
        raise PlannerMemoryError("PLANNER_CONTROL_AUTHORITY_MISSING")
    if (
        int(authority.get("planner_generation") or 0) != int(handoff["from_generation"])
        or str(authority.get("planner_fence_token") or "") != str(handoff["from_fence_token"])
        or str(authority.get("conversation_id") or "") != str(handoff["predecessor_conversation_id"])
    ):
        raise PlannerMemoryError("PLANNER_PROMOTION_PREDECESSOR_AUTHORITY_MISMATCH")
    binding = handoff.get("successor_binding")
    if not isinstance(binding, dict):
        raise PlannerMemoryError("PLANNER_SUCCESSOR_NOT_BOUND")

    new_current = copy.deepcopy(current)
    new_handoff = copy.deepcopy(handoff)
    new_control = copy.deepcopy(planner_control)

    new_current["memory_version"] = int(expected_memory_version) + 1
    new_current["planner_generation"] = int(handoff["to_generation"])
    new_current["planner_fence_token"] = str(handoff["pending_successor_fence_token"])
    new_current["last_checkpoint"] = {
        "trigger": "AFTER_PROMOTION",
        "from_memory_version": int(expected_memory_version),
        "memory_version": new_current["memory_version"],
        "promotion_ref": promotion_ref,
    }
    new_current["written_at"] = promoted_at

    binding = copy.deepcopy(binding)
    binding["semantic_authority"] = True
    new_handoff["successor_binding"] = binding
    new_handoff["promotion"] = {
        "promotion_ref": promotion_ref,
        "promoted_at": promoted_at,
        "predecessor_authority_revoked": True,
        "active_generation": handoff["to_generation"],
        "active_fence_token": handoff["pending_successor_fence_token"],
        "active_conversation_id": binding["conversation_id"],
        "promotion_owner": "HARNESS",
    }
    new_handoff["handoff_state"] = "PROMOTED"
    new_handoff["handoff_revision"] = int(new_handoff["handoff_revision"]) + 1

    retired = new_control.setdefault("retired_planners", [])
    retired.append({
        "planner_generation": handoff["from_generation"],
        "planner_fence_token": handoff["from_fence_token"],
        "conversation_id": handoff["predecessor_conversation_id"],
        "project_key": handoff["predecessor_project_key"],
        "authority_revoked": True,
        "promotion_ref": promotion_ref,
    })
    new_control["authority"] = {
        **authority,
        "planner_generation": handoff["to_generation"],
        "planner_fence_token": handoff["pending_successor_fence_token"],
        "conversation_id": binding["conversation_id"],
        "request_id": binding["request_id"],
        "challenge": binding["challenge"],
        "project_key": binding["project_key"],
        "takeover_handoff_id": handoff["handoff_id"],
        "semantic_authority": True,
        "fenced_for_rotation": False,
    }
    new_control["activity"] = "ROTATING"
    new_control["successor"] = {
        **(new_control.get("successor") or {}),
        "state": "PROMOTED",
        "handoff_id": handoff["handoff_id"],
        "from_generation": handoff["from_generation"],
        "to_generation": handoff["to_generation"],
        "pending_fence_token": handoff["pending_successor_fence_token"],
        "candidate_conversation_id": binding["conversation_id"],
        "candidate_request_id": binding["request_id"],
        "candidate_challenge": binding["challenge"],
        "promotion_ref": promotion_ref,
    }
    validate_current_capsule(new_current, task_id=str(current["task_id"]))
    return {
        "current": new_current,
        "handoff": new_handoff,
        "planner_control": new_control,
        "atomic_bundle_required": True,
    }


def retirement_request_id(handoff: dict[str, Any]) -> str:
    return f"planner-retire-{handoff['handoff_id']}-g{int(handoff['from_generation'])}"


def validate_predecessor_retirement_eligibility(
    handoff: dict[str, Any],
    planner_control: dict[str, Any],
    *,
    task_cell_project_key: str,
    protected_conversation_ids: Iterable[str] = (),
) -> dict[str, Any]:
    _require_fields(handoff, HANDOFF_REQUIRED, "PLANNER_HANDOFF_FIELDS_MISSING")
    if handoff.get("handoff_state") not in {
        "PROMOTED", "RETIREMENT_PENDING", "RETIREMENT_ERROR", "RETIRED"
    }:
        raise PlannerMemoryError("PREDECESSOR_DELETE_BEFORE_VERIFIED_TAKEOVER")
    promotion = handoff.get("promotion")
    if not isinstance(promotion, dict) or promotion.get("predecessor_authority_revoked") is not True:
        raise PlannerMemoryError("PREDECESSOR_DELETE_BEFORE_VERIFIED_TAKEOVER")
    binding = handoff.get("successor_binding")
    if not isinstance(binding, dict) or binding.get("semantic_authority") is not True:
        raise PlannerMemoryError("PREDECESSOR_DELETE_BEFORE_REPLACEMENT_AUTHORITY")
    if str(handoff.get("predecessor_project_key") or "") != task_cell_project_key:
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_IDENTITY_MISMATCH")
    authority = planner_control.get("authority") if isinstance(planner_control, dict) else None
    if not isinstance(authority, dict):
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_IDENTITY_MISMATCH")
    if (
        int(authority.get("planner_generation") or 0) != int(handoff["to_generation"])
        or str(authority.get("planner_fence_token") or "")
        != str(handoff["pending_successor_fence_token"])
    ):
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_IDENTITY_MISMATCH")
    if (str(authority.get("conversation_id") or "") != str(binding.get("conversation_id") or "")
            or str(promotion.get("active_conversation_id") or "") != str(authority.get("conversation_id") or "")):
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_IDENTITY_MISMATCH")
    target_conversation = str(handoff.get("predecessor_conversation_id") or "")
    if target_conversation in {str(x) for x in protected_conversation_ids}:
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_IDENTITY_MISMATCH")
    if target_conversation == str(authority.get("conversation_id") or ""):
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_IDENTITY_MISMATCH")
    return {
        "eligible": True,
        "retirement_request_id": retirement_request_id(handoff),
        "project_key": handoff["predecessor_project_key"],
        "conversation_id": target_conversation,
        "exact_id_only": True,
    }


def record_predecessor_retirement_result(
    handoff: dict[str, Any],
    *,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(handoff)
    if status not in {"RETIRED", "RETIREMENT_PENDING", "RETIREMENT_ERROR"}:
        raise PlannerMemoryError("PREDECESSOR_RETIREMENT_STATUS_INVALID")
    result["predecessor_retirement"] = {
        "retirement_request_id": retirement_request_id(handoff),
        "project_key": handoff["predecessor_project_key"],
        "conversation_id": handoff["predecessor_conversation_id"],
        "status": status,
        "last_error": error,
    }
    result["handoff_state"] = status
    result["handoff_revision"] = int(result["handoff_revision"]) + 1
    return result


def terminalize_planner_memory(
    current: dict[str, Any],
    *,
    expected_memory_version: int,
    final_refs: dict[str, Any],
    written_at: str,
) -> dict[str, Any]:
    validate_current_capsule(current)
    if current.get("read_only") is True:
        raise PlannerMemoryError("PLANNER_MEMORY_READ_ONLY")
    if int(current["memory_version"]) != int(expected_memory_version):
        raise PlannerMemoryError("PLANNER_MEMORY_CAS_MISMATCH")
    result = copy.deepcopy(current)
    result["memory_version"] = int(expected_memory_version) + 1
    result["terminal"] = True
    result["read_only"] = True
    result["retention_class"] = "AUDIT_FINAL"
    result["terminal_refs"] = copy.deepcopy(final_refs)
    result["last_checkpoint"] = {
        "trigger": "BEFORE_COMPLETE",
        "from_memory_version": int(expected_memory_version),
        "memory_version": result["memory_version"],
    }
    result["written_at"] = written_at
    validate_current_capsule(result, task_id=str(current["task_id"]))
    return result


def cleanup_manifest_digest(manifest: dict[str, Any]) -> str:
    material = copy.deepcopy(manifest)
    material.pop("manifest_digest", None)
    return sha256_hex(material)


def validate_cleanup_candidate_schema(candidate: dict[str, Any]) -> None:
    if not isinstance(candidate, dict):
        raise PlannerMemoryError("CLEANUP_CANDIDATE_INVALID")
    if not candidate.get("retention_class") or not candidate.get("storage_policy_class"):
        raise PlannerMemoryError("CLEANUP_UNCLASSIFIED")
    _require_fields(candidate, CLEANUP_CANDIDATE_REQUIRED, "CLEANUP_CANDIDATE_FIELDS_MISSING")
    if candidate["action"] not in {"DELETE", "PRESERVE"}:
        raise PlannerMemoryError("CLEANUP_ACTION_INVALID")
    if candidate["resource_kind"] not in RESOURCE_KINDS:
        raise PlannerMemoryError("CLEANUP_RESOURCE_KIND_INVALID")
    if candidate["retention_class"] not in RETENTION_CLASSES:
        raise PlannerMemoryError("CLEANUP_UNCLASSIFIED")
    if candidate["storage_policy_class"] not in STORAGE_POLICY_CLASSES:
        raise PlannerMemoryError("CLEANUP_UNCLASSIFIED")


def validate_cleanup_manifest(
    manifest: dict[str, Any],
    *,
    expected_task_id: str | None = None,
    expected_control_epoch: int | None = None,
    started_manifest_digest: str | None = None,
) -> str:
    if not isinstance(manifest, dict):
        raise PlannerMemoryError("CLEANUP_MANIFEST_INVALID")
    _require_fields(manifest, CLEANUP_REQUIRED, "CLEANUP_MANIFEST_FIELDS_MISSING")
    if int(manifest.get("schema_version") or 0) != 1 or manifest.get("role") != "planner":
        raise PlannerMemoryError("CLEANUP_MANIFEST_SCHEMA_INVALID")
    task_id = _safe_task_id(str(manifest.get("task_id") or ""))
    if expected_task_id is not None and task_id != _safe_task_id(expected_task_id):
        raise PlannerMemoryError("CLEANUP_TASK_OWNERSHIP_MISMATCH")
    if expected_control_epoch is not None and int(manifest["control_epoch"]) != int(expected_control_epoch):
        raise PlannerMemoryError("CLEANUP_CONTROL_EPOCH_MISMATCH")
    if int(manifest.get("cleanup_generation") or 0) < 1:
        raise PlannerMemoryError("CLEANUP_GENERATION_INVALID")

    candidates = list(manifest.get("delete_candidates") or []) + list(
        manifest.get("preserve_candidates") or []
    )
    ids: set[str] = set()
    for candidate in candidates:
        validate_cleanup_candidate_schema(candidate)
        cid = str(candidate["candidate_id"])
        if cid in ids:
            raise PlannerMemoryError("CLEANUP_CANDIDATE_DUPLICATE", cid)
        ids.add(cid)
        if str(candidate.get("task_owner") or "") != task_id:
            raise PlannerMemoryError("CLEANUP_TASK_OWNERSHIP_MISMATCH", cid)
        if candidate["action"] == "DELETE" and candidate not in (manifest.get("delete_candidates") or []):
            raise PlannerMemoryError("CLEANUP_ACTION_LIST_MISMATCH")
        if candidate["action"] == "PRESERVE" and candidate not in (manifest.get("preserve_candidates") or []):
            raise PlannerMemoryError("CLEANUP_ACTION_LIST_MISMATCH")

    actual = cleanup_manifest_digest(manifest)
    if str(manifest.get("manifest_digest") or "") != actual:
        raise PlannerMemoryError("CLEANUP_MANIFEST_DIGEST_MISMATCH")
    if started_manifest_digest is not None and actual != started_manifest_digest:
        raise PlannerMemoryError("CLEANUP_MANIFEST_CHANGED")
    return actual


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def canonicalize_managed_target(path: str, root: str) -> tuple[Path, Path]:
    root_path = Path(root).expanduser().resolve(strict=False)
    target_path = Path(path).expanduser().resolve(strict=False)
    if not _path_is_within(target_path, root_path):
        raise PlannerMemoryError("CLEANUP_OUTSIDE_MANAGED_ROOT")
    return target_path, root_path


def _validate_git_path_for_task(path: str, task_id: str) -> str:
    normal = _normal_repo_path(path)
    allowed = (
        planner_memory_root(task_id) + "/",
        f"evidence/{task_id}/",
        f"cases/{task_id}/",
    )
    if not any(normal.startswith(prefix) for prefix in allowed):
        raise PlannerMemoryError("CLEANUP_TASK_OWNERSHIP_MISMATCH")
    return normal


def classify_cleanup_candidate(
    candidate: dict[str, Any],
    *,
    task_id: str,
    managed_roots: dict[str, str],
    protected_refs: Iterable[str] = (),
    rollback_window_refs: Iterable[str] = (),
    dependency_checks: dict[str, Any] | None = None,
    protected_conversation_ids: Iterable[str] = (),
) -> dict[str, Any]:
    validate_cleanup_candidate_schema(candidate)
    task_id = _safe_task_id(task_id)
    if str(candidate.get("task_owner") or "") != task_id:
        raise PlannerMemoryError("CLEANUP_TASK_OWNERSHIP_MISMATCH")
    retention = str(candidate["retention_class"])
    storage = str(candidate["storage_policy_class"])
    resource_kind = str(candidate["resource_kind"])
    identity = candidate["path_or_exact_resource_identity"]
    protected = {str(x) for x in protected_refs}
    rollback = {str(x) for x in rollback_window_refs}
    checks = dependency_checks or {}

    if storage == "SOURCE":
        return {"candidate_id": candidate["candidate_id"], "status": "PRESERVE_SOURCE"}
    if candidate["action"] == "PRESERVE":
        return {"candidate_id": candidate["candidate_id"], "status": "PRESERVE_DECLARED"}
    if retention == "AUDIT_FINAL":
        return {"candidate_id": candidate["candidate_id"], "status": "PRESERVE_AUDIT_FINAL"}
    if retention == "ROLLBACK_WINDOW":
        return {"candidate_id": candidate["candidate_id"], "status": "DEFER_ROLLBACK"}

    gate = candidate.get("delete_after_gate")
    if retention == "HANDOFF_ONLY" and not gate:
        return {
            "candidate_id": candidate["candidate_id"],
            "status": "DEFER_GATE",
            "gate": "verified_successor_takeover_or_final_delivery",
        }
    if gate and checks.get(str(gate)) is not True:
        return {"candidate_id": candidate["candidate_id"], "status": "DEFER_GATE", "gate": gate}

    if resource_kind == "TASK_CELL_CONVERSATION":
        if not isinstance(identity, dict):
            raise PlannerMemoryError("CLEANUP_CONVERSATION_IDENTITY_INVALID")
        project_key = str(identity.get("project_key") or "")
        conversation_id = str(identity.get("conversation_id") or "")
        expected_project = str(candidate.get("managed_root_id_or_task_cell_project_key") or "")
        if not project_key.startswith("g-p-") or project_key != expected_project or not conversation_id:
            raise PlannerMemoryError("CLEANUP_CONVERSATION_IDENTITY_INVALID")
        if conversation_id in {str(x) for x in protected_conversation_ids}:
            return {"candidate_id": candidate["candidate_id"], "status": "PRESERVE_PROTECTED"}
        digest = sha256_hex({"project_key": project_key, "conversation_id": conversation_id})
        expected = str(candidate.get("expected_blob_sha_or_identity_digest") or "")
        if expected and expected != digest:
            raise PlannerMemoryError("CLEANUP_IDENTITY_DIGEST_MISMATCH")
        return {
            "candidate_id": candidate["candidate_id"],
            "status": "WOULD_DELETE",
            "exact_identity": {"project_key": project_key, "conversation_id": conversation_id},
        }

    if resource_kind == "GIT_PATH":
        normal = _validate_git_path_for_task(str(identity), task_id)
        if normal in protected or normal in rollback:
            return {"candidate_id": candidate["candidate_id"], "status": "PRESERVE_PROTECTED"}
        return {"candidate_id": candidate["candidate_id"], "status": "WOULD_DELETE", "path": normal}

    if resource_kind in {"MANAGED_PATH", "RUNTIME_RECORD"}:
        root_id = str(candidate.get("managed_root_id_or_task_cell_project_key") or "")
        root = managed_roots.get(root_id)
        if not root:
            raise PlannerMemoryError("CLEANUP_MANAGED_ROOT_UNKNOWN", root_id)
        target, root_path = canonicalize_managed_target(str(identity), root)
        target_text = str(target)
        if target_text in protected or target_text in rollback:
            return {"candidate_id": candidate["candidate_id"], "status": "PRESERVE_PROTECTED"}
        if not target.exists():
            return {
                "candidate_id": candidate["candidate_id"],
                "status": "ALREADY_ABSENT",
                "path": target_text,
                "managed_root": str(root_path),
            }
        expected_identity = str(candidate.get("expected_blob_sha_or_identity_digest") or "")
        if expected_identity:
            if target.is_file():
                with target.open("rb") as stream:
                    actual_identity = hashlib.sha256(stream.read()).hexdigest()
            else:
                actual_identity = sha256_hex({"path": target_text})
            if actual_identity != expected_identity:
                raise PlannerMemoryError("CLEANUP_IDENTITY_DIGEST_MISMATCH")
        return {
            "candidate_id": candidate["candidate_id"],
            "status": "WOULD_DELETE",
            "path": target_text,
            "managed_root": str(root_path),
            "bytes": target.stat().st_size if target.is_file() else None,
        }

    raise PlannerMemoryError("CLEANUP_RESOURCE_KIND_INVALID")


def plan_cleanup_apply(
    manifest: dict[str, Any],
    *,
    managed_roots: dict[str, str],
    started_manifest_digest: str | None = None,
    protected_conversation_ids: Iterable[str] = (),
) -> dict[str, Any]:
    digest = validate_cleanup_manifest(
        manifest,
        expected_task_id=str(manifest.get("task_id") or ""),
        expected_control_epoch=int(manifest.get("control_epoch") or 0),
        started_manifest_digest=started_manifest_digest,
    )
    protected = list(manifest.get("protected_refs_snapshot") or [])
    rollback = list(manifest.get("rollback_window_refs") or [])
    checks = manifest.get("dependency_checks")
    if not isinstance(checks, dict):
        raise PlannerMemoryError("CLEANUP_DEPENDENCY_CHECKS_INVALID")

    results = []
    failures = []
    validated = []
    candidates = list(manifest.get("delete_candidates") or []) + list(
        manifest.get("preserve_candidates") or []
    )
    for candidate in candidates:
        try:
            planned = classify_cleanup_candidate(
                candidate,
                task_id=str(manifest["task_id"]),
                managed_roots=managed_roots,
                protected_refs=protected,
                rollback_window_refs=rollback,
                dependency_checks=checks,
                protected_conversation_ids=protected_conversation_ids,
            )
            validated.append(candidate["candidate_id"])
            results.append(planned)
        except PlannerMemoryError as exc:
            failures.append(exc.code)
            results.append({
                "candidate_id": candidate.get("candidate_id"),
                "status": "REJECTED",
                "error": exc.code,
            })

    already_absent = sum(1 for row in results if row.get("status") == "ALREADY_ABSENT")
    preserved = sum(1 for row in results if str(row.get("status") or "").startswith("PRESERVE"))
    deferred = sum(1 for row in results if row.get("status") in {"DEFER_ROLLBACK", "DEFER_GATE"})
    status = "DONE" if not failures else "CLEANUP_ERROR"
    return {
        "manifest_id": manifest["manifest_id"],
        "manifest_digest": digest,
        "cleanup_generation": manifest["cleanup_generation"],
        "task_id": manifest["task_id"],
        "control_epoch": manifest["control_epoch"],
        "validated_candidates": validated,
        "candidate_results": results,
        "deleted_bytes_if_applicable": 0,
        "already_absent_count": already_absent,
        "preserved_count": preserved,
        "deferred_rollback_count": deferred,
        "failure_codes": sorted(set(failures)),
        "status": status,
        "dry_run_only": True,
    }


def collapse_terminal_projection(
    planner_control: dict[str, Any],
    *,
    terminal_refs: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(planner_control)
    result["semantic_authority_closed"] = True
    result["activity"] = "DONE"
    result["wait"] = None
    result["inbox"] = {"events": {}, "active_doorbell": None}
    result["successor"] = {
        "state": "NONE",
        "handoff_id": None,
        "from_generation": None,
        "to_generation": None,
        "packet_ref": None,
        "candidate_conversation_id": None,
        "candidate_request_id": None,
        "candidate_challenge": None,
        "pending_fence_token": None,
    }
    result["terminal_refs"] = copy.deepcopy(terminal_refs)
    result.pop("runtime", None)
    result.pop("cleanup", None)
    result.pop("authorized_action", None)
    result.pop("replan", None)
    return result


def select_compact_audit_final(
    artifacts: Iterable[dict[str, Any]],
    *,
    max_audit_final_items: int = 16,
) -> dict[str, Any]:
    audit = []
    rollback = []
    rejected_raw = []
    for artifact in artifacts:
        retention = str(artifact.get("retention_class") or "")
        kind = str(artifact.get("kind") or "")
        if kind in {"RAW_CHAT", "RAW_LOG", "WORKER_SCRATCH", "REPRODUCIBLE_CACHE"}:
            rejected_raw.append(artifact.get("ref"))
            continue
        if retention == "AUDIT_FINAL":
            audit.append(copy.deepcopy(artifact))
        elif retention == "ROLLBACK_WINDOW" and artifact.get("rollback_live") is True:
            rollback.append(copy.deepcopy(artifact))
    if len(audit) > max_audit_final_items:
        raise PlannerMemoryError("CLEANUP_AUDIT_FINAL_NOT_COMPACT")
    return {
        "audit_final": audit,
        "rollback_window": rollback,
        "excluded_raw_continuity": rejected_raw,
    }


def bounded_growth_accounting(
    completed_tasks: Iterable[dict[str, Any]],
    *,
    max_audit_final_objects_per_task: int,
) -> dict[str, Any]:
    rows = list(completed_tasks)
    count = len(rows)
    audit_objects = sum(int(row.get("audit_final_objects") or 0) for row in rows)
    audit_bytes = sum(int(row.get("audit_final_bytes") or 0) for row in rows)
    rollback_objects = sum(int(row.get("rollback_objects") or 0) for row in rows)
    rollback_bytes = sum(int(row.get("rollback_bytes") or 0) for row in rows)
    raw_history_objects = sum(int(row.get("raw_history_objects") or 0) for row in rows)
    max_objects = count * int(max_audit_final_objects_per_task)
    return {
        "task_count": count,
        "audit_final_objects": audit_objects,
        "audit_final_bytes": audit_bytes,
        "rollback_objects": rollback_objects,
        "rollback_bytes": rollback_bytes,
        "raw_history_objects": raw_history_objects,
        "max_audit_final_objects": max_objects,
        "bounded": raw_history_objects == 0 and audit_objects <= max_objects,
    }
