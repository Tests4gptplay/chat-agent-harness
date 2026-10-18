"""Immediate mechanical-error delivery using the real Bridge and Helper lifecycle.

Only the authenticated browser UI is simulated. Mailboxes, exception conversion,
local bindings and (for completion) a disposable real Git repository are exercised.
"""
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_bridge.server import Handler, WakeStore
from playwright_host.bridge import BridgeClient, BridgeError
from playwright_host.config import HostConfig, TASK_CELL
from playwright_host.error_helper import ERROR_HELPER_OWNER
from playwright_host.runtime import BrowserRuntime
from playwright_host.worker import WorkerManager




from harness.worker_lineage_compat import (
    WORKER_LINEAGES_KEY,
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


class HarnessErrorHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WakeStore(self.root / "spool", repo_root=self.root / "repo")
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.store = self.store
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.http.server_port}/api"
        self.runtime = BrowserRuntime(HostConfig(bridge_url=self.url, runtime_root=self.root / "host"))
        self.runtime.ui = MagicMock()
        self.runtime.workers.ui = self.runtime.ui
        self.page = MagicMock()
        self.page.locator.return_value.count.return_value = 0
        self.page.url = TASK_CELL["project_root_url"] + "/c/error-helper-chat"
        self.runtime.ui.bootstrap_conversation.return_value = (self.page, "error-helper-chat", self.page.url)
        self.runtime.ui.find_page.return_value = self.page
        self.runtime.ui.marker_state.return_value = {"assistant_texts": []}
        self.runtime.ui.matching_pages.return_value = []
        self.runtime.ui.delete_project_conversation.return_value = True
        self.addCleanup(self.runtime.workers.mcp.close)

    def stop_server(self):
        self.http.shutdown()
        self.thread.join(timeout=3)
        self.http.server_close()

    def role(self):
        return self.runtime.state.get_role(ERROR_HELPER_OWNER, 1, "helper")

    def prompt(self):
        return self.runtime.ui.bootstrap_conversation.call_args.args[2]

    def seed_git(self):
        repo = self.store.repo_root
        repo.mkdir(parents=True, exist_ok=True)
        origin = self.root / "origin.git"
        subprocess.check_call(["git", "init", "--bare", "-q", str(origin)])
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "state").mkdir()
        (repo / "state/chatgpt.json").write_text(json.dumps({"active_task": None, "control_request": {"status": "PENDING", "request_id": "business-control"}}))
        git(repo, "add", "."); git(repo, "commit", "-qm", "seed")
        git(repo, "branch", "-M", "main"); git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-u", "origin", "main")
        return repo

    def write_result(self, repo, value):
        ref = self.role()["active_output_ref"]
        target = repo / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))
        git(repo, "add", ref); git(repo, "commit", "-qm", "helper result")
        git(repo, "push", "origin", "main")

    def test_first_error_opens_helper_now_with_source_and_exception(self):
        self.runtime.bridge.event("worker.observe_error", "error", operation="workers.observe",
                                  exception_type="RuntimeError", message="page closed during observation", task_id="owned-task")
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertIsNotNone(self.role())
        for value in ("worker.observe_error", "workers.observe", "RuntimeError", "page closed during observation", "owned-task"):
            self.assertIn(value, self.prompt())
        self.assertFalse((self.store.repo_root / "tasks").exists())
        self.assertFalse((self.store.repo_root / "state/task_cells").exists())

    def test_warning_is_only_telemetry(self):
        self.runtime.bridge.event("worker.status", "warn", message="healthy long build")
        self.runtime.ui.bootstrap_conversation.assert_not_called()
        self.assertEqual(self.store.harness_errors({"client_id": self.runtime.state.client_id})["errors"], [])

    def test_real_bridge_rejection_raises_and_immediately_opens_helper(self):
        with self.assertRaises(BridgeError):
            self.runtime.bridge.call("unknown_operation")
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertIn("unknown_operation", self.prompt())
        self.assertIn("UNKNOWN_OP", self.prompt())
        self.assertEqual(len(self.store.harness_errors({"client_id": self.runtime.state.client_id})["errors"]), 1)

    def test_bridge_outage_does_not_gate_helper_start(self):
        self.runtime.bridge.url = "http://127.0.0.1:1/api"
        with self.assertRaises(BridgeError):
            self.runtime.bridge.call("control_status")
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertIn("control_status", self.prompt())

    def test_external_error_mailbox_delivers_and_ack_preserves_new_errors(self):
        caller = BridgeClient(self.url, "other-runtime", "git-agent-harness")
        caller.event("harness.executor_error", "error", operation="owned-run", message="executor failed")
        self.runtime._poll_harness_errors()
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertIn("harness.executor_error", self.prompt())
        first_ack = self.runtime.state.data["harness_error_acks"][:]
        caller.event("harness.executor_error", "error", operation="owned-run", message="executor failed")
        fresh = self.store.harness_errors({"client_id": "other-runtime", "ack": first_ack})["errors"]
        self.assertEqual(len(fresh), 1)
        self.assertNotIn(fresh[0]["error_id"], first_ack)
        self.runtime._poll_harness_errors()
        self.runtime._poll_harness_errors()
        self.assertEqual(self.store.harness_errors({"client_id": "other-runtime"})["errors"], [])

    def test_more_errors_stay_with_the_existing_helper(self):
        for _ in range(3):
            self.runtime.bridge.event("worker.observe_error", "error", message="same failed page")
        self.runtime.bridge.event("playwright.control_error", "error", message="control also failed")
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertEqual(len(self.runtime.state.data["harness_error"]["errors"]), 2)

    def test_failed_helper_creation_reuses_request_without_recursive_spawn(self):
        self.runtime.ui.bootstrap_conversation.side_effect = RuntimeError("CDP disconnected")
        self.runtime.bridge.event("playwright.tick_error", "error", message="original failure")
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        request_id = self.runtime.state.data["harness_error"]["request_id"]
        self.runtime.ui.bootstrap_conversation.side_effect = None
        self.runtime._service_error_helper()
        self.assertEqual(self.role()["request_id"], request_id)
        self.assertEqual(self.runtime.ui.bootstrap_conversation.call_count, 2)

    def test_restart_reuses_existing_error_helper_binding(self):
        self.runtime.bridge.event("playwright.tick_error", "error", message="test failure")
        restarted = BrowserRuntime(HostConfig(bridge_url=self.url, runtime_root=self.root / "host"))
        self.addCleanup(restarted.workers.mcp.close)
        restarted.ui = MagicMock()
        restarted._service_error_helper()
        restarted.ui.bootstrap_conversation.assert_not_called()
        self.assertEqual(restarted.state.get_role(ERROR_HELPER_OWNER, 1, "helper")["request_id"], self.role()["request_id"])

    def test_persistent_observation_error_does_not_starve_control_or_watchdog(self):
        rt = self.runtime
        rt.workers = MagicMock()
        rt.workers.observe.side_effect = RuntimeError("broken observer")
        rt.workers.maintain.return_value = []
        rt.workers.liveness_sweep.return_value = []
        rt._process_semantic_tool_calls = MagicMock(return_value=[])
        rt._observe_foreground_terminal = MagicMock()
        rt.process_control = MagicMock(return_value={"ok": True})
        rt.state.data["lanes"] = {}
        with patch("playwright_host.runtime.time.monotonic", side_effect=[60.0, 120.0, 180.0]):
            for _ in range(3):
                rt.tick()
        self.assertEqual(rt.process_control.call_count, 3)
        self.assertEqual(rt.workers.liveness_sweep.call_count, 3)
        rt.ui.bootstrap_conversation.assert_called_once()

    def test_one_bad_worker_lineage_does_not_skip_other_lineages(self):
        manager = self.runtime.workers
        manager.state.data["lanes"] = {"lane-01": {WORKER_LINEAGES_KEY: {
            "task-a::1": {"owner_task_id": "task-a", "owner_control_epoch": 1},
            "task-b::1": {"owner_task_id": "task-b", "owner_control_epoch": 1},
        }}}
        manager.process_worker_page = MagicMock(side_effect=[RuntimeError("bad first page"), {"ok": True}])
        self.assertEqual(manager.observe(), [{"ok": True}])
        self.assertEqual(manager.process_worker_page.call_count, 2)
        self.runtime.ui.bootstrap_conversation.assert_called_once()

    def reconcile_outputs(self):
        result = self.runtime.bridge.call("planner_runtime_tick", bound_turns=self.runtime._bound_semantic_turns())
        self.runtime._finish_git_turns(result.get("semantic_turns") or [])
        return [row["result"] for row in result.get("semantic_turns") or []]

    def test_completed_helper_cleans_only_its_binding_after_real_git_result(self):
        repo = self.seed_git()
        before_state = (repo / "state/chatgpt.json").read_bytes()
        self.runtime.bridge.event("playwright.tick_error", "error", message="original error")
        self.runtime._poll_harness_errors(); self.runtime._poll_harness_errors()
        self.runtime.ui.marker_state.side_effect = AssertionError("completion must not read DOM")
        pending = self.reconcile_outputs()
        self.assertEqual(pending, [])
        self.runtime.ui.delete_project_conversation.assert_not_called()
        self.write_result(repo, {"diagnosis": "runtime error repaired", "repair_result": "idle operations verified", "turn_signal": "done"})
        result = self.reconcile_outputs()
        self.assertTrue(result[0]["accepted"])
        self.runtime.ui.delete_project_conversation.assert_called_once()
        self.assertIsNone(self.role())
        self.assertNotIn("harness_error", self.runtime.state.data)
        self.assertEqual((repo / "state/chatgpt.json").read_bytes(), before_state)
        self.assertFalse((repo / "state/task_cells").exists())

    def test_cleanup_failure_retries_exact_existing_helper_without_new_conversation(self):
        repo = self.seed_git()
        self.runtime.bridge.event("playwright.tick_error", "error", message="original error")
        self.write_result(repo, {"diagnosis": "fixed", "repair_result": "verified", "turn_signal": "done"})
        self.runtime.ui.marker_state.side_effect = AssertionError("completion must not read DOM")
        self.runtime.ui.delete_project_conversation.side_effect = [False, True]
        self.reconcile_outputs()
        self.assertIsNotNone(self.role())
        self.reconcile_outputs()
        self.assertIsNone(self.role())
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertEqual(self.runtime.ui.delete_project_conversation.call_count, 2)

    def test_result_write_not_done_does_not_close_helper(self):
        repo = self.seed_git()
        self.runtime.bridge.event("playwright.tick_error", "error", message="error remains")
        self.write_result(repo, {"diagnosis": "error identified", "repair_result": "blocked on external outage"})
        self.runtime.ui.marker_state.side_effect = AssertionError("completion must not read DOM")
        result = self.reconcile_outputs()
        self.assertEqual(result, [])
        self.assertIsNotNone(self.role())
        self.runtime.ui.delete_project_conversation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
