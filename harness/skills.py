#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]+$")
TERM_RE = re.compile(r"[A-Za-z0-9_.+-]+|[\u4e00-\u9fff]+")
VALID_STATUS = {"CANDIDATE", "ACTIVE", "DEPRECATED"}


class SkillRegistryError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SkillRegistryError(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _terms(text: str) -> set[str]:
    return {item.lower() for item in TERM_RE.findall(text or "") if item.strip()}


class SkillRegistry:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.skills_root = self.root / "skills"
        self.index_path = self.skills_root / "index.json"

    def safe_ref(self, value: str, *, must_exist: bool = True) -> Path:
        rel = Path(str(value))
        if rel.is_absolute() or ".." in rel.parts:
            raise SkillRegistryError(f"unsafe repository-relative ref: {value}")
        out = (self.root / rel).resolve()
        try:
            out.relative_to(self.root)
        except ValueError as exc:
            raise SkillRegistryError(f"ref escapes repository root: {value}") from exc
        if must_exist and not out.exists():
            raise SkillRegistryError(f"missing evidence/ref: {value}")
        return out

    def skill_paths(self) -> list[Path]:
        paths: list[Path] = []
        for folder in ("candidates", "active", "deprecated"):
            base = self.skills_root / folder
            if base.exists():
                paths.extend(sorted(base.glob("*.json")))
        return paths

    def load_skill_ref(self, skill_ref: str) -> tuple[Path, dict[str, Any]]:
        path = self.safe_ref(skill_ref)
        if path.suffix.lower() != ".json":
            raise SkillRegistryError("skill ref must point to JSON")
        skill = load_json(path)
        self.validate_skill(skill, check_evidence=True)
        return path, skill

    def validate_skill(self, skill: dict[str, Any], *, check_evidence: bool) -> dict[str, int]:
        required = {
            "v", "skill_id", "title", "status", "summary", "tags",
            "applicability", "procedure", "evidence", "stats", "provenance",
        }
        missing = sorted(required - set(skill))
        if missing:
            raise SkillRegistryError(f"skill missing fields: {missing}")
        if skill.get("v") != 1:
            raise SkillRegistryError("skill v must be 1")

        skill_id = str(skill.get("skill_id") or "")
        if not SKILL_ID_RE.fullmatch(skill_id):
            raise SkillRegistryError(f"invalid skill_id: {skill_id!r}")
        status = str(skill.get("status") or "")
        if status not in VALID_STATUS:
            raise SkillRegistryError(f"invalid skill status: {status}")
        if not str(skill.get("title") or "").strip() or not str(skill.get("summary") or "").strip():
            raise SkillRegistryError("title and summary are required")

        tags = skill.get("tags")
        if not isinstance(tags, list) or any(not isinstance(x, str) or not x.strip() for x in tags):
            raise SkillRegistryError("tags must be non-empty strings")
        if len(tags) != len(set(tags)):
            raise SkillRegistryError("tags must be unique")

        applicability = skill.get("applicability")
        if not isinstance(applicability, dict):
            raise SkillRegistryError("applicability must be an object")
        for key in ("intents", "required_capabilities", "constraints"):
            value = applicability.get(key)
            if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
                raise SkillRegistryError(f"applicability.{key} must be a list of non-empty strings")

        procedure = skill.get("procedure")
        if not isinstance(procedure, list) or not procedure:
            raise SkillRegistryError("procedure must contain at least one step")
        step_ids: set[str] = set()
        for step in procedure:
            if not isinstance(step, dict):
                raise SkillRegistryError("procedure step must be an object")
            step_id = str(step.get("step_id") or "")
            instruction = str(step.get("instruction") or "")
            if not step_id or not instruction:
                raise SkillRegistryError("procedure step requires step_id and instruction")
            if step_id in step_ids:
                raise SkillRegistryError(f"duplicate step_id: {step_id}")
            step_ids.add(step_id)
            for ref in step.get("implementation_refs", []):
                if check_evidence:
                    self.safe_ref(str(ref))

        evidence = skill.get("evidence")
        if not isinstance(evidence, list):
            raise SkillRegistryError("evidence must be a list")
        success_count = 0
        failure_count = 0
        seen_result_refs: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise SkillRegistryError("evidence entry must be an object")
            task_id = str(item.get("task_id") or "")
            result_ref = str(item.get("result_ref") or "")
            outcome = str(item.get("outcome") or "").upper()
            if not task_id or not result_ref or not outcome or not str(item.get("verified_at") or ""):
                raise SkillRegistryError("evidence requires task_id, result_ref, outcome, verified_at")
            if result_ref in seen_result_refs:
                raise SkillRegistryError(f"duplicate evidence result_ref: {result_ref}")
            seen_result_refs.add(result_ref)
            if check_evidence:
                result = load_json(self.safe_ref(result_ref))
                if str(result.get("task_id") or "") != task_id:
                    raise SkillRegistryError(f"evidence task mismatch for {result_ref}")
                actual = str(result.get("status") or "").upper()
                if actual != outcome:
                    raise SkillRegistryError(
                        f"evidence outcome mismatch for {result_ref}: declared {outcome}, actual {actual}"
                    )
            if outcome == "PASS":
                success_count += 1
            else:
                failure_count += 1

        stats = skill.get("stats")
        if not isinstance(stats, dict):
            raise SkillRegistryError("stats must be an object")
        for key in ("success_count", "failure_count", "use_count"):
            value = stats.get(key)
            if not isinstance(value, int) or value < 0:
                raise SkillRegistryError(f"stats.{key} must be a non-negative integer")
        if stats.get("success_count") != success_count or stats.get("failure_count") != failure_count:
            raise SkillRegistryError("stats success/failure counts must match evidence")
        if status == "ACTIVE" and success_count < 1:
            raise SkillRegistryError("ACTIVE skill requires at least one verified PASS result")

        provenance = skill.get("provenance")
        if not isinstance(provenance, dict):
            raise SkillRegistryError("provenance must be an object")
        for key in ("created_at", "updated_at", "source_kind"):
            if not str(provenance.get(key) or ""):
                raise SkillRegistryError(f"provenance.{key} is required")
        source_refs = provenance.get("source_refs")
        if not isinstance(source_refs, list):
            raise SkillRegistryError("provenance.source_refs must be a list")
        if check_evidence:
            for ref in source_refs:
                self.safe_ref(str(ref))

        return {"success_count": success_count, "failure_count": failure_count}

    def validate_all(self) -> dict[str, Any]:
        checked = []
        for path in self.skill_paths():
            skill = load_json(path)
            self.validate_skill(skill, check_evidence=True)
            checked.append(path.relative_to(self.root).as_posix())
        return {"ok": True, "checked": checked, "count": len(checked)}

    def rebuild_index(self) -> dict[str, Any]:
        entries = []
        for path in self.skill_paths():
            skill = load_json(path)
            counts = self.validate_skill(skill, check_evidence=True)
            app = skill["applicability"]
            entries.append(
                {
                    "skill_id": skill["skill_id"],
                    "title": skill["title"],
                    "status": skill["status"],
                    "ref": path.relative_to(self.root).as_posix(),
                    "summary": skill["summary"],
                    "tags": skill["tags"],
                    "intents": app["intents"],
                    "required_capabilities": app["required_capabilities"],
                    "success_count": counts["success_count"],
                    "failure_count": counts["failure_count"],
                    "use_count": skill["stats"]["use_count"],
                    "updated_at": skill["provenance"]["updated_at"],
                }
            )
        entries.sort(key=lambda item: (item["status"] != "ACTIVE", item["skill_id"]))
        index = {
            "v": 1,
            "updated_at": utc_now(),
            "skill_count": len(entries),
            "active_count": sum(1 for x in entries if x["status"] == "ACTIVE"),
            "skills": entries,
        }
        write_json(self.index_path, index)
        return index

    def match(
        self,
        query: str,
        *,
        capabilities: set[str] | None = None,
        include_candidates: bool = False,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            self.rebuild_index()
        index = load_json(self.index_path)
        query_terms = _terms(query)
        query_lower = query.lower()
        capabilities_lower = {x.lower() for x in capabilities or set()}
        matches = []

        for entry in index.get("skills", []):
            status = str(entry.get("status") or "")
            if status == "DEPRECATED" or (status == "CANDIDATE" and not include_candidates):
                continue
            required = {str(x).lower() for x in entry.get("required_capabilities", [])}
            if capabilities is not None and not required.issubset(capabilities_lower):
                continue

            title = str(entry.get("title") or "")
            summary = str(entry.get("summary") or "")
            tags = [str(x) for x in entry.get("tags", [])]
            intents = [str(x) for x in entry.get("intents", [])]
            searchable = " ".join([title, summary, *tags, *intents])
            terms = _terms(searchable)
            overlap = query_terms & terms
            score = len(overlap) * 4
            if query_lower and query_lower in searchable.lower():
                score += 12
            for tag in tags:
                if tag.lower() in query_lower:
                    score += 6
            if status == "ACTIVE":
                score += 2
            score += min(int(entry.get("success_count") or 0), 3)
            score -= min(int(entry.get("failure_count") or 0), 3) * 2
            if query_terms and score <= 2:
                continue
            matches.append({"score": score, **entry})

        matches.sort(key=lambda item: (-int(item["score"]), item["skill_id"]))
        return matches[: max(1, limit)]

    def promote(self, candidate_ref: str) -> dict[str, Any]:
        path, skill = self.load_skill_ref(candidate_ref)
        expected_parent = (self.skills_root / "candidates").resolve()
        if path.parent.resolve() != expected_parent:
            raise SkillRegistryError("promotion requires a skill under skills/candidates")
        counts = self.validate_skill(skill, check_evidence=True)
        if counts["success_count"] < 1:
            raise SkillRegistryError("candidate promotion requires at least one verified PASS result")

        skill["status"] = "ACTIVE"
        skill["provenance"]["updated_at"] = utc_now()
        dest = self.skills_root / "active" / f"{skill['skill_id']}.json"
        if dest.exists():
            raise SkillRegistryError(f"active skill already exists: {dest.relative_to(self.root)}")
        write_json(dest, skill)
        path.unlink()
        self.rebuild_index()
        return {
            "promoted": True,
            "skill_id": skill["skill_id"],
            "ref": dest.relative_to(self.root).as_posix(),
        }

    def record_evidence(self, skill_ref: str, task_id: str, result_ref: str) -> dict[str, Any]:
        path, skill = self.load_skill_ref(skill_ref)
        result_path = self.safe_ref(result_ref)
        result = load_json(result_path)
        actual_task = str(result.get("task_id") or "")
        if actual_task != task_id:
            raise SkillRegistryError(f"result task_id mismatch: expected {task_id}, got {actual_task}")
        outcome = str(result.get("status") or "").upper()
        if not outcome:
            raise SkillRegistryError("result has no status")

        evidence = skill["evidence"]
        existing = next((item for item in evidence if item.get("result_ref") == result_ref), None)
        record = {
            "task_id": task_id,
            "result_ref": result_ref,
            "outcome": outcome,
            "verified_at": utc_now(),
        }
        if existing is None:
            evidence.append(record)
        else:
            existing.update(record)

        success_count = sum(1 for item in evidence if str(item.get("outcome") or "").upper() == "PASS")
        failure_count = len(evidence) - success_count
        skill["stats"]["success_count"] = success_count
        skill["stats"]["failure_count"] = failure_count
        skill["stats"]["use_count"] = int(skill["stats"].get("use_count") or 0) + 1
        skill["provenance"]["updated_at"] = utc_now()
        self.validate_skill(skill, check_evidence=True)
        write_json(path, skill)
        self.rebuild_index()
        return {
            "recorded": True,
            "skill_id": skill["skill_id"],
            "outcome": outcome,
            "success_count": success_count,
            "failure_count": failure_count,
            "use_count": skill["stats"]["use_count"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="CAH evidence-backed Skill Registry")
    parser.add_argument("--root", default=str(ROOT))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate-all")
    sub.add_parser("rebuild-index")

    match = sub.add_parser("match")
    match.add_argument("--query", required=True)
    match.add_argument("--capability", action="append", default=None)
    match.add_argument("--include-candidates", action="store_true")
    match.add_argument("--limit", type=int, default=5)

    promote = sub.add_parser("promote")
    promote.add_argument("--candidate", required=True)

    record = sub.add_parser("record-evidence")
    record.add_argument("--skill", required=True)
    record.add_argument("--task-id", required=True)
    record.add_argument("--result-ref", required=True)

    args = parser.parse_args()
    registry = SkillRegistry(Path(args.root))

    if args.cmd == "validate-all":
        out = registry.validate_all()
    elif args.cmd == "rebuild-index":
        out = registry.rebuild_index()
    elif args.cmd == "match":
        caps = None if args.capability is None else set(args.capability)
        out = {
            "query": args.query,
            "matches": registry.match(
                args.query,
                capabilities=caps,
                include_candidates=args.include_candidates,
                limit=args.limit,
            ),
        }
    elif args.cmd == "promote":
        out = registry.promote(args.candidate)
    elif args.cmd == "record-evidence":
        out = registry.record_evidence(args.skill, args.task_id, args.result_ref)
    else:
        return 2

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
