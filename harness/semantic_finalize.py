#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUSINESS_OUTCOMES = {"PASS", "ERROR", "BLOCKED"}
FINALIZABLE_DISPATCH_STATES = {"READY", "ACKED", "RUNNING"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_repo_path(root: Path, value: str) -> Path:
    rel = Path(str(value))
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"unsafe repository path: {value}")
    out = (root / rel).resolve()
    out.relative_to(root.resolve())
    return out


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


def set_condition(cl: dict[str, Any], cid: str, state: str, detail: str, ref: str) -> None:
    for item in cl.get("conditions") or []:
        if isinstance(item, dict) and item.get("id") == cid:
            item["state"] = state
            item["detail"] = detail
            item["evidence_ref"] = ref
            return


def _nonempty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if isinstance(x, str) and str(x).strip()]


def canonical_participant_label(value: str) -> str | None:
    raw = str(value or "").strip()
    lower = raw.lower()
    aliases = (
        ("deepseek", "DeepSeek"),
        ("minimax", "MiniMax"),
        ("xiaomi", "Xiaomi"),
        ("mimo", "Xiaomi"),
        ("doubao", "Doubao"),
        ("glm", "GLM"),
        ("kimi", "Kimi"),
        ("qwen", "Qwen"),
    )
    for needle, canonical in aliases:
        if needle in lower:
            return canonical
    return None


