import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from playwright_host.worker import WORKER_WATCHDOG_SECONDS, WorkerManager


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
        pool = {
            "v": 2,
            "owner_task_id": "parent-task",
            "owner_control_epoch": 1,
            "current": None,
            "managed": [],
            "handoff": None,
        }
        manager.state.lane.return_value = lane
        manager.state.get_pool.return_value = pool
        manager.state.set_pool = MagicMock()
        manager._trim_pool = MagicMock()

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

        next_pool = manager.state.set_pool.call_args.args[1]
        current = next_pool["current"]
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
        pool = {
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
            "task_pools": {"parent-task::1": pool},
        }
        manager.state.data = {"lanes": {"lane-01": lane}}
        manager.state.set_pool = MagicMock()
        return manager, pool, current

    def test_expired_watchdog_wakes_worker_helper_without_dom_dependency(self):
        manager, _pool, _current = self._expired_manager()
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

        next_pool = manager.state.set_pool.call_args.args[1]
        watchdog = next_pool["current"]["worker_watchdog"]
        self.assertEqual(watchdog["state"], "WORKER_HELPER_REQUESTED")
        self.assertEqual(watchdog["helper_request_id"], "worker-helper-request-001")

    def test_lease_liveness_before_deadline_does_not_reset_watchdog(self):
        manager, pool, current = self._expired_manager()
        watchdog = dict(current["worker_watchdog"])
        watchdog["started_at"] = "2999-01-01T00:00:00+00:00"
        watchdog["deadline_at"] = "2999-01-01T00:30:00+00:00"
        current["worker_watchdog"] = watchdog
        pool["managed"] = [current]

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
            "reason": "response_still_running_lease_renewed",
        }

        out = manager.liveness_sweep()

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"], "response_still_running_lease_renewed")
        manager.state.set_pool.assert_not_called()
        self.assertEqual(
            current["worker_watchdog"]["deadline_at"],
            "2999-01-01T00:30:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
