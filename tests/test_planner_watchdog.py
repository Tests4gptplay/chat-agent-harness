import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "lean_fixtures", REPO / "tests/test_taskcell_lean.py"
)
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)

from local_bridge import planner_runtime as rt
from local_bridge.planner_runtime import _rotation_due
from playwright_host.runtime import BrowserRuntime, PLANNER_WATCHDOG_SECONDS
from playwright_host.config import TASK_CELL


class PlannerWatchdogBrowserTests(unittest.TestCase):
    def _runtime(self):
        runtime = BrowserRuntime.__new__(BrowserRuntime)
        runtime.bridge = MagicMock()
        runtime.state = MagicMock()
        runtime.ui = MagicMock()
        runtime.cfg = SimpleNamespace(project_id="git-agent-harness")
        return runtime

    def _role(self):
        return {
            "v": 1,
            "task_id": "task-001",
            "control_epoch": 1,
            "role": "planner",
            "conversation_id": "planner-old",
            "url": "https://chatgpt.com/g/g-p-task-cell/c/planner-old",
            "request_id": "planner-request-001",
            "active_request_id": "planner-request-001",
            "active_output_ref": "evidence/task-001/roles/planner/out.json",
            "challenge": "planner-challenge-001",
            "planner_generation": 3,
            "planner_fence_token": "planner-fence-003",
        }

    def _armed(self, runtime):
        return runtime._arm_planner_watchdog(
            self._role(),
            request_id="planner-request-001",
            output_ref="evidence/task-001/roles/planner/out.json",
            generation=3,
            fence_token="planner-fence-003",
            wake_text="GAH_WAKE v=1 id=planner-request-001 project=git-agent-harness\noriginal wake",
        )

    def test_arm_planner_watchdog_is_fixed_30_minutes(self):
        runtime = self._runtime()
        armed = self._armed(runtime)
        watchdog = armed["planner_watchdog"]
        self.assertEqual(PLANNER_WATCHDOG_SECONDS, 30 * 60)
        self.assertEqual(watchdog["kind"], "PLANNER_PHYSICAL_WATCHDOG")
        self.assertEqual(watchdog["timeout_seconds"], 30 * 60)
        started = runtime._planner_watchdog_deadline(watchdog["started_at"])
        deadline = runtime._planner_watchdog_deadline(watchdog["deadline_at"])
        self.assertIsNotNone(started)
        self.assertIsNotNone(deadline)
        self.assertEqual(int((deadline - started).total_seconds()), 30 * 60)
        self.assertIn("original wake", watchdog["wake_text"])

    def test_expired_planner_watchdog_replays_same_conversation_without_helper(self):
        runtime = self._runtime()
        armed = self._armed(runtime)
        armed["planner_watchdog"]["started_at"] = "2000-01-01T00:00:00+00:00"
        armed["planner_watchdog"]["deadline_at"] = "2000-01-01T00:30:00+00:00"
        runtime.state.data = {
            "task_cell": {"roles": {"task-001|1|planner": armed}}
        }
        runtime.bridge.call.side_effect = lambda op, **kwargs: (
            {"ok": True, "active": True, "prompt_count": 7, "max_prompts_per_generation": 10}
            if op == "planner_watchdog_status"
            else {"ok": True, "recorded": True, "prompt_count": 8, "max_prompts_per_generation": 10}
            if op == "planner_watchdog_replay_started"
            else {"ok": False, "error": f"unexpected:{op}"}
        )
        page = MagicMock()
        runtime.ui.conversation_page.return_value = page
        runtime.ui.stop_response.return_value = {"stopped": True, "already_idle": False}
        runtime.ui.submit_text.return_value = {"ok": True, "response_started": True}

        out = runtime._planner_watchdog_sweep()

        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["result"]["recovered"], out)
        self.assertEqual(out[0]["result"]["mode"], "same_conversation_replay")
        runtime.ui.stop_response.assert_called_once_with(page)
        self.assertEqual(runtime.ui.bootstrap_conversation.call_count, 0)
        ops = [call.args[0] for call in runtime.bridge.call.call_args_list]
        self.assertEqual(
            ops,
            ["planner_watchdog_status", "planner_watchdog_replay_started"],
        )
        saved = runtime.state.set_role.call_args.args[0]
        self.assertEqual(saved["conversation_id"], "planner-old")
        self.assertEqual(saved["planner_generation"], 3)
        self.assertEqual(saved["planner_fence_token"], "planner-fence-003")
        self.assertEqual(saved["planner_watchdog"]["state"], "ARMED")
        self.assertEqual(saved["planner_watchdog"]["replay_attempt"], 1)
        self.assertEqual(saved["watchdog_recovery"]["prompt_count"], 8)

    def test_replay_failure_replaces_only_physical_conversation(self):
        runtime = self._runtime()
        armed = self._armed(runtime)
        runtime.bridge.call.side_effect = lambda op, **kwargs: (
            {"ok": True, "active": True, "prompt_count": 8, "max_prompts_per_generation": 10}
            if op == "planner_watchdog_status"
            else {"ok": True, "recorded": True, "prompt_count": 9, "max_prompts_per_generation": 10}
            if op == "planner_watchdog_replay_started"
            else {"ok": False, "error": f"unexpected:{op}"}
        )
        runtime.ui.conversation_page.return_value = MagicMock()
        runtime.ui.stop_response.return_value = {"stopped": True, "already_idle": False}
        runtime.ui.submit_text.side_effect = RuntimeError("composer unavailable")
        runtime._delete_exact = MagicMock(return_value=True)
        runtime.ui.bootstrap_conversation.return_value = (
            MagicMock(),
            "planner-new",
            "https://chatgpt.com/g/g-p-task-cell/c/planner-new",
        )

        result = runtime._recover_planner_watchdog(armed, armed["planner_watchdog"])

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "physical_conversation_replacement")
        self.assertEqual(result["old_conversation_id"], "planner-old")
        self.assertEqual(result["new_conversation_id"], "planner-new")
        saved = runtime.state.set_role.call_args.args[0]
        self.assertEqual(saved["conversation_id"], "planner-new")
        self.assertEqual(saved["planner_generation"], 3)
        self.assertEqual(saved["planner_fence_token"], "planner-fence-003")
        self.assertEqual(saved["active_request_id"], "planner-request-001")
        self.assertEqual(saved["watchdog_recovery"]["prompt_count"], 9)
        runtime._delete_exact.assert_called_once_with(
            TASK_CELL["project_key"],
            TASK_CELL["project_root_url"],
            "planner-old",
        )

    def test_recovery_failure_rearms_another_30_minute_cycle(self):
        runtime = self._runtime()
        armed = self._armed(runtime)
        armed["planner_watchdog"]["started_at"] = "2000-01-01T00:00:00+00:00"
        armed["planner_watchdog"]["deadline_at"] = "2000-01-01T00:30:00+00:00"
        runtime.state.data = {
            "task_cell": {"roles": {"task-001|1|planner": armed}}
        }
        runtime._recover_planner_watchdog = MagicMock(return_value={
            "ok": False,
            "error": "PLANNER_WATCHDOG_RECOVERY_FAILED",
            "replay_error": "temporary replay failure",
            "replacement_error": "temporary replacement failure",
        })

        out = runtime._planner_watchdog_sweep()

        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["ok"])
        saved = runtime.state.set_role.call_args.args[0]
        watchdog = saved["planner_watchdog"]
        self.assertEqual(watchdog["state"], "ARMED")
        self.assertEqual(watchdog["recovery_failure_count"], 1)
        self.assertEqual(watchdog["last_error"], "PLANNER_WATCHDOG_RECOVERY_FAILED")
        started = runtime._planner_watchdog_deadline(watchdog["started_at"])
        deadline = runtime._planner_watchdog_deadline(watchdog["deadline_at"])
        self.assertIsNotNone(started)
        self.assertIsNotNone(deadline)
        self.assertEqual(int((deadline - started).total_seconds()), 30 * 60)
        self.assertNotEqual(watchdog.get("state"), "RECOVERY_FAILED")

    def test_prompt_ceiling_rotates_only_after_current_turn_is_clear(self):
        due, reason = _rotation_due({
            "runtime": {
                "prompt_count": 10,
                "max_prompts_per_generation": 10,
            }
        })
        self.assertTrue(due)
        self.assertEqual(reason, "prompt_ceiling")
        due, reason = _rotation_due({
            "runtime": {
                "prompt_count": 9,
                "max_prompts_per_generation": 10,
            }
        })
        self.assertFalse(due)
        self.assertIsNone(reason)


