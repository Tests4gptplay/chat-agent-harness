#!/usr/bin/env python3
"""CAH local Blender case executor.

Operations:
- bootstrap: verify Blender + managed local workspace and persist environment evidence.
- run_iteration: execute a Worker-authored bpy script, validate public case artifacts,
  preserve full local logs/blend, and commit compact/durable Git evidence.
- promote_final: promote one accepted iteration into final deliverables.

The executor never authors creative geometry. Semantic Workers own the bpy script and
iteration decisions; this file only performs deterministic local execution/validation.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOT = Path(os.environ.get("GAH_WORK_ROOT", str(Path.home() / "CAH" / "Workloads")))
REQUIRED_STAGES = (
    "01_blockout.png",
    "02_lens.png",
    "03_controls.png",
    "04_materials.png",
    "05_lighting.png",
)


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


def repo_path(value: str) -> Path:
    rel = Path(str(value))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe repository path: {value}")
    out = (ROOT / rel).resolve()
    out.relative_to(ROOT.resolve())
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_git_text(path: Path) -> str:
    """Hash UTF-8 text as canonical Git/LF bytes, independent of Windows CRLF checkout."""
    text = path.read_text(encoding="utf-8")
    canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def clean_log(text: str, max_chars: int = 400_000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n... [CAH log middle truncated] ...\n\n" + text[-half:]


def locate_blender() -> tuple[Path | None, str]:
    env = os.environ.get("GAH_BLENDER_EXE", "").strip()
    if env and Path(env).is_file():
        return Path(env), "GAH_BLENDER_EXE"
    found = shutil.which("blender") or shutil.which("blender.exe")
    if found:
        return Path(found), "PATH"

    candidates: list[Path] = []
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    for root in roots:
        if not root.exists():
            continue
        candidates.extend(root.glob("Blender Foundation/Blender*/blender.exe"))
        candidates.extend(root.glob("Blender*/blender.exe"))
        candidates.extend(root.glob("*/blender.exe"))
    candidates = sorted({p.resolve() for p in candidates if p.is_file()}, reverse=True)
    return (candidates[0], "common_install_scan") if candidates else (None, "missing")


def blender_version(exe: Path) -> str:
    proc = subprocess.run(
        [str(exe), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    first = (proc.stdout or "").splitlines()
    return first[0].strip()[:200] if first else "unknown"


def managed_workspace(raw: str, task_id: str) -> Path:
    root = Path(os.environ.get("GAH_WORK_ROOT", str(DEFAULT_WORK_ROOT))).resolve()
    requested = Path(raw).resolve() if raw else (root / task_id).resolve()
    requested.relative_to(root)
    requested.mkdir(parents=True, exist_ok=True)
    return requested


def append_journal(case_dir: Path, line: str) -> str:
    path = case_dir / "logs" / "execution_journal.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Execution journal\n\n"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + line.rstrip() + "\n", encoding="utf-8")
    return path.relative_to(ROOT).as_posix()


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
        "fault_boundary": "none" if status == "PASS" else "blender_case_executor",
    }


def op_bootstrap(action: dict[str, Any]) -> dict[str, Any]:
    payload = action.get("payload") or {}
    case_dir = repo_path(str(payload["case_dir"]))
    workspace = managed_workspace(str(payload.get("local_workspace") or ""), str(action["task_id"]))
    exe, source = locate_blender()
    manifest_path = case_dir / "evidence" / "bootstrap.json"
    journal_ref = append_journal(
        case_dir,
        f"- {utc_now()} — deterministic bootstrap executor started; local workspace prepared.",
    )
    manifest = {
        "v": 1,
        "task_id": action["task_id"],
        "observed_at": utc_now(),
        "blender_available": bool(exe),
        "blender_version": blender_version(exe) if exe else None,
        "blender_discovery_source": source,
        "local_workspace_policy": "GAH-managed local work root; heavy intermediate .blend/log files stay local",
        "local_workspace": str(workspace),
        "public_case_dir": case_dir.relative_to(ROOT).as_posix(),
    }
    write_json(manifest_path, manifest)
    result = base_result(
        action,
        "PASS" if exe else "BLOCKED",
        "Blender bootstrap ready" if exe else "Blender executable not found on local executor",
    )
    result["evidence"] = [
        {"type": "blender_available", "value": bool(exe)},
        {"type": "blender_version", "value": manifest["blender_version"]},
        {"type": "local_workspace_policy", "value": manifest["local_workspace_policy"]},
        {"type": "case_manifest", "value": manifest_path.relative_to(ROOT).as_posix()},
    ]
    result["artifacts"] = [manifest_path.relative_to(ROOT).as_posix(), journal_ref]
    if not exe:
        result["fault_boundary"] = "blender_missing"
    return result


def op_run_iteration(action: dict[str, Any]) -> dict[str, Any]:
    payload = action.get("payload") or {}
    task_id = str(action["task_id"])
    iteration = int(payload["iteration"])
    if iteration < 1 or iteration > 5:
        raise ValueError("iteration must be 1..5")
    case_dir = repo_path(str(payload["case_dir"]))
    script = repo_path(str(payload["script_path"]))
    if not script.is_file():
        raise ValueError(f"Worker-authored script missing: {script}")
    workspace = managed_workspace(str(payload.get("local_workspace") or ""), task_id)
    local_iter = workspace / f"iter_{iteration:02d}"
    local_iter.mkdir(parents=True, exist_ok=True)

    exe, source = locate_blender()
    if not exe:
        result = base_result(action, "BLOCKED", "Blender executable not found")
        result["evidence"] = [{"type": "blender_available", "value": False}]
        result["fault_boundary"] = "blender_missing"
        return result

    stage_dir = case_dir / "camera" / "stages" / f"iter_{iteration:02d}"
    render_dir = case_dir / "camera" / "renders"
    manifest_dir = case_dir / "camera" / "manifests"
    evidence_dir = case_dir / "evidence"
    log_dir = case_dir / "logs"
    preview_dir = case_dir / "camera" / "previews"
    for d in (stage_dir, render_dir, manifest_dir, evidence_dir, log_dir, preview_dir):
        d.mkdir(parents=True, exist_ok=True)

    final_render = render_dir / f"iter_{iteration:02d}.png"
    manifest_path = manifest_dir / f"iter_{iteration:02d}.json"
    blend_path = local_iter / f"camera_iter_{iteration:02d}.blend"
    local_log = local_iter / "blender_stdout.log"
    public_log = log_dir / f"iter_{iteration:02d}_blender.log"
    preview_b64 = preview_dir / f"iter_{iteration:02d}_final.png.b64.txt"

    env = os.environ.copy()
    env.update(
        {
            "GAH_TASK_ID": task_id,
            "GAH_ITERATION": str(iteration),
            "GAH_CASE_DIR": str(case_dir),
            "GAH_STAGE_DIR": str(stage_dir),
            "GAH_FINAL_RENDER": str(final_render),
            "GAH_ITERATION_MANIFEST": str(manifest_path),
            "GAH_BLEND_PATH": str(blend_path),
            "GAH_LOCAL_ITER_DIR": str(local_iter),
        }
    )
    started = utc_now()
    proc = subprocess.run(
        [
            str(exe),
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "7",
            "--python",
            str(script),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(payload.get("blender_timeout_seconds") or 2400),
        check=False,
    )
    log_text = proc.stdout or ""
    local_log.write_text(log_text, encoding="utf-8")
    public_log.write_text(clean_log(log_text), encoding="utf-8")

    missing = [name for name in REQUIRED_STAGES if not (stage_dir / name).is_file()]
    required_files = [final_render, manifest_path, blend_path]
    missing += [str(p.name) for p in required_files if not p.is_file()]
    if proc.returncode != 0 or missing:
        evidence_path = evidence_dir / f"iter_{iteration:02d}_executor.json"
        evidence = {
            "v": 1,
            "task_id": task_id,
            "iteration": iteration,
            "started_at": started,
            "finished_at": utc_now(),
            "blender_return_code": proc.returncode,
            "missing_outputs": missing,
            "script_ref": script.relative_to(ROOT).as_posix(),
            "public_log_ref": public_log.relative_to(ROOT).as_posix(),
        }
        write_json(evidence_path, evidence)
        journal_ref = append_journal(
            case_dir,
            f"- {utc_now()} — iteration {iteration} executor failed/blocked; return={proc.returncode}; missing={missing}.",
        )
        result = base_result(action, "ERROR", f"Blender iteration {iteration} failed validation")
        result["evidence"] = [
            {"type": "iteration", "value": iteration},
            {"type": "blender_return_code", "value": proc.returncode},
            {"type": "missing_outputs", "value": missing},
            {"type": "executor_evidence", "value": evidence_path.relative_to(ROOT).as_posix()},
        ]
        result["artifacts"] = [
            public_log.relative_to(ROOT).as_posix(),
            evidence_path.relative_to(ROOT).as_posix(),
            journal_ref,
        ]
        result["fault_boundary"] = "blender_iteration_validation"
        return result

    width, height = png_size(final_render)
    if (width, height) != (800, 800):
        raise ValueError(f"final render must be 800x800, got {width}x{height}")

    preview_b64.write_text(base64.b64encode(final_render.read_bytes()).decode("ascii"), encoding="ascii")

    stage_refs = [(stage_dir / name).relative_to(ROOT).as_posix() for name in REQUIRED_STAGES]
    output_refs = stage_refs + [
        final_render.relative_to(ROOT).as_posix(),
        manifest_path.relative_to(ROOT).as_posix(),
        public_log.relative_to(ROOT).as_posix(),
        preview_b64.relative_to(ROOT).as_posix(),
    ]
    evidence_path = evidence_dir / f"iter_{iteration:02d}_executor.json"
    evidence = {
        "v": 1,
        "task_id": task_id,
        "iteration": iteration,
        "started_at": started,
        "finished_at": utc_now(),
        "blender_version": blender_version(exe),
        "blender_discovery_source": source,
        "script_ref": script.relative_to(ROOT).as_posix(),
        "script_sha256": sha256(script),
        "final_render_ref": final_render.relative_to(ROOT).as_posix(),
        "final_render_sha256": sha256(final_render),
        "final_render_dimensions": [width, height],
        "stage_refs": stage_refs,
        "blend_local_path": str(blend_path),
        "blend_sha256": sha256(blend_path),
        "blend_size_bytes": blend_path.stat().st_size,
        "manifest_ref": manifest_path.relative_to(ROOT).as_posix(),
        "preview_base64_ref": preview_b64.relative_to(ROOT).as_posix(),
        "public_log_ref": public_log.relative_to(ROOT).as_posix(),
    }
    write_json(evidence_path, evidence)
    journal_ref = append_journal(
        case_dir,
        f"- {utc_now()} — iteration {iteration} Blender execution PASS; 5 stage images + 800x800 final render + local blend validated.",
    )
    output_refs.extend([evidence_path.relative_to(ROOT).as_posix(), journal_ref])

    result = base_result(action, "PASS", f"Blender iteration {iteration} completed with durable visual evidence")
    result["evidence"] = [
        {"type": "iteration", "value": iteration},
        {"type": "blender_version", "value": evidence["blender_version"]},
        {"type": "script_sha256", "value": evidence["script_sha256"]},
        {"type": "final_render", "value": evidence["final_render_ref"]},
        {"type": "final_render_dimensions", "value": [width, height]},
        {"type": "stage_count", "value": len(stage_refs)},
        {"type": "blend_sha256", "value": evidence["blend_sha256"]},
        {"type": "iteration_manifest", "value": evidence["manifest_ref"]},
        {"type": "final_preview_base64", "value": evidence["preview_base64_ref"]},
        {"type": "executor_evidence", "value": evidence_path.relative_to(ROOT).as_posix()},
    ]
    result["artifacts"] = output_refs
    return result


def op_promote_final(action: dict[str, Any]) -> dict[str, Any]:
    payload = action.get("payload") or {}
    task_id = str(action["task_id"])
    iteration = int(payload["selected_iteration"])
    if iteration < 1 or iteration > 5:
        raise ValueError("selected_iteration must be 1..5")
    case_dir = repo_path(str(payload["case_dir"]))
    script = repo_path(str(payload["script_path"]))
    workspace = managed_workspace(str(payload.get("local_workspace") or ""), task_id)
    local_blend = workspace / f"iter_{iteration:02d}" / f"camera_iter_{iteration:02d}.blend"
    source_render = case_dir / "camera" / "renders" / f"iter_{iteration:02d}.png"
    if not local_blend.is_file() or not source_render.is_file() or not script.is_file():
        raise ValueError("selected iteration deliverables are incomplete")

    final_script = case_dir / "camera" / "final_camera.py"
    final_render = case_dir / "camera" / "final.png"
    final_blend = case_dir / "camera" / "final.blend"
    final_manifest = case_dir / "camera" / "final_manifest.json"
    final_script.parent.mkdir(parents=True, exist_ok=True)

    if local_blend.stat().st_size > 90 * 1024 * 1024:
        result = base_result(action, "BLOCKED", "final .blend exceeds 90 MiB Git-safe promotion threshold")
        result["evidence"] = [
            {"type": "selected_iteration", "value": iteration},
            {"type": "blend_size_bytes", "value": local_blend.stat().st_size},
            {"type": "blend_sha256", "value": sha256(local_blend)},
        ]
        result["fault_boundary"] = "final_blend_too_large"
        return result

    shutil.copy2(script, final_script)
    shutil.copy2(source_render, final_render)
    shutil.copy2(local_blend, final_blend)
    width, height = png_size(final_render)
    manifest = {
        "v": 1,
        "task_id": task_id,
        "selected_iteration": iteration,
        "promoted_at": utc_now(),
        "final_render": {
            "ref": final_render.relative_to(ROOT).as_posix(),
            "sha256": sha256(final_render),
            "dimensions": [width, height],
        },
        "final_script": {
            "ref": final_script.relative_to(ROOT).as_posix(),
            "sha256": sha256_git_text(final_script),
            "worktree_sha256": sha256(final_script),
            "hash_policy": "sha256 is canonical UTF-8 LF content; worktree_sha256 records local checkout bytes",
        },
        "final_blend": {
            "ref": final_blend.relative_to(ROOT).as_posix(),
            "sha256": sha256(final_blend),
            "size_bytes": final_blend.stat().st_size,
        },
    }
    write_json(final_manifest, manifest)
    journal_ref = append_journal(
        case_dir,
        f"- {utc_now()} — iteration {iteration} promoted to final render/script/blend deliverables.",
    )
    result = base_result(action, "PASS", f"Iteration {iteration} promoted to final deliverables")
    result["evidence"] = [
        {"type": "selected_iteration", "value": iteration},
        {"type": "final_render", "value": manifest["final_render"]["ref"]},
        {"type": "final_script", "value": manifest["final_script"]["ref"]},
        {"type": "final_blend", "value": manifest["final_blend"]["ref"]},
        {"type": "final_manifest", "value": final_manifest.relative_to(ROOT).as_posix()},
    ]
    result["artifacts"] = [
        final_render.relative_to(ROOT).as_posix(),
        final_script.relative_to(ROOT).as_posix(),
        final_blend.relative_to(ROOT).as_posix(),
        final_manifest.relative_to(ROOT).as_posix(),
        journal_ref,
    ]
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--action", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args()
    action = load_json(Path(args.action))
    result_path = Path(args.result)
    operation = str(action.get("operation") or "")
    try:
        if operation == "bootstrap":
            result = op_bootstrap(action)
        elif operation == "run_iteration":
            result = op_run_iteration(action)
        elif operation == "promote_final":
            result = op_promote_final(action)
        else:
            raise ValueError(f"unsupported blender_case operation: {operation}")
    except subprocess.TimeoutExpired as exc:
        result = base_result(action, "ERROR", f"Blender subprocess timed out after {exc.timeout}s")
        result["evidence"] = [{"type": "timeout_seconds", "value": exc.timeout}]
        result["fault_boundary"] = "blender_subprocess_timeout"
    except Exception as exc:
        result = base_result(action, "ERROR", f"blender_case executor error: {type(exc).__name__}: {exc}")
        result["evidence"] = [{"type": "exception", "value": f"{type(exc).__name__}: {exc}"}]
        result["fault_boundary"] = "blender_case_executor_exception"

    result_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(result_path, result)
    print(json.dumps({"status": result["status"], "summary": result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
