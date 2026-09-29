import unittest
from unittest.mock import MagicMock, patch

from playwright_host.ui import ChatGPTUI
from playwright_host.worker import WorkerManager


class ProjectConversationAbsenceTests(unittest.TestCase):
    def setUp(self):
        self.ui = ChatGPTUI("http://127.0.0.1:9222")
        self.page = MagicMock()
        main = MagicMock()
        self.page.locator.return_value = main
        main.first.wait_for.return_value = None

    def test_absence_requires_known_sibling_witness(self):
        calls = {"n": 0}

        def listed(_page, _project):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [{"conversation_id": "current-chat"}]

        self.ui.list_project_conversations = MagicMock(side_effect=listed)
        result = self.ui.confirm_project_conversation_absent(
            self.page,
            "g-p-test",
            "deleted-standby",
            witness_conversation_ids=["current-chat", "other-standby"],
            timeout_ms=1000,
        )
        self.assertTrue(result)
        self.page.reload.assert_called_once_with(wait_until="domcontentloaded", timeout=30000)

    def test_present_victim_is_never_reconciled_as_absent(self):
        self.ui.list_project_conversations = MagicMock(return_value=[
            {"conversation_id": "deleted-standby"},
            {"conversation_id": "current-chat"},
        ])
        result = self.ui.confirm_project_conversation_absent(
            self.page,
            "g-p-test",
            "deleted-standby",
            witness_conversation_ids=["current-chat"],
            timeout_ms=1000,
        )
        self.assertFalse(result)

    def test_unproven_empty_list_fails_closed(self):
        self.ui.list_project_conversations = MagicMock(return_value=[])
        with patch("playwright_host.ui.time.monotonic", side_effect=[0.0, 0.0, 2.0]):
            result = self.ui.confirm_project_conversation_absent(
                self.page,
                "g-p-test",
                "deleted-standby",
                witness_conversation_ids=["current-chat"],
                timeout_ms=1000,
            )
        self.assertFalse(result)


class WorkerRetentionTests(unittest.TestCase):
    def _manager(self, confirm_absent):
        manager = WorkerManager.__new__(WorkerManager)
        manager.bridge = MagicMock()
        manager.state = MagicMock()
        manager.ui = MagicMock()
        manager.project_id = "git-agent-harness"

        lane = {
            "lane_id": "lane-01",
            "project_key": "g-p-test",
            "project_root_url": "https://chatgpt.com/g/g-p-test/project",
            "enabled": True,
        }
        manager.state.lane.return_value = lane

        managed = [
            {
                "conversation_id": f"standby-{idx}",
                "status": "standby",
                "handoff_verified": True,
                "created_at": f"2026-09-28T00:0{idx}:00Z",
            }
            for idx in range(5)
        ]
        managed.append({
            "conversation_id": "current-chat",
            "status": "current",
            "handoff_verified": True,
            "created_at": "2026-09-28T01:00:00Z",
        })
        pool = {
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": managed[-1],
            "managed": managed,
        }
        manager.state.get_pool.return_value = pool
        manager.ui.project_root.return_value = MagicMock()
        manager.ui.delete_project_conversation.return_value = False
        manager.ui.confirm_project_conversation_absent.return_value = confirm_absent
        manager.ui.matching_pages.return_value = []
        return manager, pool

    def test_confirmed_external_delete_prunes_stale_standby(self):
        manager, pool = self._manager(True)
        manager._trim_pool("lane-01", "parent-task", 1)

        remaining = [item["conversation_id"] for item in pool["managed"]]
        self.assertEqual(len(remaining), 5)
        self.assertNotIn("standby-0", remaining)
        manager.state.set_pool.assert_called_once()
        manager.bridge.event.assert_called_once()
        self.assertTrue(manager.bridge.event.call_args.kwargs["already_absent"])

    def test_unproven_absence_preserves_retention_record(self):
        manager, pool = self._manager(False)
        manager._trim_pool("lane-01", "parent-task", 1)

        remaining = [item["conversation_id"] for item in pool["managed"]]
        self.assertEqual(len(remaining), 6)
        self.assertIn("standby-0", remaining)
        manager.bridge.event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
