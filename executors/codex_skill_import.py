#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path.home() / ".codex" / "skills"
IMPORT_ROOT = ROOT / "imports" / "codex-skills"
MAX_TEXT_BYTES = 512 * 1024
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".ps1", ".sh", ".js", ".ts"}
SENSITIVE_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*[^\s#]{8,}"),
]
CAPABILITY_KEYWORDS = {
    "blender": ("blender", "bpy"),
    "unreal": ("unreal", "ue5", "ue "),
    "python": ("python", ".py", "pytest"),
    "git": ("git", "github"),
    "web": ("browser", "playwright", "selenium", "web "),
    "powershell": ("powershell", "pwsh", ".ps1"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    return slug[:96] or "skill"


def is_excluded(rel: Path) -> bool:
    return any(part.lower() == ".system" for part in rel.parts)


def decode_text(data: bytes) -> str | None:
    if bytes([0]) in data:
        return None
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return None


def contains_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key and value:
            out[key] = value
    return out


def first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:160]
    return None


def first_paragraph(text: str) -> str | None:
    lines: list[str] = []
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            text = text[end + 4:]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if lines:
                break
            continue
        lines.append(stripped)
        if len(" ".join(lines)) >= 300:
            break
    paragraph = " ".join(lines).strip()
    return paragraph[:800] or None


def infer_capabilities(text: str, rel_root: str) -> list[str]:
    haystack = (rel_root + "\n" + text[:12000]).lower()
    return [
        capability
        for capability, needles in CAPABILITY_KEYWORDS.items()
        if any(needle in haystack for needle in needles)
    ]


def discover_source() -> Path:
    explicit = os.environ.get("GAH_CODEX_SKILLS_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if DEFAULT_SOURCE.is_dir():
        return DEFAULT_SOURCE
    if os.name == "nt":
        for drive in ("C:/", "D:/", "E:/", "F:/"):
            root = Path(drive)
            if not root.exists():
                continue
            for candidate in root.glob("*/.codex/skills"):
                if candidate.is_dir():
                    return candidate
    return DEFAULT_SOURCE


def load_previous_index() -> dict[str, Any]:
    path = IMPORT_ROOT / "index.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def build_candidate(skill_key: str, title: str, summary: str, capabilities: list[str], snapshot_ref: str, manifest_ref: str, imported_at: str) -> dict[str, Any]:
    skill_id = f"codex-{safe_slug(skill_key)}-v1"
    tags = list(dict.fromkeys(["codex-import", safe_slug(skill_key), *capabilities]))
    return {
        "v": 1,
        "skill_id": skill_id,
        "title": title,
        "status": "CANDIDATE",
        "summary": summary,
        "tags": tags,
        "applicability": {
            "intents": [summary],
            "required_capabilities": capabilities,
            "constraints": [
                "Imported from a local Codex user Skill; source semantics must be reviewed before promotion.",
                "Current task requirements and fresh evidence override imported instructions."
            ]
        },
        "procedure": [{
            "step_id": "imported-procedure",
            "instruction": "Read the sanitized imported Codex Skill snapshot and follow its reusable procedure while preserving its stated constraints. Refine this candidate into native CAH steps before promotion.",
            "deterministic": False,
            "implementation_refs": [snapshot_ref]
        }],
        "evidence": [],
        "stats": {"success_count": 0, "failure_count": 0, "use_count": 0},
        "provenance": {
            "created_at": imported_at,
            "updated_at": imported_at,
            "source_kind": "codex_user_skill_import",
            "source_refs": [snapshot_ref, manifest_ref]
        }
    }


def import_skills(source_root: Path) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"Codex skills root not found: {source_root}")

    previous = load_previous_index()
    previous_by_key = {
        str(item.get("skill_key")): item
        for item in previous.get("skills", [])
        if isinstance(item, dict) and item.get("skill_key")
    }

    imported_at = utc_now()
    skill_dirs = sorted(p for p in source_root.iterdir() if p.is_dir() and p.name.lower() != ".system")
    entries = []
    created_candidates: list[str] = []
    changed_candidates: list[str] = []
    unchanged = 0
    skipped_sensitive = 0
    skipped_binary_or_large = 0

    for skill_dir in skill_dirs:
        skill_key = skill_dir.name
        rel_files = []
        snapshot_parts = []
        source_hash = hashlib.sha256()
        source_doc_text = None

        for path in sorted(skill_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(source_root)
            if is_excluded(rel):
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS or path.stat().st_size > MAX_TEXT_BYTES:
                skipped_binary_or_large += 1
                continue
            data = path.read_bytes()
            text = decode_text(data)
            if text is None:
                skipped_binary_or_large += 1
                continue
            if contains_sensitive(text):
                skipped_sensitive += 1
                rel_files.append({"relative_path": rel.as_posix(), "sha256": sha256_bytes(data), "size_bytes": len(data), "status": "skipped_sensitive"})
                continue

            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            canonical = normalized.encode("utf-8")
            source_hash.update(rel.as_posix().encode("utf-8") + b"|" + canonical + b"|")
            rel_files.append({"relative_path": rel.as_posix(), "sha256": sha256_bytes(canonical), "size_bytes": len(canonical), "status": "included"})
            snapshot_parts.append(f"\n\n<!-- SOURCE: {rel.as_posix()} -->\n\n{normalized}")
            if path.name.lower() == "skill.md":
                source_doc_text = normalized

        fingerprint = source_hash.hexdigest()
        if not snapshot_parts:
            entries.append({"skill_key": skill_key, "fingerprint": fingerprint, "status": "no_safe_text", "files": rel_files})
            continue

        slug = safe_slug(skill_key)
        out_dir = IMPORT_ROOT / "sources" / slug
        snapshot_path = out_dir / "source.md"
        per_manifest = out_dir / "manifest.json"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            f"# Imported Codex Skill source: {skill_key}\n\n"
            f"Source identity: codex_user_skills/{skill_key}\n\n"
            f"Fingerprint: {fingerprint}\n"
            + "".join(snapshot_parts),
            encoding="utf-8"
        )

        snapshot_ref = snapshot_path.relative_to(ROOT).as_posix()
        manifest_ref = per_manifest.relative_to(ROOT).as_posix()
        prior = previous_by_key.get(skill_key) or {}
        changed = str(prior.get("fingerprint") or "") != fingerprint
        stable_imported_at = imported_at if changed else str(prior.get("imported_at") or imported_at)

        metadata_text = source_doc_text or "\n".join(snapshot_parts)
        frontmatter = parse_frontmatter(metadata_text)
        title = (frontmatter.get("name") or frontmatter.get("title") or first_heading(metadata_text) or skill_key)[:160]
        summary = (frontmatter.get("description") or first_paragraph(metadata_text) or f"Imported Codex user Skill: {skill_key}")[:800]
        capabilities = infer_capabilities(metadata_text, skill_key)

        candidate_ref = f"skills/candidates/codex-{slug}-v1.json"
        candidate_path = ROOT / candidate_ref
        candidate_status = "existing"
        if not candidate_path.exists():
            write_json(candidate_path, build_candidate(skill_key, title, summary, capabilities, snapshot_ref, manifest_ref, imported_at))
            created_candidates.append(candidate_ref)
            candidate_status = "created"
        elif changed:
            changed_candidates.append(candidate_ref)
            candidate_status = "source_changed_candidate_preserved"

        per = {
            "v": 1,
            "skill_key": skill_key,
            "source_identity": f"codex_user_skills/{skill_key}",
            "fingerprint": fingerprint,
            "imported_at": stable_imported_at,
            "changed": changed,
            "candidate_ref": candidate_ref,
            "candidate_status": candidate_status,
            "snapshot_ref": snapshot_ref,
            "files": rel_files
        }
        write_json(per_manifest, per)
        entries.append(per)
        if not changed:
            unchanged += 1

    current_keys = {str(item.get("skill_key")) for item in entries}
    removed = sorted(key for key in previous_by_key if key not in current_keys)
    previous_fingerprints = {key: str(value.get("fingerprint") or "") for key, value in previous_by_key.items()}
    current_fingerprints = {str(item.get("skill_key")): str(item.get("fingerprint") or "") for item in entries}
    material_change = previous_fingerprints != current_fingerprints or sorted(previous.get("removed_since_previous_sync", [])) != removed
    reconciled_at = imported_at if material_change or not previous else str(previous.get("reconciled_at") or imported_at)
    write_json(IMPORT_ROOT / "index.json", {
        "v": 1,
        "source_identity": "codex_user_skills",
        "excludes": [".system"],
        "reconciled_at": reconciled_at,
        "skill_count": len(entries),
        "skills": entries,
        "removed_since_previous_sync": removed
    })
    return {
        "source_skill_count": len(skill_dirs),
        "imported_skill_count": len(entries),
        "created_candidates": created_candidates,
        "source_changed_candidates_preserved": changed_candidates,
        "unchanged_count": unchanged,
        "removed_since_previous_sync": removed,
        "skipped_sensitive_file_count": skipped_sensitive,
        "skipped_binary_or_large_file_count": skipped_binary_or_large,
        "index_ref": "imports/codex-skills/index.json"
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Incrementally import local Codex user Skills into CAH candidates")
    parser.add_argument("--action", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    action = json.loads(Path(args.action).read_text(encoding="utf-8"))
    source = discover_source()
    result_path = Path(args.result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        summary = import_skills(source)
        subprocess.run(
            [sys.executable, str(ROOT / "harness" / "skills.py"), "rebuild-index"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        artifacts = ["imports/codex-skills/index.json", "skills/index.json", *summary["created_candidates"]]
        index = json.loads((IMPORT_ROOT / "index.json").read_text(encoding="utf-8"))
        for item in index.get("skills", []):
            if not isinstance(item, dict):
                continue
            if item.get("snapshot_ref"):
                artifacts.append(str(item["snapshot_ref"]))
            if item.get("skill_key"):
                artifacts.append(f"imports/codex-skills/sources/{safe_slug(str(item['skill_key']))}/manifest.json")
        artifacts = list(dict.fromkeys(artifacts))
        result = {
            "v": 1,
            "result_id": f"result-{action['action_id']}",
            "action_id": action["action_id"],
            "task_id": action["task_id"],
            "round": action["round"],
            "status": "PASS",
            "summary": "Codex user Skills reconciled into CAH import records/candidates",
            "evidence": [
                {"type": "imported_skill_count", "value": summary["imported_skill_count"]},
                {"type": "created_candidate_count", "value": len(summary["created_candidates"])},
                {"type": "changed_candidate_count", "value": len(summary["source_changed_candidates_preserved"])},
                {"type": "unchanged_count", "value": summary["unchanged_count"]},
                {"type": "skipped_sensitive_file_count", "value": summary["skipped_sensitive_file_count"]},
                {"type": "index_ref", "value": summary["index_ref"]}
            ],
            "artifacts": artifacts,
            "fault_boundary": "none"
        }
    except Exception as exc:
        result = {
            "v": 1,
            "result_id": f"result-{action['action_id']}",
            "action_id": action["action_id"],
            "task_id": action["task_id"],
            "round": action["round"],
            "status": "ERROR",
            "summary": f"Codex Skill import failed: {type(exc).__name__}: {exc}",
            "evidence": [{"type": "exception", "value": f"{type(exc).__name__}: {exc}"}],
            "artifacts": [],
            "fault_boundary": "codex_skill_import"
        }

    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "summary": result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
