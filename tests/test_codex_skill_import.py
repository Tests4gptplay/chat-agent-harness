import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import executors.codex_skill_import as importer


class CodexSkillImportTests(unittest.TestCase):
    def test_excludes_system_and_creates_candidates_incrementally(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "source"
            root = base / "repo"
            (source / ".system" / "hidden").mkdir(parents=True)
            (source / ".system" / "hidden" / "SKILL.md").write_text("# Hidden\nNever import", encoding="utf-8")
            (source / "blender-review").mkdir(parents=True)
            (source / "blender-review" / "SKILL.md").write_text(
                "---\nname: Blender Review\ndescription: Refine Blender assets with visual review.\n---\n"
                "# Blender Review\nUse bpy and render evidence.",
                encoding="utf-8",
            )
            (root / "skills/candidates").mkdir(parents=True)

            with mock.patch.object(importer, "ROOT", root), mock.patch.object(importer, "IMPORT_ROOT", root / "imports/codex-skills"):
                first = importer.import_skills(source)
                self.assertEqual(first["imported_skill_count"], 1)
                self.assertEqual(len(first["created_candidates"]), 1)
                candidate = root / first["created_candidates"][0]
                self.assertTrue(candidate.exists())
                got = json.loads(candidate.read_text(encoding="utf-8"))
                self.assertEqual(got["status"], "CANDIDATE")
                self.assertIn("blender", got["applicability"]["required_capabilities"])
                index = json.loads((root / "imports/codex-skills/index.json").read_text(encoding="utf-8"))
                self.assertEqual(index["excludes"], [".system"])
                self.assertEqual(index["skills"][0]["source_identity"], "codex_user_skills/blender-review")

                second = importer.import_skills(source)
                self.assertEqual(second["created_candidates"], [])
                self.assertEqual(second["unchanged_count"], 1)

    def test_changed_source_preserves_existing_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "source"
            root = base / "repo"
            skill_dir = source / "demo"
            skill_dir.mkdir(parents=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("# Demo\nFirst version.", encoding="utf-8")
            (root / "skills/candidates").mkdir(parents=True)

            with mock.patch.object(importer, "ROOT", root), mock.patch.object(importer, "IMPORT_ROOT", root / "imports/codex-skills"):
                first = importer.import_skills(source)
                candidate_ref = first["created_candidates"][0]
                candidate_path = root / candidate_ref
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate["summary"] = "Semantically refined by Worker."
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

                skill_file.write_text("# Demo\nSecond source version.", encoding="utf-8")
                second = importer.import_skills(source)
                self.assertEqual(second["created_candidates"], [])
                self.assertEqual(second["source_changed_candidates_preserved"], [candidate_ref])
                preserved = json.loads(candidate_path.read_text(encoding="utf-8"))
                self.assertEqual(preserved["summary"], "Semantically refined by Worker.")

    def test_sensitive_text_is_not_snapshotted(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "source"
            root = base / "repo"
            (source / "secret-skill").mkdir(parents=True)
            (source / "secret-skill" / "SKILL.md").write_text(
                "# Secret\napi_key = abcdefghijklmnopqrstuvwxyz123456",
                encoding="utf-8",
            )
            (root / "skills/candidates").mkdir(parents=True)

            with mock.patch.object(importer, "ROOT", root), mock.patch.object(importer, "IMPORT_ROOT", root / "imports/codex-skills"):
                out = importer.import_skills(source)
                self.assertEqual(out["skipped_sensitive_file_count"], 1)
                self.assertEqual(out["created_candidates"], [])
                snapshots = list((root / "imports/codex-skills").rglob("source.md"))
                self.assertEqual(snapshots, [])


if __name__ == "__main__":
    unittest.main()
