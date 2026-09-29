#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from typing import Any


SLOT_COUNT = 5
TURN_OUTCOMES = {"CONTINUE", "COMPLETE", "WAIT", "NEED_USER", "ERROR"}
PLANNER_SLOT_ENTRY_TYPES = {
    "DIRECTION",
    "REVIEW",
    "REVIEW_DIRECTION",
    "ACCEPT",
    "REJECT",
    "RETIRE",
}


def _safe_id(value: Any, code: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        raise ValueError(code)
    return text


def planner_semantic_memory_path(task_id: str) -> str:
    return f"memory/planner/{_safe_id(task_id, 'PLANNER_TASK_ID_INVALID')}/memory.md"


def planner_plan_note_path(task_id: str) -> str:
    return f"memory/planner/{_safe_id(task_id, 'PLANNER_TASK_ID_INVALID')}/plan_note.md"


def worker_child_reply_path(child_task_id: str) -> str:
    return f"memory/worker/{_safe_id(child_task_id, 'WORKER_CHILD_ID_INVALID')}/reply.md"


def worker_turn_reply_entry_path(child_task_id: str, wake_id: str) -> str:
    child = _safe_id(child_task_id, "WORKER_CHILD_ID_INVALID")
    wake = _safe_id(wake_id, "WORKER_WAKE_ID_INVALID")
    return f"state/worker_turns/{child}/{wake}/reply-entry.md"


def worker_turn_reply_entry_header(wake_id: str) -> str:
    wake = _safe_id(wake_id, "WORKER_WAKE_ID_INVALID")
    return f"## {wake} · WORKER · WORK_RESULT\n\n"


def worker_reply_has_write(text: str, wake_id: str) -> bool:
    """Initialization metadata is not semantic work; no model bookkeeping."""
    return bool(str(text).strip().removeprefix(worker_turn_reply_entry_header(wake_id).strip()).strip())


def planner_turn_root(task_id: str, doorbell_id: str) -> str:
    task = _safe_id(task_id, "PLANNER_TASK_ID_INVALID")
    doorbell = _safe_id(doorbell_id, "PLANNER_DOORBELL_ID_INVALID")
    return f"state/planner_turns/{task}/{doorbell}"


def planner_turn_memory_entry_path(task_id: str, doorbell_id: str) -> str:
    return f"{planner_turn_root(task_id, doorbell_id)}/memory-entry.md"


def planner_turn_outcome_path(task_id: str, doorbell_id: str) -> str:
    return f"{planner_turn_root(task_id, doorbell_id)}/outcome.json"


def planner_turn_slot_path(task_id: str, doorbell_id: str, slot_index: int) -> str:
    slot = int(slot_index)
    if slot < 1:
        raise ValueError("PLANNER_SLOT_INDEX_INVALID")
    return f"{planner_turn_root(task_id, doorbell_id)}/worker-slot-{slot}.json"


def make_planner_turn_slot(
    *,
    task_id: str,
    control_epoch: int,
    doorbell_id: str,
    planner_generation: int,
    planner_fence_token: str,
    slot_index: int,
    bound_child_task_id: str | None = None,
    bound_child_reply_ref: str | None = None,
) -> dict[str, Any]:
    slot_path = planner_turn_slot_path(task_id, doorbell_id, slot_index)
    bound_child = (
        _safe_id(bound_child_task_id, "WORKER_CHILD_ID_INVALID")
        if bound_child_task_id
        else None
    )
    return {
        "v": 1,
        "task_id": _safe_id(task_id, "PLANNER_TASK_ID_INVALID"),
        "control_epoch": int(control_epoch),
        "doorbell_id": _safe_id(doorbell_id, "PLANNER_DOORBELL_ID_INVALID"),
        "planner_generation": int(planner_generation),
        "planner_fence_token": str(planner_fence_token),
        "slot_index": int(slot_index),
        "slot_ref": slot_path,
        "bound_child_task_id": bound_child,
        "bound_child_reply_ref": str(bound_child_reply_ref or "") or None,
        "entry_type": None,
        "semantic": None,
    }


def planner_slot_has_write(slot: dict[str, Any]) -> bool:
    return (
        isinstance(slot, dict)
        and str(slot.get("entry_type") or "") in PLANNER_SLOT_ENTRY_TYPES
        and slot.get("semantic") not in (None, "", {}, [])
    )


def make_planner_turn_outcome(
    *,
    task_id: str,
    control_epoch: int,
    doorbell_id: str,
    planner_generation: int,
    planner_fence_token: str,
) -> dict[str, Any]:
    return {
        "v": 1,
        "task_id": _safe_id(task_id, "PLANNER_TASK_ID_INVALID"),
        "control_epoch": int(control_epoch),
        "doorbell_id": _safe_id(doorbell_id, "PLANNER_DOORBELL_ID_INVALID"),
        "planner_generation": int(planner_generation),
        "planner_fence_token": str(planner_fence_token),
        "outcome": None,
        "semantic": None,
    }


def planner_turn_is_committed(outcome: dict[str, Any]) -> bool:
    return isinstance(outcome, dict) and str(outcome.get("outcome") or "") in TURN_OUTCOMES


def planner_turn_memory_entry_header(
    *,
    entry_id: str,
    planner_generation: int,
) -> str:
    return f"## {entry_id} · Planner G{int(planner_generation)}\n\n"


def render_planner_memory_entry(
    *,
    entry_id: str,
    planner_generation: int,
    semantic_text: str,
) -> str:
    return (
        f"\n## {entry_id} · Planner G{int(planner_generation)}\n\n"
        f"{str(semantic_text).rstrip()}\n"
    )


def render_child_reply_entry(
    *,
    entry_id: str,
    writer_role: str,
    entry_type: str,
    semantic: Any,
) -> str:
    role = str(writer_role or "").upper()
    kind = str(entry_type or "").upper()
    body = semantic if isinstance(semantic, str) else json.dumps(
        semantic,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"\n## {entry_id} · {role} · {kind}\n\n{body.rstrip()}\n"
