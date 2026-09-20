#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .resource_wait import enter_resource_wait
except ImportError:
    from resource_wait import enter_resource_wait

ROOT = Path(__file__).resolve().parents[1]
SAFE_EXECUTOR = re.compile(r"^[A-Za-z0-9_.-]+$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe_repo_path(root: Path, value: str, label: str) -> Path:
    rel = Path(str(value))
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"{label}: must be a safe repository-relative path")
    resolved = (root / rel).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"{label}: path escapes repository root") from exc
    return resolved


def condition(cl: dict[str, Any], cond_id: str) -> dict[str, Any]:
    matches = [x for x in cl.get("conditions", []) if isinstance(x, dict) and x.get("id") == cond_id]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one CL condition {cond_id!r}, found {len(matches)}")
    return matches[0]


def set_condition(cl: dict[str, Any], cond_id: str, state: str, detail: str, evidence_ref: str | None = None) -> None:
    item = condition(cl, cond_id)
    item["state"] = state
    item["detail"] = detail
    if evidence_ref is not None:
        item["evidence_ref"] = evidence_ref
    cl["updated_at"] = now()


def optional_condition(cl: dict[str, Any], cond_id: str) -> dict[str, Any] | None:
    matches = [x for x in cl.get("conditions", []) if isinstance(x, dict) and x.get("id") == cond_id]
    if len(matches) > 1:
        raise SystemExit(f"expected at most one CL condition {cond_id!r}, found {len(matches)}")
    return matches[0] if matches else None


def set_condition_if_present(
    cl: dict[str, Any],
    cond_id: str,
    state: str,
    detail: str,
    evidence_ref: str | None = None,
) -> bool:
    item = optional_condition(cl, cond_id)
    if item is None:
        return False
    item["state"] = state
    item["detail"] = detail
    if evidence_ref is not None:
        item["evidence_ref"] = evidence_ref
    cl["updated_at"] = now()
    return True


def semantic_branch_mode(action: dict[str, Any], bg: dict[str, Any]) -> bool:
    if not bool(action.get("worker_continuation")):
        return False
    ids = {
        str(x.get("id") or "")
        for x in (bg.get("conditions") or [])
        if isinstance(x, dict)
    }
    return {"semantic_work", "deterministic_execution", "branch_output", "barrier_acceptance"}.issubset(ids)


def foreground_lane_condition_id(action: dict[str, Any]) -> str | None:
    lane = str(action.get("lane_id") or "")
    if lane.startswith("lane-"):
        suffix = lane.split("-", 1)[1]
        if suffix.isdigit():
            return f"lane{suffix}"
    return None





def host_config_need(result: dict[str, Any]) -> dict[str, str] | None:
    if str(result.get("status") or "") != "BLOCKED":
        return None
    if str(result.get("fault_boundary") or "") != "NEED_HOST_CONFIG":
        return None
    values: dict[str, str] = {}
    for item in result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("type") or "")
        if key in {"capability_id", "capability_projection_ref", "user_prompt"}:
            value = str(item.get("value") or "").strip()
            if value:
                values[key] = value
    if not values.get("capability_id") or not values.get("user_prompt"):
        return None
    return values

def result_identity_error(action: dict[str, Any], result: dict[str, Any]) -> str | None:
    checks = (
        ("action_id", action.get("action_id")),
        ("task_id", action.get("task_id")),
        ("round", action.get("round")),
    )
    bad = [f"{key} expected {expected!r} got {result.get(key)!r}" for key, expected in checks if result.get(key) != expected]
    return "; ".join(bad) if bad else None


