import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from playwright_host.worker import WORKER_WATCHDOG_SECONDS, WorkerManager


from harness.worker_lineage_compat import (
    WORKER_LINEAGES_KEY,
)


class WorkerWatchdogTests(unittest.TestCase):
    def test_create_worker_arms_30_minute_watchdog_at_response_start(self):
        manager = WorkerManager.__new__(WorkerManager)
        manager.project_id = "git-agent-harness"
        manager.bridge = MagicMock()
        manager.ui = MagicMock()
        manager.state = MagicMock()

        lane = {
            "lane_id": "lane-01",
            "project_key": "g-p-worker",
            "project_root_url": "https://chatgpt.com/g/g-p-worker/project",
            "enabled": True,
            "pending_remove": False,
        }
        lineage = {
            "v": 2,
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": None,
            "managed": [],
            "handoff": None,
        }
        manager.state.lane.return_value = lane
        manager.state.get_lineage.return_value = lineage
        manager.state.set_lineage = MagicMock()
        manager._trim_lineage = MagicMock()

        page = MagicMock()
        page.url = "https://chatgpt.com/g/g-p-worker/c/worker-conv-001"
        manager.ui.bootstrap_conversation.return_value = (
            page,
            "worker-conv-001",
            page.url,
        )
        manager.bridge.call.return_value = {"ok": True}
        manager.bridge.event = MagicMock()

        wake = {
            "v": 1,
            "wake_id": "wake-worker-watchdog-001",
            "project_id": "git-agent-harness",
            "task_id": "child-task-001",
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "backend_cl": "cl/child-task-001.backend.json",
            "dispatch_id": "dispatch-worker-watchdog-001",
            "dispatch_generation": 7,
            "fence_token": "fence-worker-watchdog-001",
        }

        result = manager.create_worker(
            "lane-01",
            "parent-task",
            1,
            wake,
            handoff_id="pool-worker-watchdog-001",
        )
        self.assertTrue(result["ok"], result)

        next_lineage = manager.state.set_lineage.call_args.args[1]
        current = next_lineage["current"]
        watchdog = current["worker_watchdog"]
        self.assertEqual(watchdog["kind"], "WORKER_HELPER")
        self.assertEqual(watchdog["state"], "ARMED")
        self.assertEqual(watchdog["timeout_seconds"], 30 * 60)
        self.assertEqual(WORKER_WATCHDOG_SECONDS, 30 * 60)
        self.assertEqual(watchdog["child_task_id"], "child-task-001")
        self.assertEqual(watchdog["dispatch_id"], "dispatch-worker-watchdog-001")
        self.assertEqual(watchdog["dispatch_generation"], 7)
        self.assertEqual(watchdog["fence_token"], "fence-worker-watchdog-001")
        started = datetime.fromisoformat(watchdog["started_at"])
        deadline = datetime.fromisoformat(watchdog["deadline_at"])
        self.assertEqual(int((deadline - started).total_seconds()), 30 * 60)

    def test_same_generation_retry_replaces_local_current_pointer_without_new_owner(self):
        manager = WorkerManager.__new__(WorkerManager)
        manager.project_id = "git-agent-harness"
        manager.bridge = MagicMock()
        manager.ui = MagicMock()
        manager.state = MagicMock()
        manager._trim_lineage = MagicMock()

        lane = {
            "lane_id": "lane-01",
            "project_key": "g-p-worker",
            "project_root_url": "https://chatgpt.com/g/g-p-worker/project",
            "enabled": True,
            "pending_remove": False,
        }
        old = {
            "conversation_id": "worker-old-conv",
            "url": "https://chatgpt.com/g/g-p-worker/c/worker-old-conv",
            "lane_id": "lane-01",
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "status": "current",
            "handoff_verified": True,
            "handoff_id": "pool-worker-same",
            "wake_id": "wake-same",
            "worker_watchdog": {"state": "SAME_GENERATION_RETRY_QUEUED"},
        }
        standby = {
            "conversation_id": "worker-standby",
            "status": "standby",
            "handoff_verified": True,
        }
        lineage = {
            "v": 2,
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": old,
            "managed": [standby, old],
            "handoff": None,
        }
        manager.state.lane.return_value = lane
        manager.state.get_lineage.return_value = lineage
        manager.state.set_lineage = MagicMock()

        page = MagicMock()
        page.url = "https://chatgpt.com/g/g-p-worker/c/worker-new-conv"
        manager.ui.bootstrap_conversation.return_value = (
            page,
            "worker-new-conv",
            page.url,
        )
        manager.bridge.call.return_value = {
            "ok": True,
            "accepted": False,
            "idle": "already_accepted",
            "state": "RUNNING",
        }
        manager.bridge.event = MagicMock()

        wake = {
            "v": 1,
            "wake_id": "wake-same",
            "project_id": "git-agent-harness",
            "task_id": "child-task-001",
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "backend_cl": "cl/child-task-001.backend.json",
            "dispatch_id": "dispatch-same",
            "dispatch_generation": 32,
            "fence_token": "fence-same-generation",
            "_same_generation_retry": {
                "request_id": "worker-retry-001",
                "helper_request_id": "worker-helper-001",
                "old_conversation_id": "worker-old-conv",
                "worker_ref": "pool-worker-same",
            },
        }
        result = manager.create_worker(
            "lane-01",
            "parent-task",
            1,
            wake,
            handoff_id="pool-worker-same",
        )
        self.assertTrue(result["ok"], result)
        next_lineage = manager.state.set_lineage.call_args.args[1]
        self.assertEqual(next_lineage["current"]["conversation_id"], "worker-new-conv")
        self.assertEqual(next_lineage["current"]["handoff_id"], "pool-worker-same")
        self.assertEqual(next_lineage["current"]["wake_id"], "wake-same")
        self.assertEqual(
            [x["conversation_id"] for x in next_lineage["managed"]],
            ["worker-standby", "worker-new-conv"],
        )
        self.assertEqual(
            next_lineage["physical_retry"]["from_conversation_id"],
            "worker-old-conv",
        )
        self.assertEqual(
            next_lineage["physical_retry"]["to_conversation_id"],
            "worker-new-conv",
        )
        self.assertEqual(next_lineage["physical_retry"]["dispatch_generation"], 32)

    def test_retry_reentry_completes_existing_replacement_without_third_chat(self):
        manager = WorkerManager.__new__(WorkerManager)
        manager.project_id = "git-agent-harness"
        manager.bridge = MagicMock()
        manager.ui = MagicMock()
        manager.state = MagicMock()

        retry = {
            "request_id": "worker-retry-001",
            "helper_request_id": "worker-helper-001",
            "old_conversation_id": "worker-old-conv",
            "worker_ref": "pool-worker-same",
        }
        current = {
            "conversation_id": "worker-new-conv",
            "url": "https://chatgpt.com/g/g-p-worker/c/worker-new-conv",
            "lane_id": "lane-01",
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "status": "current",
            "handoff_verified": True,
            "handoff_id": "pool-worker-same",
            "wake_id": "wake-same",
            "same_generation_retry": retry,
        }
        lineage = {
            "v": 2,
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": current,
            "managed": [current],
            "handoff": None,
            "physical_retry": {
                "request_id": "worker-retry-001",
                "from_conversation_id": "worker-old-conv",
                "to_conversation_id": "worker-new-conv",
            },
        }
        lane = {
            "lane_id": "lane-01",
            "project_key": "g-p-worker",
            "project_root_url": "https://chatgpt.com/g/g-p-worker/project",
            "enabled": True,
            "pending_remove": False,
        }
        manager.state.get_lineage.return_value = lineage
        manager.create_worker = MagicMock()
        manager._complete_same_generation_retry = MagicMock(
            return_value={"ok": True, "completed": True}
        )
        manager.bridge.call.side_effect = lambda op, **kwargs: (
            {
                "ok": True,
                "matched": True,
                "state": "RUNNING",
                "suppress": True,
            }
            if op == "dispatch_status"
            else {"ok": True}
        )

        wake = {
            "v": 1,
            "wake_id": "wake-same",
            "project_id": "git-agent-harness",
            "task_id": "child-task-001",
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "backend_cl": "cl/child-task-001.backend.json",
            "dispatch_id": "dispatch-same",
            "dispatch_generation": 32,
            "fence_token": "fence-same-generation",
            "_same_generation_retry": retry,
        }

        result = manager._route_claimed_wake(
            "lane-01",
            lane,
            "wake-same",
            wake,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["deduped_physical_worker"])
        manager.create_worker.assert_not_called()
        manager._complete_same_generation_retry.assert_called_once_with(
            wake,
            new_conversation_id="worker-new-conv",
        )

    def _expired_manager(self):
        manager = WorkerManager.__new__(WorkerManager)
        manager.bridge = MagicMock()
        manager.ui = MagicMock()
        manager.state = MagicMock()
        current = {
            "conversation_id": "worker-conv-expired",
            "url": "https://chatgpt.com/g/g-p-worker/c/worker-conv-expired",
            "lane_id": "lane-01",
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "status": "current",
            "handoff_verified": True,
            "handoff_id": "pool-worker-expired",
            "wake_id": "wake-expired",
            "worker_watchdog": {
                "kind": "WORKER_HELPER",
                "state": "ARMED",
                "timeout_seconds": 30 * 60,
                "started_at": "2000-01-01T00:00:00+00:00",
                "deadline_at": "2000-01-01T00:30:00+00:00",
                "child_task_id": "child-task-001",
                "backend_cl": "cl/child-task-001.backend.json",
                "dispatch_id": "dispatch-expired",
                "dispatch_generation": 7,
                "fence_token": "fence-expired",
                "wake_id": "wake-expired",
            },
        }
        lineage = {
            "v": 2,
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": current,
            "managed": [current],
            "handoff": None,
        }
        lane = {
            "lane_id": "lane-01",
            "project_key": "g-p-worker",
            "project_root_url": "https://chatgpt.com/g/g-p-worker/project",
            "enabled": True,
            "pending_remove": False,
            WORKER_LINEAGES_KEY: {"parent-task::1": lineage},
        }
        manager.state.data = {"lanes": {"lane-01": lane}}
        manager.state.set_lineage = MagicMock()
        return manager, lineage, current

    def test_expired_watchdog_wakes_worker_helper_without_dom_dependency(self):
        manager, _lineage, _current = self._expired_manager()
        manager.bridge.call.return_value = {
            "ok": True,
            "staged": True,
            "helper_request_id": "worker-helper-request-001",
            "helper_output_ref": "evidence/parent-task/roles/helper/worker-helper-request-001.json",
        }

        out = manager.liveness_sweep()

        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["worker_watchdog"])
        call = manager.bridge.call.call_args
        self.assertEqual(call.args[0], "worker_watchdog_expired")
        self.assertEqual(call.kwargs["parent_task_id"], "parent-task")
        self.assertEqual(call.kwargs["child_task_id"], "child-task-001")
        self.assertEqual(call.kwargs["dispatch_generation"], 7)
        self.assertEqual(call.kwargs["reason"], "worker_watchdog_30m")
        manager.ui.conversation_page.assert_not_called()
        manager.ui.dispatch_turn_state.assert_not_called()

        next_lineage = manager.state.set_lineage.call_args.args[1]
        watchdog = next_lineage["current"]["worker_watchdog"]
        self.assertEqual(watchdog["state"], "WORKER_HELPER_REQUESTED")
        self.assertEqual(watchdog["helper_request_id"], "worker-helper-request-001")

    def test_lease_liveness_before_deadline_does_not_reset_watchdog(self):
        manager, lineage, current = self._expired_manager()
        watchdog = dict(current["worker_watchdog"])
        watchdog["started_at"] = "2999-01-01T00:00:00+00:00"
        watchdog["deadline_at"] = "2999-01-01T00:30:00+00:00"
        current["worker_watchdog"] = watchdog
        lineage["managed"] = [current]

        page = MagicMock()
        manager.ui.conversation_page.return_value = page
        manager.ui.dispatch_turn_state.return_value = {
            "response_started": True,
            "response_ended": False,
            "dispatch_context": {
                "task_id": "child-task-001",
                "backend_cl": "cl/child-task-001.backend.json",
                "dispatch_id": "dispatch-expired",
                "dispatch_generation": 7,
                "fence_token": "fence-expired",
            },
        }
        manager._ensure_admitted = MagicMock(return_value={"ok": True, "matched": True})
        manager.bridge.call.return_value = {
            "ok": True,
            "matched": True,
            "recovered": False,
            "reason": "managed_worker_response_running_watchdog_owned",
        }

        out = manager.liveness_sweep()

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"], "managed_worker_response_running_watchdog_owned")
        manager.state.set_lineage.assert_not_called()
        self.assertEqual(
            current["worker_watchdog"]["deadline_at"],
            "2999-01-01T00:30:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
