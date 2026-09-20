import unittest

from executors.branch_script import validate_payload


class BranchScriptValidationTests(unittest.TestCase):
    def test_accepts_pinned_showcase_script(self):
        got = validate_payload(
            {
                "branch": "showcase/ue-quicklook-retro-camera",
                "expected_head": "a" * 40,
                "script": "cases/ue-mod-quicklook-001/work/lane-01/fixture/run_fixture.ps1",
                "script_sha256": "b" * 64,
                "script_timeout_seconds": 1800,
                "script_args": [],
                "evidence_paths": [
                    "cases/ue-mod-quicklook-001/work/lane-01/evidence/cook_summary.json",
                    "cases/ue-mod-quicklook-001/work/lane-01/evidence/fixture_manifest.json",
                ],
            }
        )
        self.assertEqual(got["branch"], "showcase/ue-quicklook-retro-camera")
        self.assertEqual(got["script_timeout_seconds"], 1800)

    def test_rejects_non_showcase_branch(self):
        with self.assertRaises(ValueError):
            validate_payload(
                {
                    "branch": "main",
                    "expected_head": "a" * 40,
                    "script": "cases/x/work/lane-00/run.ps1",
                    "script_sha256": "b" * 64,
                    "script_timeout_seconds": 30,
                    "evidence_paths": ["cases/x/work/lane-00/evidence/result.json"],
                }
            )

    def test_rejects_script_outside_case_work_root(self):
        with self.assertRaises(ValueError):
            validate_payload(
                {
                    "branch": "showcase/x",
                    "expected_head": "a" * 40,
                    "script": "host/run.ps1",
                    "script_sha256": "b" * 64,
                    "script_timeout_seconds": 30,
                    "evidence_paths": ["cases/x/work/lane-00/evidence/result.json"],
                }
            )

    def test_rejects_parent_traversal_evidence(self):
        with self.assertRaises(ValueError):
            validate_payload(
                {
                    "branch": "showcase/x",
                    "expected_head": "a" * 40,
                    "script": "cases/x/work/lane-00/run.ps1",
                    "script_sha256": "b" * 64,
                    "script_timeout_seconds": 30,
                    "evidence_paths": ["cases/x/work/lane-00/../../bad.json"],
                }
            )


if __name__ == "__main__":
    unittest.main()
