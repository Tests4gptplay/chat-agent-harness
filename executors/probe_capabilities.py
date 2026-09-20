#!/usr/bin/env python3
"""Emit a sanitized executor capability manifest.

The probe deliberately reports no hostname, username, absolute executable path,
credential, or environment-variable value. It is safe to use as compact routing
evidence when the chosen node_id is non-sensitive.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.capabilities import CAPABILITY_UE56, resolve_capability

SAFE_NODE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_version(value: str | None) -> str | None:
    if not value:
        return None
    one_line = " ".join(str(value).split())
    return one_line[:160] or None


def command_version(executable: str, args: list[str], timeout: int = 5) -> str | None:
    try:
        proc = subprocess.run(
            [executable, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (proc.stdout or "").strip().splitlines()
    return clean_version(output[0] if output else None)


def resolve_tool(command_names: list[str], env_name: str | None = None) -> tuple[str | None, str]:
    if env_name:
        raw = os.environ.get(env_name, "").strip()
        if raw and Path(raw).is_file():
            return raw, "env"
    for name in command_names:
        found = shutil.which(name)
        if found:
            return found, "path"
    return None, "missing"


def interactive_desktop() -> bool | str:
    raw = os.environ.get("GAH_INTERACTIVE_DESKTOP", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return "unknown"


def tool(available: bool, version: str | None, source: str) -> dict[str, Any]:
    return {
        "available": bool(available),
        "version": clean_version(version),
        "source": source,
    }


def build_manifest(node_id: str) -> dict[str, Any]:
    if not SAFE_NODE.fullmatch(node_id):
        raise ValueError("node_id must match [A-Za-z0-9._:-]{1,128}")

    git_exe, git_source = resolve_tool(["git"])
    blender_exe, blender_source = resolve_tool(["blender", "blender.exe"], "GAH_BLENDER_EXE")
    ue_projection, _ue_private = resolve_capability(CAPABILITY_UE56)

    return {
        "v": 1,
        "node_id": node_id,
        "observed_at": utc_now(),
        "platform": {
            "system": clean_version(platform.system()) or "unknown",
            "architecture": clean_version(platform.machine()) or "unknown",
        },
        "interactive_desktop": interactive_desktop(),
        "tools": {
            "python": tool(True, platform.python_version(), "runtime"),
            "git": tool(bool(git_exe), command_version(git_exe, ["--version"]) if git_exe else None, git_source),
            "blender": tool(bool(blender_exe), command_version(blender_exe, ["--version"]) if blender_exe else None, blender_source),
            "unreal_editor": tool(
                bool(ue_projection.get("available")),
                str(ue_projection.get("version") or "") or None,
                str(ue_projection.get("source") or "missing"),
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", default="local-node")
    parser.add_argument("--out")
    args = parser.parse_args()

    try:
        manifest = build_manifest(args.node_id)
    except ValueError as exc:
        parser.error(str(exc))

    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
