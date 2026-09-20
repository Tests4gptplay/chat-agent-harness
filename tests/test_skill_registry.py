import json
import tempfile
import unittest
from pathlib import Path

from harness.skills import SkillRegistry, SkillRegistryError


class SkillRegistryTests(unittest.TestCase):
    def make_root(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        for folder in ("skills/active", "skills/candidates", "skills/deprecated", "results", "impl"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        return td, root

    def write_result(self, root, task_id, status="PASS", name="result.json"):
        path = root / "results" / name
        path.write_text(json.dumps({"v": 1, "task_id": task_id, "status": status}), encoding="utf-8")
        return path.relative_to(root).as_posix()

    def skill(self, *, status="CANDIDATE", result_ref=None, outcome=None):
        evidence = []
        if result_ref is not None:
            evidence.append({
                "task_id": "task-1",
                "result_ref": result_ref,
                "outcome": outcome or "PASS",
                "verified_at": "2026-09-19T00:00:00Z",
            })
        success = sum(1 for x in evidence if x["outcome"] == "PASS")
        return {
            "v": 1,
            "skill_id": "demo-skill-v1",
            "title": "Demo reusable procedure",
            "status": status,
            "summary": "Reusable visual refinement workflow for Blender assets.",
            "tags": ["blender", "visual-review"],
            "applicability": {
                "intents": ["refine Blender asset"],
                "required_capabilities": ["blender"],
                "constraints": ["render evidence is available"],
            },
            "procedure": [
                {
                    "step_id": "run",
                    "instruction": "Run the bounded refinement loop.",
                    "deterministic": False,
                    "implementation_refs": [],
                }
            ],
            "evidence": evidence,
            "stats": {
                "success_count": success,
                "failure_count": len(evidence) - success,
                "use_count": 0,
            },
            "provenance": {
                "created_at": "2026-09-19T00:00:00Z",
                "updated_at": "2026-09-19T00:00:00Z",
                "source_kind": "test",
                "source_refs": [],
            },
        }

    def test_active_skill_requires_verified_pass(self):
        td, root = self.make_root()
        self.addCleanup(td.cleanup)
        reg = SkillRegistry(root)
        with self.assertRaises(SkillRegistryError):
            reg.validate_skill(self.skill(status="ACTIVE"), check_evidence=True)

    def test_candidate_promotes_after_pass_evidence(self):
        td, root = self.make_root()
        self.addCleanup(td.cleanup)
        result_ref = self.write_result(root, "task-1")
        candidate = self.skill(result_ref=result_ref)
        candidate_path = root / "skills/candidates/demo-skill-v1.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        reg = SkillRegistry(root)
        out = reg.promote("skills/candidates/demo-skill-v1.json")
        self.assertTrue(out["promoted"])
        self.assertTrue((root / "skills/active/demo-skill-v1.json").exists())
        self.assertFalse(candidate_path.exists())
        index = json.loads((root / "skills/index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["active_count"], 1)

    def test_declared_evidence_must_match_terminal_result(self):
        td, root = self.make_root()
        self.addCleanup(td.cleanup)
        result_ref = self.write_result(root, "task-1", status="ERROR")
        candidate = self.skill(result_ref=result_ref, outcome="PASS")
        reg = SkillRegistry(root)
        with self.assertRaises(SkillRegistryError):
            reg.validate_skill(candidate, check_evidence=True)

    def test_match_filters_by_capability(self):
        td, root = self.make_root()
        self.addCleanup(td.cleanup)
        result_ref = self.write_result(root, "task-1")
        active = self.skill(status="ACTIVE", result_ref=result_ref)
        path = root / "skills/active/demo-skill-v1.json"
        path.write_text(json.dumps(active), encoding="utf-8")
        reg = SkillRegistry(root)
        reg.rebuild_index()
        matches = reg.match("Blender visual review", capabilities={"blender"})
        self.assertEqual(matches[0]["skill_id"], "demo-skill-v1")
        self.assertEqual(reg.match("Blender visual review", capabilities={"python"}), [])

    def test_record_evidence_accumulates_success_and_failure(self):
        td, root = self.make_root()
        self.addCleanup(td.cleanup)
        first = self.write_result(root, "task-1", status="PASS", name="first.json")
        second = self.write_result(root, "task-2", status="ERROR", name="second.json")
        active = self.skill(status="ACTIVE", result_ref=first)
        path = root / "skills/active/demo-skill-v1.json"
        path.write_text(json.dumps(active), encoding="utf-8")
        reg = SkillRegistry(root)
        out = reg.record_evidence("skills/active/demo-skill-v1.json", "task-2", second)
        self.assertEqual(out["success_count"], 1)
        self.assertEqual(out["failure_count"], 1)
        self.assertEqual(out["use_count"], 1)
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
