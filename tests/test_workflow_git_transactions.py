import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowGitTransactionTests(unittest.TestCase):
    def test_no_workflow_local_rebase_push_sequence(self):
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            for forbidden in (
                "git rebase origin/main",
                "git push origin HEAD:main",
            ):
                if forbidden in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {forbidden}")
        self.assertEqual(
            offenders,
            [],
            "workflows must use harness/git_publish.py instead of raw canonical publish: "
            + "; ".join(offenders),
        )

    def test_shared_helper_is_used_by_maintained_writers(self):
        expected = {
            "action-submit-recovery.yml",
            "codex-skill-sync.yml",
            "host-capability-probe.yml",
            "stage0-single-thread.yml",
        }
        missing = []
        for name in sorted(expected):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            normalized = text.replace("\\", "/")
            if 'harness/git_publish.py' not in normalized:
                missing.append(name)
        self.assertEqual(missing, [], f"maintained Git writer(s) bypass transaction helper: {missing}")


if __name__ == "__main__":
    unittest.main()
