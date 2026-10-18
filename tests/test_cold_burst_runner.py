import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ColdBurstRunnerContractTests(unittest.TestCase):
    def test_startup_reports_but_does_not_control_cold_runner(self):
        text = (ROOT / "Start_CAH.ps1").read_text(encoding="utf-8")
        self.assertIn("$ColdBurstRoot = '__CAH_COLD_RUNNER_ROOT__'", text)
        self.assertIn("$ColdBurstName = '__CAH_COLD_RUNNER_NAME__'", text)

        block = text.split("function Report-ColdBurstRunner", 1)[1].split("function Read-Cdp", 1)[0]
        self.assertNotIn("Start-Process", block)
        self.assertNotIn("Stop-Process", block)
        for state in ("READY_OFF", "ACTIVE_IDLE", "ACTIVE_BUSY", "DIRTY", "MISSING"):
            self.assertIn(state, block)

    def test_cold_workflow_has_dedicated_runner_and_concurrency(self):
        text = (ROOT / ".github" / "workflows" / "managed-host-script-cold.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("requests/managed-host-script-cold/*.json", text)
        self.assertIn("runs-on: [self-hosted, Windows, X64, backup]", text)
        self.assertIn("group: gah-managed-host-script-cold", text)

    def test_role_contract_preserves_capacity_ownership(self):
        planner = (ROOT / "docs" / "task-cell" / "PLANNER.md").read_text(encoding="utf-8")
        worker = (ROOT / "docs" / "task-cell" / "WORKER.md").read_text(encoding="utf-8")
        helper = (ROOT / "docs" / "task-cell" / "HELPER.md").read_text(encoding="utf-8")

        self.assertIn("## COLD_BURST RUNNER", worker)
        self.assertIn("READY_OFF", planner)
        self.assertIn("ACTIVE_BUSY", helper)
        self.assertIn("cah-shot", helper)

    def test_obsolete_runner_control_protocol_is_absent(self):
        self.assertFalse((ROOT / "host" / "backup_runner.ps1").exists())
        active_contracts = (
            "AGENTS.md",
            "docs/task-cell/PLANNER.md",
            "docs/task-cell/WORKER.md",
            "docs/task-cell/HELPER.md",
            "installation/README.md",
            "local_bridge/planner_runtime.py",
        )
        combined = "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in active_contracts)
        self.assertNotIn("host/backup_runner.ps1", combined)
        self.assertNotIn("cah-cold-rescue", combined)


if __name__ == "__main__":
    unittest.main()
