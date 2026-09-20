#!/usr/bin/env python3
"""CAH host capability discovery and machine-local configuration cache.

Absolute host paths are machine-local state. Canonical/public Git receives only
sanitized projections returned by this module.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - non-Windows
    winreg = None  # type: ignore

CAPABILITY_UE56 = "unreal_engine_5_6"
STATE_AVAILABLE = "AVAILABLE"
STATE_NEED_HOST_CONFIG = "NEED_HOST_CONFIG"
STATE_UNAVAILABLE = "UNAVAILABLE"

DEFAULT_WINDOWS_LOCAL_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CAH"
DEFAULT_NONWINDOWS_LOCAL_ROOT = Path.home() / ".gah"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_local_root() -> Path:
    raw = os.environ.get("GAH_LOCAL_ROOT", "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_WINDOWS_LOCAL_ROOT if os.name == "nt" else DEFAULT_NONWINDOWS_LOCAL_ROOT


def cache_path(local_root: Path | None = None) -> Path:
    root = (local_root or default_local_root()).resolve()
    return root / "runtime" / "host-capabilities.local.json"


def _load_cache(local_root: Path | None = None) -> dict[str, Any]:
    path = cache_path(local_root)
    if not path.is_file():
        return {"v": 1, "updated_at": None, "capabilities": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"v": 1, "updated_at": None, "capabilities": {}}
    if not isinstance(value, dict) or not isinstance(value.get("capabilities"), dict):
        return {"v": 1, "updated_at": None, "capabilities": {}}
    return value


def _save_cache(cache: dict[str, Any], local_root: Path | None = None) -> None:
    path = cache_path(local_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache["v"] = 1
    cache["updated_at"] = utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _normalize_root(candidate: str | Path) -> Path:
    path = Path(candidate).expanduser()
    if path.name.lower() == "engine":
        path = path.parent
    return path.resolve()


@dataclass(frozen=True)
class Candidate:
    root: Path
    source: str


def _version_string(build: dict[str, Any]) -> str:
    major = int(build.get("MajorVersion") or 0)
    minor = int(build.get("MinorVersion") or 0)
    patch = int(build.get("PatchVersion") or 0)
    return f"{major}.{minor}.{patch}"


def validate_ue56_root(candidate: str | Path) -> dict[str, Any] | None:
    """Return verified local details for an Unreal 5.6 root, otherwise None."""
    try:
        root = _normalize_root(candidate)
    except (OSError, RuntimeError):
        return None
    build_file = root / "Engine" / "Build" / "Build.version"
    editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
    uat = root / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"
    if not build_file.is_file() or not editor.is_file() or not uat.is_file():
        return None
    try:
        build = json.loads(build_file.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        major = int(build.get("MajorVersion") or 0)
        minor = int(build.get("MinorVersion") or 0)
    except (TypeError, ValueError):
        return None
    if (major, minor) != (5, 6):
        return None
    return {
        "root": str(root),
        "editor": str(editor),
        "uat": str(uat),
        "build_version_file": str(build_file),
        "version": _version_string(build),
        "major": major,
        "minor": minor,
        "patch": int(build.get("PatchVersion") or 0),
    }


def _dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            key = os.path.normcase(str(item.root.resolve()))
        except (OSError, RuntimeError):
            key = os.path.normcase(str(item.root))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _cache_candidates(local_root: Path | None) -> list[Candidate]:
    record = (_load_cache(local_root).get("capabilities") or {}).get(CAPABILITY_UE56)
    if not isinstance(record, dict):
        return []
    root = str(record.get("root") or "").strip()
    return [Candidate(Path(root), "local_cache")] if root else []


def _env_candidates(env: dict[str, str]) -> list[Candidate]:
    out: list[Candidate] = []
    root = str(env.get("GAH_UE56_ROOT") or "").strip()
    if root:
        out.append(Candidate(Path(root), "env"))
    editor = str(env.get("GAH_UNREAL_EDITOR_EXE") or "").strip()
    if editor:
        p = Path(editor)
        try:
            root_from_editor = p.resolve().parents[3]
            out.append(Candidate(root_from_editor, "env"))
        except (IndexError, OSError, RuntimeError):
            pass
    return out


def _launcher_candidates(env: dict[str, str]) -> list[Candidate]:
    if os.name != "nt":
        return []
    program_data = Path(env.get("PROGRAMDATA") or r"C:\ProgramData")
    manifest = program_data / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat"
    if not manifest.is_file():
        return []
    try:
        value = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Candidate] = []
    for item in value.get("InstallationList") or []:
        if not isinstance(item, dict):
            continue
        location = str(item.get("InstallLocation") or "").strip()
        if location:
            out.append(Candidate(Path(location), "launcher"))
    return out


def _registry_candidates() -> list[Candidate]:
    if os.name != "nt" or winreg is None:
        return []
    out: list[Candidate] = []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine\5.6", "InstalledDirectory"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\EpicGames\Unreal Engine\5.6", "InstalledDirectory"),
    ]
    for hive, key_name, value_name in keys:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
            if value:
                out.append(Candidate(Path(str(value)), "registry"))
        except OSError:
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Epic Games\Unreal Engine\Builds") as key:
            index = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if value:
                    out.append(Candidate(Path(str(value)), "registry"))
    except OSError:
        pass
    return out


def _path_candidates() -> list[Candidate]:
    out: list[Candidate] = []
    for command in ("UnrealEditor.exe", "UnrealEditor"):
        found = shutil.which(command)
        if not found:
            continue
        p = Path(found)
        try:
            out.append(Candidate(p.resolve().parents[3], "path"))
        except (IndexError, OSError, RuntimeError):
            pass
    return out


def _filesystem_candidates(extra: Iterable[str | Path] | None = None) -> list[Candidate]:
    out: list[Candidate] = []
    for raw in extra or []:
        if str(raw).strip():
            out.append(Candidate(Path(raw), "filesystem"))
    if os.name != "nt":
        return out
    names = ("UE5", "UE5.6", "UE_5.6", "Unreal", "UnrealEngine")
    for drive in ("D:\\", "E:\\", "F:\\", "C:\\"):
        drive_path = Path(drive)
        if not drive_path.exists():
            continue
        for name in names:
            out.append(Candidate(drive_path / name, "filesystem"))
        out.append(Candidate(drive_path / "Epic Games" / "UE_5.6", "filesystem"))
    return out


PROJECTION_KEYS = {
    "v",
    "capability_id",
    "state",
    "available",
    "version",
    "source",
    "validation",
    "observed_at",
    "prompt",
    "reason",
}
PROJECTION_STATES = {STATE_AVAILABLE, STATE_NEED_HOST_CONFIG, STATE_UNAVAILABLE}
PROJECTION_SOURCES = {
    "local_cache",
    "user_config",
    "env",
    "launcher",
    "registry",
    "path",
    "filesystem",
    "runtime",
    "missing",
}
ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


def validate_projection(projection: dict[str, Any]) -> dict[str, Any]:
    if set(projection) != PROJECTION_KEYS:
        missing = sorted(PROJECTION_KEYS - set(projection))
        extra = sorted(set(projection) - PROJECTION_KEYS)
        raise ValueError(f"invalid capability projection keys; missing={missing} extra={extra}")
    if projection.get("v") != 1:
        raise ValueError("capability projection v must be 1")
    capability_id = str(projection.get("capability_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", capability_id):
        raise ValueError("invalid capability_id")
    state = str(projection.get("state") or "")
    if state not in PROJECTION_STATES:
        raise ValueError("invalid capability state")
    if bool(projection.get("available")) != (state == STATE_AVAILABLE):
        raise ValueError("capability available flag disagrees with state")
    source = str(projection.get("source") or "")
    if source not in PROJECTION_SOURCES:
        raise ValueError("invalid capability source")
    validation = str(projection.get("validation") or "")
    observed_at = str(projection.get("observed_at") or "")
    if not validation or len(validation) > 256 or not observed_at:
        raise ValueError("capability projection validation/observed_at required")
    for key, value in projection.items():
        if not isinstance(value, str):
            continue
        if ABSOLUTE_PATH_RE.match(value.strip()):
            raise ValueError(f"absolute host path leaked into sanitized projection field {key}")
    return projection


def sanitized_projection(
    capability_id: str,
    *,
    state: str,
    version: str | None,
    source: str,
    validation: str,
    prompt: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return validate_projection({
        "v": 1,
        "capability_id": capability_id,
        "state": state,
        "available": state == STATE_AVAILABLE,
        "version": version,
        "source": source,
        "validation": validation,
        "observed_at": utc_now(),
        "prompt": prompt,
        "reason": reason,
    })


def resolve_ue56(
    *,
    local_root: Path | None = None,
    supplied_root: str | Path | None = None,
    env: dict[str, str] | None = None,
    filesystem_candidates: Iterable[str | Path] | None = None,
    persist: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    env_map = dict(os.environ if env is None else env)
    candidates: list[Candidate] = []
    if supplied_root is not None and str(supplied_root).strip():
        candidates.append(Candidate(Path(supplied_root), "user_config"))
    candidates.extend(_env_candidates(env_map))
    candidates.extend(_cache_candidates(local_root))
    candidates.extend(_launcher_candidates(env_map))
    candidates.extend(_registry_candidates())
    candidates.extend(_path_candidates())
    candidates.extend(_filesystem_candidates(filesystem_candidates))

    for item in _dedupe(candidates):
        verified = validate_ue56_root(item.root)
        if not verified:
            continue
        if persist:
            cache = _load_cache(local_root)
            caps = cache.setdefault("capabilities", {})
            caps[CAPABILITY_UE56] = {
                "state": STATE_AVAILABLE,
                "root": verified["root"],
                "editor": verified["editor"],
                "uat": verified["uat"],
                "build_version_file": verified["build_version_file"],
                "version": verified["version"],
                "source": item.source,
                "validated_at": utc_now(),
            }
            _save_cache(cache, local_root)
        projection = sanitized_projection(
            CAPABILITY_UE56,
            state=STATE_AVAILABLE,
            version=verified["version"],
            source=item.source,
            validation="Engine/Build/Build.version",
        )
        return projection, verified

    prompt = "UE 5.6 was not found. Please provide the installation directory containing the Engine folder."
    projection = sanitized_projection(
        CAPABILITY_UE56,
        state=STATE_NEED_HOST_CONFIG,
        version=None,
        source="missing",
        validation="Engine/Build/Build.version",
        prompt=prompt,
        reason="automatic_discovery_exhausted",
    )
    return projection, None


Provider = Callable[..., tuple[dict[str, Any], dict[str, Any] | None]]
PROVIDERS: dict[str, Provider] = {
    CAPABILITY_UE56: resolve_ue56,
}


def resolve_capability(capability_id: str, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    provider = PROVIDERS.get(capability_id)
    if provider is None:
        return (
            sanitized_projection(
                capability_id,
                state=STATE_UNAVAILABLE,
                version=None,
                source="missing",
                validation="none",
                reason="unknown_capability",
            ),
            None,
        )
    projection, private = provider(**kwargs)
    return validate_projection(projection), private


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a reusable CAH host capability")
    parser.add_argument("capability_id", choices=sorted(PROVIDERS))
    parser.add_argument("--local-root")
    parser.add_argument("--candidate-root")
    parser.add_argument("--projection-out")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    projection, _private = resolve_capability(
        args.capability_id,
        local_root=Path(args.local_root) if args.local_root else None,
        supplied_root=args.candidate_root,
        persist=not args.no_persist,
    )
    text = json.dumps(projection, ensure_ascii=False, indent=2) + "\n"
    if args.projection_out:
        path = Path(args.projection_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
