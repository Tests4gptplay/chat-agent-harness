#!/usr/bin/env python3
"""Deterministic finalizer for CAH Blender public case studies."""
from __future__ import annotations

import argparse
import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected object")
    return value


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe(value: str) -> Path:
    rel = Path(str(value))
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"unsafe repository path: {value}")
    out = (ROOT / rel).resolve()
    out.relative_to(ROOT.resolve())
    return out


def set_condition(cl: dict[str, Any], cid: str, state: str, detail: str, ref: str) -> None:
    for item in cl.get("conditions") or []:
        if isinstance(item, dict) and item.get("id") == cid:
            item["state"] = state
            item["detail"] = detail
            item["evidence_ref"] = ref
            return
    raise SystemExit(f"condition not found: {cid}")


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"not PNG: {path}")
    return struct.unpack(">II", data[16:24])


def validate(final: dict[str, Any], task: dict[str, Any], backend: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if int(final.get("v") or 0) != 1:
        errors.append("final.v must equal 1")
    if final.get("status") != "PASS":
        errors.append("final.status must be PASS")
    if final.get("task_id") != task.get("task_id"):
        errors.append("task_id mismatch")

    current = backend.get("dispatch")
    dispatch = final.get("dispatch")
    if not isinstance(current, dict) or not isinstance(dispatch, dict):
        errors.append("dispatch identity missing")
    else:
        if dispatch.get("dispatch_id") != current.get("dispatch_id"):
            errors.append("dispatch_id mismatch")
        if int(dispatch.get("generation") or 0) != int(current.get("generation") or 0):
            errors.append("dispatch generation mismatch")
        if dispatch.get("fence_token") != current.get("fence_token"):
            errors.append("dispatch fence mismatch")
        if current.get("state") not in {"READY", "ACKED", "RUNNING"}:
            errors.append(f"backend dispatch not finalizable from {current.get('state')}")

    count = int(final.get("iteration_count") or 0)
    selected = int(final.get("selected_iteration") or 0)
    if count < 1 or count > 5:
        errors.append("iteration_count must be 1..5")
    if selected < 1 or selected > count:
        errors.append("selected_iteration invalid")

    improvements = final.get("improvements")
    favorites = final.get("favorite_details")
    if not isinstance(improvements, list) or not [x for x in improvements if str(x).strip()]:
        errors.append("improvements required")
    if not isinstance(favorites, list) or not [x for x in favorites if str(x).strip()]:
        errors.append("favorite_details required")

    artifacts = final.get("final_artifacts")
    required_keys = ("render", "script", "blend", "manifest")
    if not isinstance(artifacts, dict):
        errors.append("final_artifacts required")
    else:
        for key in required_keys:
            ref = str(artifacts.get(key) or "")
            if not ref:
                errors.append(f"final_artifacts.{key} missing")
            elif not safe(ref).is_file():
                errors.append(f"final artifact missing: {ref}")

    if isinstance(artifacts, dict) and artifacts.get("render"):
        render = safe(str(artifacts["render"]))
        if render.is_file() and png_size(render) != (800, 800):
            errors.append("final render is not 800x800")

    refs = final.get("evidence_refs")
    if not isinstance(refs, list) or len([x for x in refs if isinstance(x, str) and x.strip()]) < 6:
        errors.append("at least six evidence_refs required")

    case_contract = task.get("case_contract") if isinstance(task.get("case_contract"), dict) else {}
    for required in (
        str(case_contract.get("original_prompt_ref") or ""),
        str(case_contract.get("public_notes_ref") or ""),
    ):
        if required and not safe(required).is_file():
            errors.append(f"case contract evidence missing: {required}")

    review_refs = final.get("iteration_reviews")
    if not isinstance(review_refs, list) or len(review_refs) != count:
        errors.append("one iteration review per iteration required")
    else:
        for ref in review_refs:
            if not isinstance(ref, str) or not safe(ref).is_file():
                errors.append(f"iteration review missing: {ref}")

    return errors


def finalize(path: Path) -> dict[str, Any]:
    final = load(path)
    task_id = str(final.get("task_id") or "")
    if not task_id:
        raise SystemExit("task_id required")
    task = load(safe(f"tasks/{task_id}.json"))
    if task.get("kind") != "blender_public_case":
        raise SystemExit("task is not blender_public_case")

    contract = task.get("execution_contract") or {}
    bg_path = safe(str(contract.get("backend_cl") or ""))
    fg_path = safe(str(contract.get("foreground_cl") or ""))
    state_path = ROOT / "state" / "chatgpt.json"
    bg, fg, state = load(bg_path), load(fg_path), load(state_path)

    if state.get("active_task") != task_id:
        raise SystemExit(f"stale final result: active_task={state.get('active_task')!r}")
    bg_ref = bg_path.relative_to(ROOT).as_posix()
    if state.get("active_dispatch_ref") not in (None, "", bg_ref):
        raise SystemExit("active_dispatch_ref mismatch")

    errors = validate(final, task, bg)
    final_ref = path.relative_to(ROOT).as_posix()
    ts = now()
    if errors:
        summary = "; ".join(errors)
        bg["overall"] = "ERROR"
        fg["overall"] = "ERROR"
        bg["error"] = {"kind": "blender_case_final_invalid", "summary": summary, "evidence_ref": final_ref}
        fg["error"] = dict(bg["error"])
        if isinstance(bg.get("dispatch"), dict):
            bg["dispatch"]["state"] = "ERROR"
        set_condition(bg, "verification", "ERROR", summary, final_ref)
        set_condition(bg, "terminal", "ERROR", summary, final_ref)
        set_condition(fg, "verification", "ERROR", summary, final_ref)
        set_condition(fg, "final_acceptance", "ERROR", summary, final_ref)
        state["phase"] = "BLOCKED"
        state["fault_boundary"] = summary
        state["next_action"] = "Inspect Blender case final-result validation failure."
        outcome = "ERROR"
    else:
        if isinstance(bg.get("dispatch"), dict):
            bg["dispatch"]["state"] = "DONE"
        bg["overall"] = "GREEN"
        bg["result_ref"] = final_ref
        bg["error"] = None
        fg["overall"] = "GREEN"
        fg["result_ref"] = final_ref
        fg["error"] = None
        fg["visual"] = "[○ ● ● ● ● ●]"
        if isinstance(fg.get("supervisor_guard"), dict):
            fg["supervisor_guard"]["state"] = "RELEASED"
            fg["supervisor_guard"]["detail"] = "Blender case evidence package accepted"
        set_condition(bg, "verification", "GREEN", "final Blender case artifact validated", final_ref)
        set_condition(bg, "terminal", "GREEN", "Blender case completion predicate satisfied", final_ref)
        set_condition(fg, "harness_claimed", "GREEN", "CAH Blender case executed through managed lane", final_ref)
        set_condition(fg, "backend_execution", "GREEN", "local Blender iterations complete", final_ref)
        set_condition(fg, "durable_result", "GREEN", "public-case evidence package durable", final_ref)
        set_condition(fg, "verification", "GREEN", "final evidence validated", final_ref)
        set_condition(fg, "final_acceptance", "GREEN", "foreground case acceptance released", final_ref)
        state["phase"] = "DONE"
        state["last_result"] = final_ref
        state["active_task"] = None
        state["active_action"] = None
        state["active_dispatch_ref"] = None
        state["pending_wake_id"] = None
        state["handoff_packet_ref"] = None
        state["next_reads"] = []
        state["next_action"] = None
        state["fault_boundary"] = "none"
        state["writeback_reason"] = f"Blender public case {task_id} completed with durable iterative visual evidence."
        current = state.get("current")
        if isinstance(current, list):
            proof = (
                f"{task_id} completed as a CAH local-Blender public case: Worker-authored bpy, "
                "stage-by-stage durable visual evidence, iterative visual review, and final render/script/blend promotion."
            )
            if proof not in current:
                current.append(proof)
        outcome = "PASS"

    bg["updated_at"] = ts
    fg["updated_at"] = ts
    state["updated"] = ts[:10]
    write(bg_path, bg)
    write(fg_path, fg)
    write(state_path, state)
    return {"task_id": task_id, "outcome": outcome, "errors": errors, "final_ref": final_ref}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--final-result", required=True)
    args = p.parse_args()
    out = finalize(safe(args.final_result))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
