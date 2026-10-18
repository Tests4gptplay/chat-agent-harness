import unittest
from unittest.mock import MagicMock, patch

from playwright_host.ui import ChatGPTUI
from playwright_host.worker import WorkerManager


from harness.worker_lineage_compat import (
    WORKER_LINEAGES_KEY,
)


class ProjectConversationAbsenceTests(unittest.TestCase):
    def setUp(self):
        self.ui = ChatGPTUI("http://127.0.0.1:9222")
        self.page = MagicMock()
        main = MagicMock()
        self.page.locator.return_value = main
        main.first.wait_for.return_value = None

    def test_absence_can_use_known_sibling_witness(self):
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

    def test_single_chat_lineage_confirms_absence_after_two_observations(self):
        self.ui.list_project_conversations = MagicMock(return_value=[])
        with patch("playwright_host.ui.time.monotonic", side_effect=[0.0, 0.0, 0.1]):
            result = self.ui.confirm_project_conversation_absent(
                self.page,
                "g-p-test",
                "deleted-standby",
                witness_conversation_ids=[],
                timeout_ms=1000,
            )
        self.assertTrue(result)
        self.assertGreaterEqual(self.ui.list_project_conversations.call_count, 2)


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
        lineage = {
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": managed[-1],
            "managed": managed,
        }
        manager.state.get_lineage.return_value = lineage
        manager.ui.project_root.return_value = MagicMock()
        manager.ui.delete_project_conversation.return_value = False
        manager.ui.confirm_project_conversation_absent.return_value = confirm_absent
        manager.ui.matching_pages.return_value = []
        return manager, lineage

    def test_confirmed_external_delete_prunes_stale_standby(self):
        manager, lineage = self._manager(True)
        manager._trim_lineage("lane-01", "parent-task", 1)

        remaining = [item["conversation_id"] for item in lineage["managed"]]
        self.assertEqual(len(remaining), 5)
        self.assertNotIn("standby-0", remaining)
        manager.state.set_lineage.assert_called_once()
        manager.bridge.event.assert_called_once()
        self.assertTrue(manager.bridge.event.call_args.kwargs["already_absent"])

    def test_unproven_absence_preserves_retention_record(self):
        manager, lineage = self._manager(False)
        manager._trim_lineage("lane-01", "parent-task", 1)

        remaining = [item["conversation_id"] for item in lineage["managed"]]
        self.assertEqual(len(remaining), 6)
        self.assertIn("standby-0", remaining)
        manager.bridge.event.assert_not_called()



class AdmissionRetentionTests(unittest.TestCase):
    def test_retention_close_failure_preserves_admitted_current_and_other_lineage(self):
        import copy
        import tempfile
        from pathlib import Path
        from playwright_host.state import HostState
        with tempfile.TemporaryDirectory() as td:
            state=HostState(Path(td)/'host.json')
            state.data['lanes']={'lane-01':{'lane_id':'lane-01','project_key':'g-p-worker',
                'project_root_url':'https://chatgpt.com/g/g-p-worker/project','enabled':True,WORKER_LINEAGES_KEY:{}}}
            old={'conversation_id':'old-worker','handoff_id':'pool-old','wake_id':'wake-old',
                 'status':'current','handoff_verified':True,'created_at':'2026-01-01T00:00:00Z'}
            standby=[{'conversation_id':f'standby-{i}','status':'standby','handoff_verified':True,
                      'created_at':f'2025-12-01T00:0{i}:00Z'} for i in range(4)]
            state.set_lineage('lane-01',{'owner_task_id':'task-owner','owner_control_epoch':1,
                'current':old,'managed':standby+[old]})
            state.set_lineage('lane-01',{'owner_task_id':'other-task','owner_control_epoch':1,
                'current':{'conversation_id':'other-worker'},'managed':[]})
            other=copy.deepcopy(state.get_lineage('lane-01','other-task',1))
            manager=WorkerManager.__new__(WorkerManager)
            manager.state=state;manager.bridge=MagicMock();manager.ui=MagicMock();manager.project_id='git-agent-harness'
            page=MagicMock();page.url='https://chatgpt.com/g/g-p-worker/c/new-worker'
            manager.ui.bootstrap_conversation.return_value=(page,'new-worker',page.url)
            manager.ui.delete_project_conversation.return_value=True
            closing=MagicMock();closing.close.side_effect=RuntimeError('standby page was already closed')
            manager.ui.matching_pages.return_value=[closing]
            manager.bridge.call.return_value={'ok':True,'accepted':True}
            wake={'v':1,'wake_id':'wake-new','project_id':'git-agent-harness','task_id':'child-new',
                  'owner_task_id':'task-owner','owner_control_epoch':1,'backend_cl':'cl/child-new.backend.json',
                  'dispatch_id':'dispatch-new','dispatch_generation':6,'fence_token':'fence-new'}
            result=manager.create_worker('lane-01','task-owner',1,wake)
            self.assertTrue(result['ok'],result)
            self.assertEqual(state.get_lineage('lane-01','task-owner',1)['current']['conversation_id'],'new-worker')
            self.assertEqual(state.get_lineage('lane-01','other-task',1),other)
            self.assertEqual(manager.bridge.call.call_args.args[0],'dispatch_accept')
            self.assertTrue(any(c.args[0]=='worker.retention_error' for c in manager.bridge.event.call_args_list))
            # A later cleanup reconciles the absent standby; admission stays intact.
            manager.ui.delete_project_conversation.return_value=False
            manager.ui.confirm_project_conversation_absent.return_value=True
            manager.ui.matching_pages.return_value=[]
            manager._trim_lineage('lane-01','task-owner',1)
            lineage=state.get_lineage('lane-01','task-owner',1)
            self.assertEqual(len(lineage['managed']),5)
            self.assertEqual(lineage['current']['conversation_id'],'new-worker')
            manager.ui.bootstrap_conversation.assert_called_once()

if __name__ == "__main__":
    unittest.main()