class SingleThreadRuntime:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _load_bundle(self, action_path: Path) -> tuple[dict[str, Any], Path, dict[str, Any], Path, dict[str, Any]]:
        action = load_json(action_path)
        for key in ("task_id", "executor", "foreground_cl", "backend_cl"):
            if not action.get(key):
                raise SystemExit(f"action missing {key}")
        fg_path = safe_repo_path(self.root, str(action["foreground_cl"]), "foreground_cl")
        bg_path = safe_repo_path(self.root, str(action["backend_cl"]), "backend_cl")
        fg = load_json(fg_path)
        bg = load_json(bg_path)
        task_id = str(action["task_id"])
        if bg.get("task_id") != task_id:
            raise SystemExit("task_id mismatch between action and backend CL")
        fg_task_id = str(fg.get("task_id") or "")
        if fg_task_id != task_id:
            parent_hint = str((action.get("payload") or {}).get("parent_task_id") or "")
            child_ok = bool(action.get("worker_continuation")) and (
                parent_hint == fg_task_id
                or (fg_task_id and task_id.startswith(f"{fg_task_id}-"))
            )
            if not child_ok:
                raise SystemExit("task_id mismatch between action and foreground CL")
        if fg.get("scope") != "foreground_supervision":
            raise SystemExit("foreground CL has wrong scope")
        if bg.get("scope") != "backend_execution":
            raise SystemExit("backend CL has wrong scope")
        return action, fg_path, fg, bg_path, bg

    def claim(self, action_path: Path) -> dict[str, Any]:
        action, fg_path, fg, bg_path, bg = self._load_bundle(action_path)
        bg["overall"] = "RUNNING"
        if semantic_branch_mode(action, bg):
            # The semantic Worker already owns/ACKed the lane dispatch. Stage 0 is
            # a nested deterministic syscall, not a second semantic claim.
            set_condition_if_present(
                bg,
                "deterministic_execution",
                "RUNNING",
                f"deterministic executor {action['executor']} claimed action {action['action_id']}",
                str(action_path.resolve().relative_to(self.root)).replace("\\", "/"),
            )
            lane_cond = foreground_lane_condition_id(action)
            if lane_cond:
                set_condition_if_present(
                    fg,
                    lane_cond,
                    "RUNNING",
                    f"semantic lane waiting on deterministic action {action['action_id']}",
                    str(action_path.resolve().relative_to(self.root)).replace("\\", "/"),
                )
            fg["overall"] = "RUNNING"
        else:
            set_condition(bg, "claimed", "GREEN", "single-thread Harness claimed task")
            set_condition(bg, "executor", "RUNNING", "executor dispatch started")
            set_condition(fg, "harness_claimed", "GREEN", "Harness accepted backend execution")
            set_condition(fg, "backend_execution", "RUNNING", "backend executor running")
        write_json(bg_path, bg)
        write_json(fg_path, fg)
        return {"phase": "claim", "task_id": action["task_id"], "backend": bg["overall"]}

    def execute(self, action_path: Path, result_path: Path) -> dict[str, Any]:
        action, fg_path, fg, bg_path, bg = self._load_bundle(action_path)
        executor = str(action["executor"])
        if not SAFE_EXECUTOR.fullmatch(executor):
            raise SystemExit("unsafe executor name")
        executor_path = (self.root / "executors" / f"{executor}.py").resolve()
        if not executor_path.exists() or executor_path.parent != (self.root / "executors").resolve():
            raise SystemExit(f"executor not found: {executor}")

        timeout_seconds = int(action.get("timeout_seconds") or 600)
        if timeout_seconds < 1 or timeout_seconds > 21600:
            raise SystemExit("timeout_seconds must be between 1 and 21600")

        result_path = result_path.resolve()
        try:
            result_path.relative_to(self.root)
        except ValueError as exc:
            raise SystemExit("result path must remain inside repository") from exc

        timed_out = False
        return_code = None
        try:
            completed = subprocess.run(
                [sys.executable, str(executor_path), "--action", str(action_path), "--result", str(result_path)],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True

        if timed_out:
            result = {
                "v": 1,
                "result_id": f"result-{action['action_id']}",
                "action_id": action["action_id"],
                "task_id": action["task_id"],
                "round": action["round"],
                "status": "ERROR",
                "summary": f"executor timed out after {timeout_seconds}s",
                "evidence": [{"type": "timeout_seconds", "value": timeout_seconds}],
                "artifacts": [],
                "fault_boundary": "executor_timeout"
            }
            write_json(result_path, result)
        elif result_path.exists():
            result = load_json(result_path)
            identity_error = result_identity_error(action, result)
            if identity_error:
                result = {
                    "v": 1,
                    "result_id": f"result-{action['action_id']}",
                    "action_id": action["action_id"],
                    "task_id": action["task_id"],
                    "round": action["round"],
                    "status": "ERROR",
                    "summary": f"executor result identity mismatch: {identity_error}",
                    "evidence": [{"type": "return_code", "value": return_code}],
                    "artifacts": [],
                    "fault_boundary": "executor_result_identity"
                }
                write_json(result_path, result)
        else:
            result = {
                "v": 1,
                "result_id": f"result-{action['action_id']}",
                "action_id": action["action_id"],
                "task_id": action["task_id"],
                "round": action["round"],
                "status": "ERROR",
                "summary": f"executor exited {return_code} without durable result",
                "evidence": [{"type": "return_code", "value": return_code}],
                "artifacts": [],
                "fault_boundary": "executor_result_missing"
            }
            write_json(result_path, result)

        status = str(result.get("status") or "ERROR")
        result_ref = str(result_path.relative_to(self.root)).replace("\\", "/")

        if semantic_branch_mode(action, bg):
            # Preserve the semantic lane CL shape. Deterministic results normally
            # return to the Worker, except typed host-config needs which release
            # reasoning capacity into WAIT_RESOURCE instead of terminal ERROR.
            bg["result_ref"] = result_ref
            summary = str(result.get("summary") or f"executor status {status}")
            need = host_config_need(result)
            if need:
                set_condition_if_present(bg, "deterministic_execution", "BLOCKED", summary, result_ref)
                set_condition_if_present(
                    bg,
                    "semantic_work",
                    "WAIT",
                    need["user_prompt"],
                    result_ref,
                )
                lane_cond = foreground_lane_condition_id(action)
                if lane_cond:
                    set_condition_if_present(
                        fg,
                        lane_cond,
                        "WAIT",
                        need["user_prompt"],
                        result_ref,
                    )
                bg["overall"] = "RUNNING"
                fg["overall"] = "RUNNING"
                fg["error"] = None
                write_json(bg_path, bg)
                write_json(fg_path, fg)
                wait = enter_resource_wait(
                    bg_path,
                    capability_id=need["capability_id"],
                    prompt=need["user_prompt"],
                    projection_ref=need.get("capability_projection_ref"),
                )
                return {
                    "phase": "execute",
                    "task_id": action["task_id"],
                    "result_status": status,
                    "result_ref": result_ref,
                    "semantic_branch": True,
                    "resource_wait": wait,
                }

            state = "GREEN" if status == "PASS" else ("BLOCKED" if status == "BLOCKED" else "ERROR")
            set_condition_if_present(bg, "deterministic_execution", state, summary, result_ref)
            set_condition_if_present(
                bg,
                "semantic_work",
                "RUNNING",
                f"deterministic result {status} is durable; semantic continuation pending",
                result_ref,
            )
            lane_cond = foreground_lane_condition_id(action)
            if lane_cond:
                set_condition_if_present(
                    fg,
                    lane_cond,
                    "RUNNING",
                    f"deterministic action {action['action_id']} returned {status}; semantic continuation pending",
                    result_ref,
                )
            bg["overall"] = "RUNNING"
            fg["overall"] = "RUNNING"
            write_json(bg_path, bg)
            write_json(fg_path, fg)
            return {
                "phase": "execute",
                "task_id": action["task_id"],
                "result_status": status,
                "result_ref": result_ref,
                "semantic_branch": True,
            }
        set_condition(bg, "durable_result", "GREEN", f"durable result written: {result_ref}", result_ref)
        set_condition(fg, "durable_result", "GREEN", f"durable result written: {result_ref}", result_ref)
        bg["result_ref"] = result_ref
        fg["result_ref"] = result_ref

        if status == "PASS":
            set_condition(bg, "executor", "GREEN", "executor PASS", result_ref)
            set_condition(fg, "backend_execution", "GREEN", "backend executor PASS", result_ref)
            set_condition(bg, "verification", "RUNNING", "result verification pending")
            set_condition(fg, "verification", "RUNNING", "result verification pending")
            bg["overall"] = "RUNNING"
        else:
            err_state = "BLOCKED" if status == "BLOCKED" else "ERROR"
            summary = str(result.get("summary") or "executor failure")
            set_condition(bg, "executor", err_state, summary, result_ref)
            set_condition(fg, "backend_execution", err_state, summary, result_ref)
            set_condition(bg, "verification", err_state, "verification stopped at executor failure", result_ref)
            set_condition(fg, "verification", err_state, "verification stopped at executor failure", result_ref)
            set_condition(bg, "terminal", err_state, summary, result_ref)
            set_condition(fg, "final_acceptance", err_state, summary, result_ref)
            bg["overall"] = err_state
            bg["error"] = {"kind": str(result.get("fault_boundary") or "executor_failure"), "summary": summary, "evidence_ref": result_ref}
            fg["overall"] = err_state
            fg["error"] = dict(bg["error"])

        write_json(bg_path, bg)
        write_json(fg_path, fg)
        return {"phase": "execute", "task_id": action["task_id"], "result_status": status, "result_ref": result_ref}

    def verify(self, action_path: Path, result_path: Path) -> dict[str, Any]:
        action, fg_path, fg, bg_path, bg = self._load_bundle(action_path)
        result = load_json(result_path)
        result_ref = str(result_path.resolve().relative_to(self.root)).replace("\\", "/")
        status = str(result.get("status") or "ERROR")
        identity_error = result_identity_error(action, result)

        artifacts = result.get("artifacts")
        artifact_error = f"result identity mismatch: {identity_error}" if identity_error else None
        if artifact_error is None and not isinstance(artifacts, list):
            artifact_error = "result.artifacts must be an array"
        elif artifact_error is None:
            for rel in artifacts:
                if not isinstance(rel, str) or not rel.strip():
                    artifact_error = "artifact ref must be a non-empty string"
                    break
                try:
                    artifact_path = safe_repo_path(self.root, rel, "artifact")
                except SystemExit as exc:
                    artifact_error = str(exc)
                    break
                if not artifact_path.exists():
                    artifact_error = f"artifact missing from working tree: {rel}"
                    break
                tracked = subprocess.run(
                    ["git", "-C", str(self.root), "cat-file", "-e", f"HEAD:{Path(rel).as_posix()}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if tracked.returncode != 0:
                    artifact_error = f"artifact not durable in Git HEAD: {rel}"
                    break

        worker_continuation = bool(action.get("worker_continuation"))
        evidence = result.get("evidence")
        evidence_ok = isinstance(evidence, list)
        evidence_error = None
        if status == "PASS" and evidence_ok:
            expected_types = {
                str(item).strip()
                for item in (action.get("expected_evidence") or [])
                if str(item).strip()
            }
            actual_types = {
                str(item.get("type") or "").strip()
                for item in evidence
                if isinstance(item, dict) and str(item.get("type") or "").strip()
            }
            missing_types = sorted(expected_types - actual_types)
            if missing_types:
                evidence_error = "missing expected evidence type(s): " + ", ".join(missing_types)
        elif status == "PASS" and not evidence_ok:
            evidence_error = "result.evidence must be an array"

        verification_error = artifact_error or evidence_error
        need = host_config_need(result)

        if need and worker_continuation and semantic_branch_mode(action, bg):
            if verification_error is not None:
                summary = verification_error
                set_condition_if_present(bg, "deterministic_execution", "ERROR", summary, result_ref)
                bg["overall"] = "ERROR"
                bg["error"] = {
                    "kind": "host_config_need_verification_failed",
                    "summary": summary,
                    "evidence_ref": result_ref,
                }
            else:
                dispatch = bg.get("dispatch")
                if not isinstance(dispatch, dict) or str(dispatch.get("state") or "") != "WAIT_RESOURCE":
                    wait = enter_resource_wait(
                        bg_path,
                        capability_id=need["capability_id"],
                        prompt=need["user_prompt"],
                        projection_ref=need.get("capability_projection_ref"),
                    )
                    bg = load_json(bg_path)
                    dispatch = bg.get("dispatch")
                set_condition_if_present(
                    bg,
                    "deterministic_execution",
                    "BLOCKED",
                    str(result.get("summary") or need["user_prompt"]),
                    result_ref,
                )
                set_condition_if_present(
                    bg,
                    "semantic_work",
                    "WAIT",
                    need["user_prompt"],
                    result_ref,
                )
                set_condition_if_present(
                    bg,
                    "branch_output",
                    "WAIT",
                    "branch output paused for host capability configuration",
                    result_ref,
                )
                set_condition_if_present(
                    bg,
                    "barrier_acceptance",
                    "WAIT",
                    "foreground barrier remains closed while host capability is unresolved",
                    result_ref,
                )
                lane_cond = foreground_lane_condition_id(action)
                if lane_cond:
                    set_condition_if_present(
                        fg,
                        lane_cond,
                        "WAIT",
                        need["user_prompt"],
                        result_ref,
                    )
                bg["overall"] = "RUNNING"
                bg["error"] = None
                fg["overall"] = "RUNNING"
                fg["error"] = None
            write_json(bg_path, bg)
            write_json(fg_path, fg)
            return {
                "phase": "verify",
                "task_id": action["task_id"],
                "backend": bg["overall"],
                "foreground": fg["overall"],
                "semantic_branch": True,
                "resource_wait": True,
            }

        if worker_continuation and semantic_branch_mode(action, bg):
            if verification_error is None and evidence_ok:
                detail = f"durable deterministic result verified for semantic branch continuation: status={status}"
                state = "GREEN" if status == "PASS" else ("BLOCKED" if status == "BLOCKED" else "ERROR")
                set_condition_if_present(bg, "deterministic_execution", state, detail, result_ref)
                bg["error"] = None if status == "PASS" else {
                    "kind": str(result.get("fault_boundary") or "executor_failure"),
                    "summary": str(result.get("summary") or f"executor status {status}"),
                    "evidence_ref": result_ref,
                }
            else:
                summary = verification_error or "result.evidence must be an array"
                set_condition_if_present(bg, "deterministic_execution", "ERROR", summary, result_ref)
                bg["error"] = {
                    "kind": "artifact_not_durable" if artifact_error else ("expected_evidence_missing" if evidence_error else "verification_failure"),
                    "summary": summary,
                    "evidence_ref": result_ref,
                }
            set_condition_if_present(
                bg,
                "semantic_work",
                "RUNNING",
                "deterministic result verified; semantic Worker continuation pending",
                result_ref,
            )
            set_condition_if_present(
                bg,
                "branch_output",
                "WAIT",
                "branch result remains a semantic Worker responsibility after executor continuation",
                result_ref,
            )
            set_condition_if_present(
                bg,
                "barrier_acceptance",
                "WAIT",
                "foreground barrier remains closed until a fenced PASS branch result",
                result_ref,
            )
            lane_cond = foreground_lane_condition_id(action)
            if lane_cond:
                set_condition_if_present(
                    fg,
                    lane_cond,
                    "RUNNING",
                    f"deterministic result {status} verified; semantic continuation pending",
                    result_ref,
                )
            bg["overall"] = "RUNNING"
            fg["overall"] = "RUNNING"
            write_json(bg_path, bg)
            write_json(fg_path, fg)
            return {
                "phase": "verify",
                "task_id": action["task_id"],
                "backend": bg["overall"],
                "foreground": fg["overall"],
                "semantic_branch": True,
            }

        if worker_continuation:
            # Executor completion is an event for the semantic Worker, not logical
            # task termination. PASS and ERROR/BLOCKED results alike must return
            # control to the Worker after durable identity/artifact verification.
            if verification_error is None and evidence_ok:
                detail = f"durable executor result verified for Worker continuation: status={status}"
                if artifacts:
                    detail += f"; {len(artifacts)} durable artifact(s)"
                set_condition(bg, "verification", "GREEN", detail, result_ref)
                set_condition(fg, "verification", "GREEN", detail, result_ref)
            else:
                summary = verification_error or "result.evidence must be an array"
                set_condition(bg, "verification", "ERROR", summary, result_ref)
                set_condition(fg, "verification", "ERROR", summary, result_ref)
                err = {
                    "kind": "artifact_not_durable" if artifact_error else ("expected_evidence_missing" if evidence_error else "verification_failure"),
                    "summary": summary,
                    "evidence_ref": result_ref,
                }
                bg["error"] = err
                fg["error"] = dict(err)

            set_condition(bg, "terminal", "WAIT", "executor completion awaits semantic Worker continuation", result_ref)
            set_condition(fg, "final_acceptance", "WAIT", "semantic Worker continuation pending", result_ref)
            bg["overall"] = "RUNNING"
            fg["overall"] = "RUNNING"
        elif status == "PASS" and evidence_ok and result["evidence"] and verification_error is None:
            detail = "durable PASS result contains evidence"
            if artifacts:
                detail += f" and {len(artifacts)} durable artifact(s)"
            set_condition(bg, "verification", "GREEN", detail, result_ref)
            set_condition(bg, "terminal", "GREEN", "backend completion predicate satisfied", result_ref)
            set_condition(fg, "verification", "GREEN", "Harness verified durable PASS evidence/artifacts", result_ref)
            set_condition(fg, "final_acceptance", "GREEN", "backend completion predicate satisfied; foreground must verify before releasing latch", result_ref)
            bg["overall"] = "GREEN"
            fg["overall"] = "GREEN"
        else:
            err_state = "BLOCKED" if status == "BLOCKED" else "ERROR"
            summary = verification_error or str(result.get("summary") or "result verification failed")
            set_condition(bg, "verification", err_state, summary, result_ref)
            set_condition(bg, "terminal", err_state, summary, result_ref)
            set_condition(fg, "verification", err_state, summary, result_ref)
            set_condition(fg, "final_acceptance", err_state, summary, result_ref)
            bg["overall"] = err_state
            fg["overall"] = err_state
            err_kind = (
                "artifact_not_durable" if artifact_error
                else ("expected_evidence_missing" if evidence_error else str(result.get("fault_boundary") or "verification_failure"))
            )
            err = {"kind": err_kind, "summary": summary, "evidence_ref": result_ref}
            bg["error"] = err
            fg["error"] = dict(err)

        write_json(bg_path, bg)
        write_json(fg_path, fg)
        return {"phase": "verify", "task_id": action["task_id"], "backend": bg["overall"], "foreground": fg["overall"]}


def main() -> int:
    p = argparse.ArgumentParser(description="Minimal single-thread CAH CL runtime")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("claim", "execute", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--action", required=True)
        if name in {"execute", "verify"}:
            sp.add_argument("--result", required=True)

    args = p.parse_args()
    rt = SingleThreadRuntime(ROOT)
    action_path = safe_repo_path(ROOT, args.action, "action")
    if args.cmd == "claim":
        out = rt.claim(action_path)
    else:
        result_path = safe_repo_path(ROOT, args.result, "result")
        out = rt.execute(action_path, result_path) if args.cmd == "execute" else rt.verify(action_path, result_path)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
