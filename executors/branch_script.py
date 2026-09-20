#!/usr/bin/env python3
"""Execute one pinned, repository-owned PowerShell script from a fenced showcase branch.

This is a narrow host syscall for semantic Workers. It does not let a Worker select an
arbitrary local executable or arbitrary filesystem path. The executor:
- accepts only showcase/* branches;
- pins the exact remote branch head and script SHA-256;
- accepts only repository-relative PowerShell scripts under cases/**/work/**;
- runs with the Harness timeout envelope supplied by Stage 0;
- permits repository mutations only in an explicit evidence allow-list;
- commits/pushes those evidence paths back to the same branch iff the branch did not
  advance during execution;
- returns compact main-branch evidence through results/<task>.json.

Heavy outputs may live in GAH-managed host roots chosen by the branch script. Those
absolute paths may be reported as evidence, but are never interpreted as Git artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SAFE_BRANCH = re.compile(r"^showcase/[A-Za-z0-9._/-]+$")
SAFE_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
MAX_EVIDENCE_PATHS = 64


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_rel(value: Any, label: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    p = PurePosixPath(raw)
    if not raw or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"{label} must be a safe repository-relative path")
    if any(part in {"", "."} for part in p.parts):
        raise ValueError(f"{label} contains an invalid path component")
    return p.as_posix()


def validate_branch(value: Any) -> str:
    branch = str(value or "").strip()
    if not SAFE_BRANCH.fullmatch(branch) or ".." in branch or branch.endswith("/"):
        raise ValueError("branch must be a safe showcase/* ref")
    return branch


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def changed_paths(worktree: Path) -> set[str]:
    tracked = run_git("diff", "--name-only", cwd=worktree).stdout.splitlines()
    staged = run_git("diff", "--cached", "--name-only", cwd=worktree).stdout.splitlines()
    untracked = run_git("ls-files", "--others", "--exclude-standard", cwd=worktree).stdout.splitlines()
    return {
        PurePosixPath(x.replace("\\", "/")).as_posix()
        for x in [*tracked, *staged, *untracked]
        if x.strip()
    }


def base_result(action: dict[str, Any], status: str, summary: str) -> dict[str, Any]:
    return {
        "v": 1,
        "result_id": f"result-{action['action_id']}",
        "action_id": action["action_id"],
        "task_id": action["task_id"],
        "round": action["round"],
        "status": status,
        "summary": summary,
        "evidence": [],
        "artifacts": [],
        "fault_boundary": "none" if status == "PASS" else "branch_script_executor",
    }


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    branch = validate_branch(payload.get("branch"))
    expected_head = str(payload.get("expected_head") or "").strip().lower()
    if expected_head and not SAFE_SHA.fullmatch(expected_head):
        raise ValueError("expected_head must be empty or an exact 40-hex commit SHA")

    script = safe_rel(payload.get("script"), "script")
    sp = PurePosixPath(script)
    if not script.startswith("cases/") or "/work/" not in f"/{script}" or sp.suffix.lower() != ".ps1":
        raise ValueError("script must be a .ps1 under cases/**/work/**")

    script_sha256 = str(payload.get("script_sha256") or "").strip().lower()
    if script_sha256 and not re.fullmatch(r"[0-9a-f]{64}", script_sha256):
        raise ValueError("script_sha256 must be empty or a 64-hex SHA-256")

    raw_evidence = payload.get("evidence_paths")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ValueError("evidence_paths must be a non-empty array")
    if len(raw_evidence) > MAX_EVIDENCE_PATHS:
        raise ValueError(f"evidence_paths exceeds {MAX_EVIDENCE_PATHS}")
    evidence_paths: list[str] = []
    for item in raw_evidence:
        rel = safe_rel(item, "evidence_path")
        if not rel.startswith("cases/"):
            raise ValueError("evidence paths must remain under cases/")
        evidence_paths.append(rel)
    if len(set(evidence_paths)) != len(evidence_paths):
        raise ValueError("evidence_paths contains duplicates")

    raw_args = payload.get("script_args") or []
    if not isinstance(raw_args, list) or any(not isinstance(x, str) for x in raw_args):
        raise ValueError("script_args must be an array of strings")
    if len(raw_args) > 32 or any(len(x) > 2048 for x in raw_args):
        raise ValueError("script_args exceeds bounded limits")

    script_timeout_seconds = int(payload.get("script_timeout_seconds") or 0)
    if script_timeout_seconds < 1 or script_timeout_seconds > 21000:
        raise ValueError("script_timeout_seconds must be between 1 and 21000")

    return {
        "branch": branch,
        "expected_head": expected_head,
        "script": script,
        "script_sha256": script_sha256,
        "evidence_paths": evidence_paths,
        "script_args": list(raw_args),
        "script_timeout_seconds": script_timeout_seconds,
    }


def execute(action: dict[str, Any]) -> dict[str, Any]:
    payload = validate_payload(action.get("payload") or {})
    branch = payload["branch"]
    expected_head = payload["expected_head"]
    started_at = utc_now()

    ps = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        result = base_result(action, "BLOCKED", "PowerShell runtime not found on host")
        result["fault_boundary"] = "powershell_missing"
        result["evidence"] = [
            {"type": "branch", "value": branch},
            {"type": "branch_head_before", "value": expected_head},
            {"type": "started_at", "value": started_at},
            {"type": "completed_at", "value": utc_now()},
        ]
        return result

    # Fetch the exact semantic branch and prove the action's expected head is current.
    run_git("fetch", "--quiet", "--no-tags", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}")
    remote_ref = f"refs/remotes/origin/{branch}"
    branch_head = run_git("rev-parse", remote_ref).stdout.strip().lower()
    if expected_head and branch_head != expected_head:
        result = base_result(
            action,
            "BLOCKED",
            f"showcase branch advanced before execution: expected {expected_head}, observed {branch_head}",
        )
        result["fault_boundary"] = "branch_head_mismatch"
        result["evidence"] = [
            {"type": "branch", "value": branch},
            {"type": "branch_head_before", "value": branch_head},
            {"type": "expected_branch_head", "value": expected_head},
            {"type": "started_at", "value": started_at},
            {"type": "completed_at", "value": utc_now()},
        ]
        return result

    temp_parent = Path(tempfile.mkdtemp(prefix="gah-branch-script-"))
    worktree = temp_parent / "worktree"
    branch_head_after = branch_head
    committed = False
    pushed = False
    return_code: int | None = None
    stdout_tail = ""
    stderr_tail = ""
    durable_refs: list[str] = []
    try:
        run_git("worktree", "add", "--force", "--detach", str(worktree), branch_head)
        script_path = (worktree / Path(payload["script"])).resolve()
        script_path.relative_to(worktree.resolve())
        if not script_path.is_file():
            raise ValueError(f"branch script missing: {payload['script']}")
        actual_script_sha = sha256(script_path)
        if payload["script_sha256"] and actual_script_sha.lower() != payload["script_sha256"]:
            raise ValueError(
                f"branch script SHA mismatch: expected {payload['script_sha256']} got {actual_script_sha}"
            )

        env = os.environ.copy()
        env.update(
            {
                "GAH_BRANCH_SCRIPT_BRANCH": branch,
                "GAH_BRANCH_SCRIPT_HEAD": branch_head,
                "GAH_BRANCH_SCRIPT_ACTION_ID": str(action["action_id"]),
                "GAH_BRANCH_SCRIPT_TASK_ID": str(action["task_id"]),
            }
        )
        command = [
            ps,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            *payload["script_args"],
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(
            command,
            cwd=worktree,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=int(payload["script_timeout_seconds"]))
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                proc.kill()
            stdout, stderr = proc.communicate()
        return_code = 124 if timed_out else proc.returncode
        stdout_tail = (stdout or "")[-12000:]
        stderr_tail = (stderr or "")[-12000:]

        changed = changed_paths(worktree)
        allowed = set(payload["evidence_paths"])
        undeclared = sorted(changed - allowed)
        if undeclared:
            raise RuntimeError(
                "branch script modified undeclared repository path(s): " + ", ".join(undeclared[:20])
            )

        existing_evidence = [
            rel for rel in payload["evidence_paths"] if (worktree / Path(rel)).is_file()
        ]
        missing_evidence = sorted(set(payload["evidence_paths"]) - set(existing_evidence))

        # Persist both PASS and failure evidence when it is confined to the declared paths.
        if existing_evidence:
            run_git("config", "user.name", "gah-self-hosted-runner", cwd=worktree)
            run_git("config", "user.email", "actions@users.noreply.github.com", cwd=worktree)
            run_git("add", "--", *existing_evidence, cwd=worktree)
            staged = run_git("diff", "--cached", "--quiet", cwd=worktree, check=False)
            if staged.returncode != 0:
                msg = f"Record branch-script evidence {action['action_id']} [skip ci]"
                run_git("commit", "-m", msg, cwd=worktree)
                committed = True
                branch_head_after = run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip().lower()

                # Fence the push: semantic Worker must still be waiting on the exact branch head.
                run_git("fetch", "--quiet", "--no-tags", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}", cwd=worktree)
                latest_remote = run_git("rev-parse", remote_ref, cwd=worktree).stdout.strip().lower()
                if latest_remote != branch_head:
                    raise RuntimeError(
                        f"showcase branch advanced during host execution: start={branch_head} current={latest_remote}"
                    )
                push = run_git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree, check=False)
                if push.returncode != 0:
                    raise RuntimeError("failed to push branch evidence: " + (push.stderr or push.stdout)[-2000:])
                pushed = True

        durable_refs = [f"github://example-owner/cah-private/{branch}/{rel}" for rel in existing_evidence]
        completed_at = utc_now()
        if return_code == 0 and not missing_evidence:
            status = "PASS"
            summary = f"Pinned branch script completed and {len(existing_evidence)} evidence file(s) are durable"
            fault = "none"
        else:
            status = "ERROR"
            summary = (
                f"Pinned branch script exited {return_code}; "
                f"missing declared evidence: {missing_evidence if missing_evidence else 'none'}"
            )
            fault = "branch_script_process_failed" if return_code else "branch_script_evidence_missing"

        result = base_result(action, status, summary)
        result["fault_boundary"] = fault
        result["evidence"] = [
            {"type": "branch", "value": branch},
            {"type": "branch_head_before", "value": branch_head},
            {"type": "branch_head_after", "value": branch_head_after},
            {"type": "script", "value": payload["script"]},
            {"type": "script_sha256", "value": actual_script_sha},
            {"type": "return_code", "value": return_code},
            {"type": "script_timeout_seconds", "value": payload["script_timeout_seconds"]},
            {"type": "started_at", "value": started_at},
            {"type": "completed_at", "value": completed_at},
            {"type": "branch_evidence_refs", "value": durable_refs},
            {"type": "branch_evidence_missing", "value": missing_evidence},
            {"type": "branch_evidence_commit_created", "value": committed},
            {"type": "branch_evidence_push_succeeded", "value": pushed or not committed},
        ]
        # Branch-only evidence is cited above, not declared as main-branch artifacts.
        result["artifacts"] = []
        if stdout_tail:
            result["evidence"].append({"type": "stdout_tail", "value": stdout_tail})
        if stderr_tail:
            result["evidence"].append({"type": "stderr_tail", "value": stderr_tail})
        return result
    finally:
        try:
            run_git("worktree", "remove", "--force", str(worktree), check=False)
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--action", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args()

    action = load_json(Path(args.action))
    result_path = Path(args.result)
    try:
        result = execute(action)
    except subprocess.CalledProcessError as exc:
        result = base_result(action, "ERROR", f"branch_script git failure: {(exc.stderr or exc.stdout or str(exc))[-3000:]}")
        result["fault_boundary"] = "branch_script_git_failure"
        result["evidence"] = [{"type": "completed_at", "value": utc_now()}]
    except Exception as exc:
        result = base_result(action, "ERROR", f"branch_script executor error: {type(exc).__name__}: {exc}")
        result["fault_boundary"] = "branch_script_executor_exception"
        result["evidence"] = [{"type": "completed_at", "value": utc_now()}]

    write_json(result_path, result)
    print(json.dumps({"status": result["status"], "summary": result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