def _dispatch_identity_errors(
    analysis: dict[str, Any],
    task: dict[str, Any],
    backend: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if int(analysis.get("v") or 0) != 1:
        errors.append("analysis.v must equal 1")
    status = str(analysis.get("status") or "")
    if status not in BUSINESS_OUTCOMES:
        errors.append(f"unsupported semantic status={status!r}")

    task_id = str(task.get("task_id") or "")
    if str(analysis.get("task_id") or "") != task_id:
        errors.append("analysis task_id mismatch")
    if str(backend.get("task_id") or "") != task_id:
        errors.append("backend task_id mismatch")

    dispatch = analysis.get("dispatch")
    current = backend.get("dispatch")
    if not isinstance(dispatch, dict) or not isinstance(current, dict):
        errors.append("analysis/backend dispatch missing")
        return errors

    if str(dispatch.get("dispatch_id") or "") != str(current.get("dispatch_id") or ""):
        errors.append("stale dispatch_id")
    try:
        analysis_generation = int(dispatch.get("generation"))
        current_generation = int(current.get("generation"))
    except (TypeError, ValueError):
        errors.append("dispatch generation missing or invalid")
    else:
        if analysis_generation != current_generation:
            errors.append("stale dispatch generation")
    if str(dispatch.get("fence_token") or "") != str(current.get("fence_token") or ""):
        errors.append("stale dispatch fence")
    return errors


def _canonical_activity_errors(
    state: dict[str, Any],
    task_id: str,
    backend_ref: str,
) -> list[str]:
    errors: list[str] = []
    if str(state.get("active_task") or "") != task_id:
        errors.append(
            f"stale active_task={state.get('active_task')!r} analysis_task={task_id!r}"
        )
    active_dispatch_ref = str(state.get("active_dispatch_ref") or "")
    if active_dispatch_ref and active_dispatch_ref != backend_ref:
        errors.append(
            f"stale active_dispatch_ref={active_dispatch_ref!r} backend_ref={backend_ref!r}"
        )
    return errors


def _rejected_result(
    *,
    task_id: str,
    analysis_ref: str,
    backend_ref: str,
    foreground_ref: str,
    errors: list[str],
    rejection_kind: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "analysis_ref": analysis_ref,
        "outcome": "REJECTED",
        "projected": False,
        "idempotent": False,
        "rejection_kind": rejection_kind,
        "errors": errors,
        "backend_cl": backend_ref,
        "foreground_cl": foreground_ref,
        "state": "state/chatgpt.json",
    }


def _is_idempotent_projection(
    *,
    status: str,
    analysis_ref: str,
    backend: dict[str, Any],
    foreground: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    expected_overall = "GREEN" if status == "PASS" else status
    expected_dispatch_state = "DONE" if status == "PASS" else status
    dispatch = backend.get("dispatch")
    return (
        isinstance(dispatch, dict)
        and str(dispatch.get("state") or "") == expected_dispatch_state
        and str(backend.get("overall") or "") == expected_overall
        and str(backend.get("result_ref") or "") == analysis_ref
        and str(foreground.get("overall") or "") == expected_overall
        and str(foreground.get("result_ref") or "") == analysis_ref
        and str(state.get("last_result") or "") == analysis_ref
    )


def _load_json_object_for_validation(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        errors.append(f"{label} missing: {path.name}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} malformed JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object")
        return None
    return value


def validate_capture_prerequisite(
    task: dict[str, Any],
    root: Path,
) -> list[str]:
    errors: list[str] = []
    task_id = str(task.get("task_id") or "")
    contract = task.get("execution_contract")
    deterministic = task.get("deterministic_prelude")
    if not isinstance(contract, dict):
        return ["task execution_contract missing"]
    if not isinstance(deterministic, dict):
        return ["task deterministic_prelude missing"]

    capture_ref = str(contract.get("capture_result") or "").strip()
    declared_result_ref = str(deterministic.get("result_path") or "").strip()
    if not capture_ref:
        return ["task capture_result missing"]
    if declared_result_ref and declared_result_ref != capture_ref:
        errors.append(
            f"capture result contract mismatch: execution_contract={capture_ref} deterministic_prelude={declared_result_ref}"
        )

    capture_path = safe_repo_path(root, capture_ref)
    capture = _load_json_object_for_validation(capture_path, "capture result", errors)
    if capture is None:
        return errors

    if int(capture.get("v") or 0) != 1:
        errors.append("capture result v must equal 1")
    if str(capture.get("status") or "") != "PASS":
        errors.append(f"capture result status must be PASS, got {capture.get('status')!r}")
    if str(capture.get("task_id") or "") != task_id:
        errors.append("capture result task_id mismatch")

    action_ref = str(deterministic.get("action_path") or "").strip()
    if action_ref:
        action_path = safe_repo_path(root, action_ref)
        action = _load_json_object_for_validation(action_path, "capture action", errors)
        if action is not None:
            if str(action.get("task_id") or "") != task_id:
                errors.append("capture action task_id mismatch")
            expected_action_id = str(action.get("action_id") or "").strip()
            if not expected_action_id:
                errors.append("capture action action_id missing")
            elif str(capture.get("action_id") or "") != expected_action_id:
                errors.append("capture result action_id mismatch")
    return errors


def validate_pass_analysis(
    analysis: dict[str, Any],
    task: dict[str, Any],
    analysis_ref: str,
    root: Path,
) -> list[str]:
    errors: list[str] = []
    if int(analysis.get("v") or 0) != 1:
        errors.append("analysis.v must equal 1")
    if analysis.get("status") != "PASS":
        errors.append("analysis.status must be PASS")
    task_id = str(task.get("task_id") or "")
    if str(analysis.get("task_id") or "") != task_id:
        errors.append("analysis task_id mismatch")

    participants = _nonempty_strings(analysis.get("participants"))
    expected_participants = {"DeepSeek", "MiniMax", "Xiaomi", "Doubao", "GLM", "Kimi", "Qwen"}
    canonical_participants = [canonical_participant_label(item) for item in participants]
    unknown_participants = [
        participants[idx]
        for idx, item in enumerate(canonical_participants)
        if item is None
    ]
    normalized_participants = [item for item in canonical_participants if item is not None]
    if unknown_participants:
        errors.append("unknown participant label(s): " + ", ".join(unknown_participants))
    if len(normalized_participants) != 7 or set(normalized_participants) != expected_participants:
        errors.append("participants must normalize to exactly the seven expected review participants")

    groups = analysis.get("groups")
    if not isinstance(groups, dict) or len(groups) < 2:
        errors.append("groups must contain at least two review groups")
    tasks = _nonempty_strings(analysis.get("tasks"))
    if len(tasks) < 2:
        errors.append("at least two modeling tasks required")
    dimensions = _nonempty_strings(analysis.get("evaluation_dimensions"))
    if len(dimensions) < 4:
        errors.append("at least four evaluation dimensions required")

    quality = analysis.get("quality_findings")
    if not isinstance(quality, list) or len(quality) < 3:
        errors.append("at least three quality findings required")
    else:
        allowed = {"author_claim", "evidence_fact", "worker_inference"}
        for idx, item in enumerate(quality):
            if not isinstance(item, dict):
                errors.append(f"quality_findings[{idx}] must be object")
                continue
            if str(item.get("provenance") or "") not in allowed:
                errors.append(f"quality_findings[{idx}] provenance invalid")
            if not _nonempty_strings(item.get("evidence_refs")):
                errors.append(f"quality_findings[{idx}] requires evidence_refs")

    timings = analysis.get("timing_findings")
    if not isinstance(timings, list) or len(timings) < 2:
        errors.append("at least two timing findings required")
    basis = analysis.get("deepseek_basis")
    if not isinstance(basis, dict):
        errors.append("deepseek_basis required")
    else:
        if len(_nonempty_strings(basis.get("strengths"))) < 3:
            errors.append("deepseek_basis requires at least three strengths")
        if len(_nonempty_strings(basis.get("tradeoffs"))) < 1:
            errors.append("deepseek_basis requires at least one tradeoff")
    limitations = _nonempty_strings(analysis.get("methodology_limitations"))
    if len(limitations) < 3:
        errors.append("at least three methodology limitations required")

    refs = set(_nonempty_strings(analysis.get("evidence_refs")))
    contract = task.get("execution_contract") if isinstance(task.get("execution_contract"), dict) else {}
    capture_ref = str(contract.get("capture_result") or "")
    deterministic = task.get("deterministic_prelude") if isinstance(task.get("deterministic_prelude"), dict) else {}
    artifact_ref = str(deterministic.get("artifact_path") or "")
    for required in (capture_ref, artifact_ref):
        if required and required not in refs:
            errors.append(f"missing evidence ref: {required}")
        if required and not safe_repo_path(root, required).exists():
            errors.append(f"evidence ref not present in checkout: {required}")

    errors.extend(validate_capture_prerequisite(task, root))

    if not str(analysis.get("summary") or "").strip():
        errors.append("summary required")
    if analysis_ref in refs:
        errors.append("analysis must not cite itself as source evidence")
    return errors


def finalize(analysis_path: Path, root: Path = ROOT) -> dict[str, Any]:
    analysis = load(analysis_path)
    task_id = str(analysis.get("task_id") or "").strip()
    if not task_id:
        raise SystemExit("analysis.task_id required")
    task_path = safe_repo_path(root, f"tasks/{task_id}.json")
    task = load(task_path)
    if str(task.get("task_id") or "") != task_id:
        raise SystemExit("task identity mismatch")
    if str(task.get("kind") or "") != "zhihu_review_one_shot":
        raise SystemExit("semantic finalizer currently accepts zhihu_review_one_shot only")

    contract = task.get("execution_contract")
    if not isinstance(contract, dict):
        raise SystemExit("task execution_contract missing")
    backend_path = safe_repo_path(root, str(contract.get("backend_cl") or ""))
    foreground_path = safe_repo_path(root, str(contract.get("foreground_cl") or ""))
    state_path = root / "state" / "chatgpt.json"
    backend = load(backend_path)
    foreground = load(foreground_path)
    state = load(state_path)

    backend_ref = backend_path.resolve().relative_to(root.resolve()).as_posix()
    foreground_ref = foreground_path.resolve().relative_to(root.resolve()).as_posix()
    analysis_ref = analysis_path.resolve().relative_to(root.resolve()).as_posix()
    status = str(analysis.get("status") or "")

    identity_errors = _dispatch_identity_errors(analysis, task, backend)
    if identity_errors:
        return _rejected_result(
            task_id=task_id,
            analysis_ref=analysis_ref,
            backend_ref=backend_ref,
            foreground_ref=foreground_ref,
            errors=identity_errors,
            rejection_kind="stale_or_invalid_identity",
        )

    if _is_idempotent_projection(
        status=status,
        analysis_ref=analysis_ref,
        backend=backend,
        foreground=foreground,
        state=state,
    ):
        return {
            "task_id": task_id,
            "analysis_ref": analysis_ref,
            "outcome": status,
            "projected": False,
            "idempotent": True,
            "errors": [],
            "backend_cl": backend_ref,
            "foreground_cl": foreground_ref,
            "state": "state/chatgpt.json",
        }

    activity_errors = _canonical_activity_errors(state, task_id, backend_ref)
    if activity_errors:
        return _rejected_result(
            task_id=task_id,
            analysis_ref=analysis_ref,
            backend_ref=backend_ref,
            foreground_ref=foreground_ref,
            errors=activity_errors,
            rejection_kind="stale_canonical_activity",
        )

    current_dispatch = backend.get("dispatch")
    assert isinstance(current_dispatch, dict)
    current_dispatch_state = str(current_dispatch.get("state") or "")
    if current_dispatch_state not in FINALIZABLE_DISPATCH_STATES:
        return _rejected_result(
            task_id=task_id,
            analysis_ref=analysis_ref,
            backend_ref=backend_ref,
            foreground_ref=foreground_ref,
            errors=[f"backend dispatch not finalizable from state={current_dispatch_state}"],
            rejection_kind="unsafe_state_transition",
        )

    if status == "PASS":
        errors = validate_pass_analysis(analysis, task, analysis_ref, root)
        if errors:
            return _rejected_result(
                task_id=task_id,
                analysis_ref=analysis_ref,
                backend_ref=backend_ref,
                foreground_ref=foreground_ref,
                errors=errors,
                rejection_kind="invalid_semantic_artifact",
            )

    now = utc_now()
    if status == "PASS":
        current_dispatch["state"] = "DONE"
        backend["dispatch"] = current_dispatch
        backend["overall"] = "GREEN"
        backend["result_ref"] = analysis_ref
        backend["error"] = None
        backend["updated_at"] = now
        set_condition(backend, "verification", "GREEN", "semantic analysis artifact validated", analysis_ref)
        set_condition(backend, "terminal", "GREEN", "semantic completion predicate satisfied", analysis_ref)

        foreground["overall"] = "GREEN"
        foreground["result_ref"] = analysis_ref
        foreground["updated_at"] = now
        foreground["error"] = None
        foreground["visual"] = "[○ ● ● ● ● ●]"
        if isinstance(foreground.get("supervisor_guard"), dict):
            foreground["supervisor_guard"]["state"] = "RELEASED"
            foreground["supervisor_guard"]["detail"] = "durable semantic analysis accepted"
        set_condition(foreground, "harness_claimed", "GREEN", "deterministic capture + semantic dispatch completed", analysis_ref)
        set_condition(foreground, "backend_execution", "GREEN", "fresh Camoufox capture PASS", str(contract.get("capture_result") or ""))
        set_condition(foreground, "durable_result", "GREEN", "fresh structured evidence durable", str(contract.get("capture_result") or ""))
        set_condition(foreground, "verification", "GREEN", "semantic analysis validated", analysis_ref)
        set_condition(foreground, "final_acceptance", "GREEN", "one-shot task completion accepted", analysis_ref)

        state["phase"] = "DONE"
        state["last_result"] = analysis_ref
        state["active_task"] = None
        state["active_action"] = None
        state["pending_wake_id"] = None
        state["active_dispatch_ref"] = None
        state["handoff_packet_ref"] = None
        state["next_reads"] = []
        state["next_action"] = None
        state["fault_boundary"] = "none"
        state["writeback_reason"] = (
            f"One-shot semantic finalizer accepted {analysis_ref}; "
            "fresh deterministic capture and semantic reduction completed."
        )
        state["updated"] = now[:10]
        verified = state.get("verified")
        if isinstance(verified, list):
            proof = (
                f"{task_id} completed one-shot: deterministic Camoufox capture -> "
                "runtime-admitted semantic dispatch -> one durable analysis artifact -> deterministic terminal finalize."
            )
            if proof not in verified:
                verified.append(proof)
        outcome = "PASS"
    else:
        summary = str(analysis.get("summary") or f"semantic analysis status={status}")
        backend["overall"] = status
        backend["result_ref"] = analysis_ref
        backend["updated_at"] = now
        backend["error"] = {
            "kind": "semantic_business_outcome",
            "status": status,
            "summary": summary,
            "evidence_ref": analysis_ref,
        }
        current_dispatch["state"] = status
        backend["dispatch"] = current_dispatch
        set_condition(backend, "verification", status, summary, analysis_ref)
        set_condition(backend, "terminal", status, summary, analysis_ref)

        foreground["overall"] = status
        foreground["result_ref"] = analysis_ref
        foreground["updated_at"] = now
        foreground["error"] = dict(backend["error"])
        set_condition(foreground, "verification", status, summary, analysis_ref)
        set_condition(foreground, "final_acceptance", status, summary, analysis_ref)

        state["phase"] = "BLOCKED" if status == "BLOCKED" else "DONE"
        state["last_result"] = analysis_ref
        state["pending_wake_id"] = None
        state["fault_boundary"] = summary
        state["next_action"] = "Inspect semantic business outcome; do not silently claim one-shot success."
        state["writeback_reason"] = f"Semantic task reported {status} in {analysis_ref}: {summary}"
        state["updated"] = now[:10]
        outcome = status

    write(backend_path, backend)
    write(foreground_path, foreground)
    write(state_path, state)
    return {
        "task_id": task_id,
        "analysis_ref": analysis_ref,
        "outcome": outcome,
        "projected": True,
        "idempotent": False,
        "errors": [],
        "backend_cl": backend_ref,
        "foreground_cl": foreground_ref,
        "state": "state/chatgpt.json",
    }


def exit_code_for_outcome(outcome: str) -> int:
    return 0 if outcome in BUSINESS_OUTCOMES else 2


def main() -> int:
    p = argparse.ArgumentParser(description="Finalize one durable semantic result into canonical CAH CL/state")
    p.add_argument("--analysis", required=True)
    args = p.parse_args()
    path = safe_repo_path(ROOT, args.analysis)
    out = finalize(path)
    print(json.dumps(out, ensure_ascii=False))
    return exit_code_for_outcome(str(out.get("outcome") or ""))


if __name__ == "__main__":
    raise SystemExit(main())
