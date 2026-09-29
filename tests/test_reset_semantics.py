import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from playwright_host.config import TASK_CELL
from playwright_host.reset_semantics import reset_targets


ROOT = Path(__file__).resolve().parents[1]


class ResetSemanticsTests(unittest.TestCase):
    def test_reset_targets_include_task_cell_and_all_registered_lanes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "state").mkdir()
            (root / "state" / "lanes.json").write_text(json.dumps({
                "lanes": [
                    {
                        "lane_id": "lane-00",
                        "display_name": "CAH Sandbox0",
                        "project_key": "g-p-lane00",
                        "project_root_url": "https://chatgpt.com/g/g-p-lane00-cah-sandbox0/project",
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-lane01",
                        "project_root_url": "https://chatgpt.com/g/g-p-lane01-cah-sandbox1/project",
                    },
                ]
            }), encoding="utf-8")

            targets = reset_targets(root)

            self.assertEqual(targets[0]["project_key"], TASK_CELL["project_key"])
            self.assertEqual(
                [item["project_key"] for item in targets[1:]],
                ["g-p-lane00", "g-p-lane01"],
            )

    def test_sidebar_scoped_project_conversation_is_enumerated(self):
        from playwright_host.ui import ChatGPTUI

        project_key = "g-p-lane00"
        page = MagicMock()
        page.url = "https://chatgpt.com/g/g-p-lane00-cah-sandbox0/project"

        scoped = MagicMock()
        scoped.get_attribute.return_value = "/g/g-p-lane00/c/conversation-123"
        scoped.inner_text.return_value = "Worker task execution"

        other = MagicMock()
        other.get_attribute.return_value = "/g/g-p-other/c/conversation-999"
        other.inner_text.return_value = "Other"

        rows = MagicMock()
        rows.count.return_value = 2
        rows.nth.side_effect = lambda i: [scoped, other][i]
        page.locator.side_effect = lambda selector: (
            rows if selector == "a[href]:visible" else MagicMock()
        )

        ui = ChatGPTUI("http://127.0.0.1:9222")
        result = ui.list_project_conversations(page, project_key)

        self.assertEqual(
            result,
            [{
                "conversation_id": "conversation-123",
                "url": "https://chatgpt.com/g/g-p-lane00-cah-sandbox0/c/conversation-123",
                "title": "Worker task execution",
            }],
        )


    def test_reset_targets_dedupe_project_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "state").mkdir()
            (root / "state" / "lanes.json").write_text(json.dumps({
                "lanes": [
                    {
                        "lane_id": "lane-00",
                        "project_key": TASK_CELL["project_key"],
                        "project_root_url": TASK_CELL["project_root_url"],
                    }
                ]
            }), encoding="utf-8")

            targets = reset_targets(root)

            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["project_key"], TASK_CELL["project_key"])

    def test_reset_bat_clears_semantics_before_local_ownership(self):
        bat = (ROOT / "Reset_CAH_Hot_State.bat").read_text(encoding="utf-8")
        semantic = bat.index("reset_cah_semantics_cli.ps1")
        local_state = bat.index("Backing up and clearing browser-local CAH ownership state")
        self.assertLess(semantic, local_state)
        restart = bat.index("Restarting only the native Playwright Host")
        self.assertLess(semantic, restart)
        self.assertNotIn("playwright_host.reset_semantics", bat)

    def test_reset_contract_declares_zero_conversation_semantics(self):
        contract = (ROOT / "docs" / "task-cell" / "FOREGROUND.md").read_text(encoding="utf-8")
        self.assertIn("clears **all conversations inside those dedicated CAH Projects**", contract)
        self.assertIn("semantic-clear step itself reported every dedicated CAH Project at zero conversations", contract)


if __name__ == "__main__":
    unittest.main()