class PlannerWatchdogGitTests(unittest.TestCase):
    def setUp(self):
        self.f = fx.RuntimeGitTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

        cell = copy.deepcopy(self.f.cell)
        control = cell["planner_control"]
        control["activity"] = "WAKING"
        authority = control["authority"]
        runtime = control.setdefault("runtime", {})
        runtime["prompt_count"] = 7
        runtime["max_prompts_per_generation"] = 10
        runtime["pending_output"] = {
            "kind": "PLANNER_TURN",
            "ref": self.f.request["required_output_ref"],
            "request_id": self.f.request["request_id"],
            "conversation_id": "planner-old",
            "status": "WAITING",
            "started_at": "2026-09-29T00:00:00Z",
            "wake_prompt": "original planner work order",
        }
        cell["roles"]["planner"].update({
            "conversation_id": "planner-old",
            "challenge": authority["challenge"],
            "planner_generation": authority["planner_generation"],
            "planner_fence_token": authority["planner_fence_token"],
        })
        self.f.commit({
            fx.CELL: cell,
            "state/chatgpt.json": {
                "v": 1,
                "control_request": {**self.f.request, "status": "DONE"},
            },
        })

    def _watchdog_req(self):
        cell = self.f.read(fx.CELL)
        authority = cell["planner_control"]["authority"]
        pending = cell["planner_control"]["runtime"]["pending_output"]
        return {
            **self.f.api,
            "task_id": fx.TASK,
            "control_epoch": 1,
            "conversation_id": "planner-old",
            "request_id": pending["request_id"],
            "output_ref": pending["ref"],
            "planner_generation": authority["planner_generation"],
            "planner_fence_token": authority["planner_fence_token"],
        }

    def test_status_accepts_only_current_unresolved_planner_turn(self):
        status = rt.planner_watchdog_status(self.f.store, self._watchdog_req())
        self.assertTrue(status["ok"], status)
        self.assertTrue(status["active"], status)
        self.assertEqual(status["prompt_count"], 7)
        self.assertEqual(status["max_prompts_per_generation"], 10)

        changed = self.f.read(fx.CELL)
        changed["planner_control"]["runtime"]["pending_output"]["status"] = "READY"
        self.f.commit({fx.CELL: changed})
        stale = rt.planner_watchdog_status(self.f.store, self._watchdog_req())
        self.assertTrue(stale["ok"], stale)
        self.assertFalse(stale["active"], stale)

    def test_status_suppresses_replay_after_turn_signal_is_durable(self):
        req = self._watchdog_req()
        self.f.commit({
            req["output_ref"]: {
                "turn_signal": "done",
                "outcome": "CONTINUE",
            },
        })
        status = rt.planner_watchdog_status(self.f.store, req)
        self.assertTrue(status["ok"], status)
        self.assertFalse(status["active"], status)
        self.assertEqual(status["idle"], "planner_watchdog_output_already_ready")

    def test_same_conversation_replay_preserves_generation_and_increments_count(self):
        req = self._watchdog_req()
        result = rt.record_planner_watchdog_replay(self.f.store, {
            **req,
            "replay_id": "planner-watchdog-replay-001",
            "old_conversation_id": "planner-old",
            "new_conversation_id": "planner-old",
            "new_conversation_url": "https://chatgpt.com/c/planner-old",
            "replacement": False,
        })
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["recorded"], result)
        self.assertEqual(result["prompt_count"], 8)

        cell = self.f.read(fx.CELL)
        control = cell["planner_control"]
        self.assertEqual(control["authority"]["conversation_id"], "planner-old")
        self.assertEqual(control["authority"]["planner_generation"], 1)
        self.assertEqual(
            control["authority"]["planner_fence_token"],
            self.f.cell["planner_control"]["authority"]["planner_fence_token"],
        )
        self.assertEqual(control["runtime"]["prompt_count"], 8)
        self.assertEqual(
            control["runtime"]["pending_output"]["conversation_id"],
            "planner-old",
        )

        duplicate = rt.record_planner_watchdog_replay(self.f.store, {
            **req,
            "replay_id": "planner-watchdog-replay-001",
            "old_conversation_id": "planner-old",
            "new_conversation_id": "planner-old",
            "new_conversation_url": "https://chatgpt.com/c/planner-old",
            "replacement": False,
        })
        self.assertTrue(duplicate["ok"], duplicate)
        self.assertTrue(duplicate["duplicate"], duplicate)
        self.assertEqual(duplicate["prompt_count"], 8)

    def test_physical_replacement_switches_only_conversation_pointer(self):
        req = self._watchdog_req()
        result = rt.record_planner_watchdog_replay(self.f.store, {
            **req,
            "replay_id": "planner-watchdog-replay-002",
            "old_conversation_id": "planner-old",
            "new_conversation_id": "planner-new",
            "new_conversation_url": "https://chatgpt.com/c/planner-new",
            "replacement": True,
        })
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["replacement"], result)
        self.assertEqual(result["planner_generation"], 1)
        self.assertEqual(result["prompt_count"], 8)

        cell = self.f.read(fx.CELL)
        control = cell["planner_control"]
        self.assertEqual(control["authority"]["conversation_id"], "planner-new")
        self.assertEqual(control["authority"]["planner_generation"], 1)
        self.assertEqual(
            control["authority"]["planner_fence_token"],
            self.f.cell["planner_control"]["authority"]["planner_fence_token"],
        )
        self.assertEqual(
            control["runtime"]["pending_output"]["conversation_id"],
            "planner-new",
        )
        self.assertEqual(cell["roles"]["planner"]["conversation_id"], "planner-new")


if __name__ == "__main__":
    unittest.main()
