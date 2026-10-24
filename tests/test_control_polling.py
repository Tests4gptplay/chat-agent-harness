"""Remote Git waits must not own the Playwright/UI thread."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from playwright_host.bridge import BridgeError
from playwright_host.config import HostConfig
from playwright_host.runtime import BrowserRuntime


class ControlPollingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.host = BrowserRuntime(HostConfig(runtime_root=Path(temporary.name)))
        self.host.ui = MagicMock()
        self.host.workers.ui = self.host.ui
        self.host._service_error_helper = MagicMock()
        self.addCleanup(self.host.close)
        self.owner = threading.get_ident()

    def bound_worker(self):
        host = self.host
        lane = host.state.lane("lane-00")
        lineage = host.state.get_lineage("lane-00", "parent-task", 1, create=True)
        ctx = {"task_id": "child-task", "backend_cl": "cl/child.backend.json",
               "dispatch_id": "dispatch-worker", "dispatch_generation": 1,
               "fence_token": "fence-worker"}
        current = {"status": "current", "handoff_verified": True,
                   "handoff_id": "pool-worker", "conversation_id": "worker-chat",
                   "wake_id": "wake-worker", "worker_watchdog": {
                       "state": "ARMED", "wake_id": "wake-worker", "child_task_id": ctx["task_id"],
                       **{key: ctx[key] for key in ctx if key != "task_id"}}}
        lineage["current"] = current
        lineage["managed"] = [current]
        host.state.set_lineage("lane-00", lineage)
        host.ui.dispatch_turn_state.return_value = {
            "dispatch_context": ctx, "response_started": True,
            "response_ended": False, "assistant_texts": [],
        }
        return lane, lineage, current, ctx

    @staticmethod
    def reconciliation(workers=()):
        return {"ok": True, "source_commit": "a" * 40, "planner_result": {"ok": True},
                "deliveries": [], "observations": [
                    {"identity_key": row["identity_key"],
                     "dispatch": {"ok": True, "matched": True, "state": "RUNNING"},
                     "takeover": {"ok": True, "rollover_requested": False},
                     "liveness": {"ok": True, "reason": "managed_worker_response_running_watchdog_owned"}}
                    for row in workers]}

    def test_slow_git_keeps_physical_observation_running_without_poll_backlog(self):
        self.bound_worker()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        polls = []
        ui_threads = []

        def call(op, **payload):
            if op == "control_poll":
                polls.append(threading.get_ident())
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test did not release blocked Git")
                return self.reconciliation(payload["workers"])
            if op == "harness_errors":
                return {"ok": True, "errors": []}
            if op == "claim":
                return {"ok": True, "wake": None}
            if op == "control_status":
                return {"ok": True, "control_request": None}
            raise AssertionError("unexpected synchronous Bridge operation: " + op)

        turn = self.host.ui.dispatch_turn_state.return_value
        self.host.ui.dispatch_turn_state.side_effect = lambda page: (ui_threads.append(threading.get_ident()) or turn)
        self.host.bridge.call = call
        self.host._process_semantic_tool_calls = MagicMock(return_value=[])
        self.host._observe_foreground_terminal = MagicMock()
        self.host._last_maintenance_tick = time.monotonic()
        started = time.monotonic()
        self.host.tick()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(entered.wait(1))
        for _ in range(20):
            self.host.tick()
        self.assertEqual(len(polls), 1)
        self.assertNotEqual(polls[0], self.owner)
        self.assertEqual(ui_threads, [self.owner] * 21)
        release.set()
        self.host._control_future.result(timeout=2)
        result = self.host.tick()
        self.assertTrue(result["observed_workers"][0]["response_running"])

    def test_completed_observation_cannot_cross_conversation_or_dispatch_identity(self):
        lane, lineage, current, ctx = self.bound_worker()
        manager = self.host.workers
        rows = self.reconciliation(manager.observation_requests())["observations"]
        manager.replace_observations(rows)
        self.assertTrue(manager._ensure_admitted(ctx, lane, current, lineage=lineage, observation=True)["matched"])
        self.host.bridge.call = MagicMock(side_effect=AssertionError("routine read touched Bridge"))
        for key, value in (("conversation_id", "replacement-chat"), ("handoff_id", "pool-replacement"),
                           ("wake_id", "replacement-wake")):
            changed = {**current, key: value}
            with self.subTest(key=key):
                self.assertIsNone(manager._ensure_admitted(ctx, lane, changed,
                                  lineage=lineage, observation=True)["matched"])
        for key, value in (("dispatch_generation", 2), ("fence_token", "new-fence"),
                           ("dispatch_id", "new-dispatch"), ("task_id", "other-child")):
            with self.subTest(key=key):
                self.assertIsNone(manager._ensure_admitted({**ctx, key: value}, lane, current,
                                  lineage=lineage, observation=True)["matched"])
        manager.replace_observations([])
        self.assertIsNone(manager._ensure_admitted(ctx, lane, current, lineage=lineage, observation=True)["matched"])
        self.host.bridge.call.assert_not_called()

    def test_first_admission_still_uses_fresh_status_and_accept(self):
        lane, lineage, current, ctx = self.bound_worker()
        self.host.bridge.call = MagicMock(side_effect=[
            {"ok": True, "matched": True, "state": "READY"},
            {"ok": True, "matched": True, "accepted": True},
        ])
        result = self.host.workers._ensure_admitted(ctx, lane, current)
        self.assertTrue(result["accepted"])
        self.assertEqual([call.args[0] for call in self.host.bridge.call.call_args_list],
                         ["dispatch_status", "dispatch_accept"])
        for call in self.host.bridge.call.call_args_list:
            self.assertNotIn("observation", call.kwargs)

    def test_reused_conversation_advances_observation_to_new_dispatch_before_response_end(self):
        lane, lineage, current, old_ctx = self.bound_worker()
        manager = self.host.workers
        old_requests = manager.observation_requests()
        manager.replace_observations(self.reconciliation(old_requests)["observations"])
        new_ctx = {**old_ctx, "dispatch_id": "dispatch-next", "dispatch_generation": 2,
                   "fence_token": "fence-next"}
        turn = self.host.ui.dispatch_turn_state.return_value
        turn["dispatch_context"] = new_ctx
        self.host.bridge.call = MagicMock(return_value={"ok": True, "matched": True})

        # The first physical observation learns the new binding without using
        # the preceding dispatch's cached authorization or performing effects.
        pending = manager.process_worker_page("lane-00", "parent-task", 1)
        self.assertEqual(pending["idle"], "git_snapshot_unavailable")
        self.host.bridge.call.assert_not_called()
        new_requests = manager.observation_requests()
        for key in ("dispatch_id", "dispatch_generation", "fence_token"):
            self.assertEqual(new_requests[0]["dispatch"][key], new_ctx[key])
        self.assertNotEqual(new_requests[0]["identity_key"], old_requests[0]["identity_key"])
        rows = self.reconciliation(new_requests)["observations"]
        manager.replace_observations(rows)
        self.assertIs(manager._observation(lane, lineage, current), rows[0])
        self.assertTrue(manager.process_worker_page("lane-00", "parent-task", 1)["response_running"])

        # Virtualization must recover that same newly observed binding from a
        # Tool-result continuation, rather than revert to the first watchdog.
        self.host.ui.dispatch_turn_state.return_value = None
        nodes = self.host.ui.message_nodes.return_value
        nodes.count.return_value = 1
        self.host.ui.message_role.return_value = "user"
        self.host.ui.message_text.return_value = "CAH_TOOL_RESULT v=1 id=existing-result"
        self.host.ui._turn_after.return_value = {
            "response_started": True, "response_ended": False, "superseded": False,
            "assistant_texts": [],
        }
        self.assertTrue(manager.process_worker_page("lane-00", "parent-task", 1)["response_running"])
        self.assertEqual(manager.observation_requests()[0]["dispatch"]["dispatch_generation"], 2)
        self.host.bridge.call.assert_not_called()

        turn["response_ended"] = True
        self.host.ui.dispatch_turn_state.return_value = turn
        completed = manager.process_worker_page("lane-00", "parent-task", 1)
        self.assertTrue(completed["response_ended"])
        call = self.host.bridge.call.call_args
        self.assertEqual(call.args, ("dispatch_liveness",))
        self.assertTrue(call.kwargs["response_ended"])
        self.assertNotIn("observation", call.kwargs)
        for key in ("dispatch_id", "dispatch_generation", "fence_token"):
            self.assertEqual(call.kwargs[key], new_ctx[key])
        self.assertEqual(current["worker_watchdog"]["dispatch_generation"], 1)

    def test_required_legacy_renewal_uses_same_background_worker(self):
        self.bound_worker()
        requests = self.host.workers.observation_requests()
        requests[0]["liveness"] = {**requests[0]["dispatch"], "response_running": True, "response_ended": False}
        calls = []

        def call(op, **payload):
            self.assertNotEqual(threading.get_ident(), self.owner)
            calls.append(op)
            if op == "control_poll":
                result = self.reconciliation(requests)
                result["observations"][0]["liveness"] = {"ok": True, "requires_fresh_liveness": True}
                return result
            self.assertEqual(op, "dispatch_liveness")
            self.assertNotIn("observation", payload)
            self.assertEqual(payload, requests[0]["liveness"])
            return {"ok": True, "lease_renewed": True}

        self.host.bridge.call = call
        self.host._submit_control_query("reconcile", self.host._query_control_reconciliation, [], requests)
        result = self.host._control_future.result(timeout=2)
        self.assertTrue(result["observations"][0]["liveness"]["lease_renewed"])
        self.assertEqual(calls, ["control_poll", "dispatch_liveness"])

    def test_real_http_async_phases_share_one_fetch_across_expired_refresh_interval(self):
        from tests.semantic_runtime_fixture import HarnessCase

        case = HarnessCase(idle=True, synchronous_control=False)
        self.addCleanup(case.close)
        original = case.store._git
        fetches = []

        def git(*args):
            if args[0] == "fetch":
                fetches.append(args)
            return original(*args)

        case.store._git = git
        runtime = case.runtime
        runtime.process_control()
        first = runtime._control_future.result(timeout=3)
        self.assertEqual(len(first["source_commit"]), 40)
        # The fixture has interval=0, so even an immediately following read
        # would refresh without the bounded immutable polling scope.
        case.clock.advance(5)
        runtime.process_control()
        status = runtime._control_future.result(timeout=3)
        self.assertIsNone(status["control_request"])
        self.assertEqual(len(fetches), 1)
        self.assertEqual(case.ui.created, [])

    def test_cleanup_and_terminal_ui_stay_on_owner_before_control_status(self):
        steps = []
        host = self.host

        def owner_step(name):
            self.assertEqual(threading.get_ident(), self.owner)
            steps.append(name)

        def call(op, **payload):
            self.assertNotEqual(threading.get_ident(), self.owner)
            if op == "control_poll":
                result = self.reconciliation()
                result["deliveries"] = [{"planner": True, "result": {"event": {"id": "terminal"}}}]
                return result
            if op == "control_status":
                self.assertEqual(steps, ["finish", "terminal", "watchdog"])
                self.assertEqual(payload["observed_source_commit"], "a" * 40)
                return {"ok": True, "control_request": {"kind": "test"}}
            raise AssertionError(op)

        host.bridge.call = call
        host._finish_git_turns = lambda turns: owner_step("finish")
        host._apply_terminal_status = lambda result, planner: owner_step("terminal")
        host._planner_watchdog_sweep = lambda: owner_step("watchdog")
        host._dispatch_control_request = lambda control: owner_step("dispatch") or {"ok": True, "handled": control}
        host.process_control()
        host._control_future.result(timeout=2)
        host.process_control()
        host._control_future.result(timeout=2)
        result = host.process_control()
        self.assertEqual(result["handled"], {"kind": "test"})
        self.assertEqual(steps, ["finish", "terminal", "watchdog", "dispatch"])

    def test_background_bridge_error_activates_helper_on_owner_and_poll_recovers(self):
        host = self.host
        failures = [True]
        incidents = []

        def request(op, **payload):
            if op == "control_poll" and failures:
                failures.pop()
                raise BridgeError("slow remote unavailable")
            return self.reconciliation() if op == "control_poll" else {"ok": True}

        host.bridge._request = request
        with patch("playwright_host.runtime.record_error",
                   side_effect=lambda *args: incidents.append(threading.get_ident())):
            host.process_control()
            with self.assertRaises(BridgeError):
                host._control_future.result(timeout=2)
            self.assertEqual(incidents, [])
            with self.assertRaises(BridgeError):
                host.process_control()
            self.assertEqual(incidents, [self.owner])
            host._service_error_helper.assert_called_once()
            host.process_control()
            self.assertTrue(host._control_future.result(timeout=2)["ok"])
            host.process_control()
            host._control_future.result(timeout=2)
            self.assertTrue(host.process_control()["ok"])

    def test_close_prevents_pending_worker_from_starting_another_phase(self):
        host = self.host
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def call(op, **payload):
            calls.append(op)
            entered.set()
            release.wait(3)
            return self.reconciliation()

        host.bridge.call = call
        host.process_control()
        self.assertTrue(entered.wait(1))
        future = host._control_future
        host.close()
        release.set()
        future.result(timeout=2)
        self.assertEqual(host.process_control()["idle"], "control_poll_closed")
        self.assertEqual(calls, ["control_poll"])


if __name__ == "__main__":
    unittest.main()
