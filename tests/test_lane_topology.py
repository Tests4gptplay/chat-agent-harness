import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.wake import make_wake, validate_wake
from local_bridge.server import WakeStore
from local_bridge.topology import stage_topology_request, topology_status


class LaneTopologyTests(unittest.TestCase):
    def test_lane_addressed_wake(self):
        wake = make_wake(
            "git-agent-harness",
            repo="example-owner/cah-private",
            result_ref="state/topology_request.json",
            lane_id="lane-00",
            worker_project_key="g-p-examplelane00",
            kind="topology_reconcile",
        )
        validate_wake(wake)
        self.assertEqual(wake["lane_id"], "lane-00")
        self.assertEqual(wake["kind"], "topology_reconcile")

    def test_topology_request_is_git_backed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])
            (work / "state").mkdir()
            (work / "state" / "chatgpt.json").write_text(json.dumps({
                "v": 1,
                "agent": "chatgpt",
                "updated": "2026-09-18",
                "phase": "DONE",
                "current": [],
                "verified": [],
                "unresolved": [],
                "fault_boundary": "",
                "next_reads": [],
                "next_action": "resume-me",
                "writeback_reason": "seed",
            }), encoding="utf-8")
            (work / "state" / "lanes.json").write_text(json.dumps({
                "v": 1,
                "topology_version": 1,
                "updated_at": "2026-09-18T00:00:00Z",
                "source_request_id": None,
                "registered_count": 1,
                "enabled_count": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "display_name": "CAH Sandbox0",
                    "project_key": "g-p-examplelane00",
                    "project_root_url": "https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project",
                    "enabled": True,
                    "status": "IDLE",
                    "last_pool_takeover_id": None,
                    "worker_rollover_request": None,
                }],
            }), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "state"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            result = stage_topology_request(store, {
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "request_id": "topo-12345678",
                "desired_version": 2,
                "control_lane_id": "lane-00",
                "worker_project_key": "g-p-examplelane00",
                "lanes": [
                    {
                        "lane_id": "lane-00",
                        "display_name": "CAH Sandbox0",
                        "project_key": "g-p-examplelane00",
                        "project_root_url": "https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project",
                        "enabled": True,
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-examplelane01",
                        "project_root_url": "https://chatgpt.com/g/g-p-examplelane01-cah-sandbox1/project",
                        "enabled": True,
                    },
                ],
            })
            self.assertTrue(result["ok"])
            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            request = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:state/topology_request.json"], text=True
            ))
            state = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:state/chatgpt.json"], text=True
            ))
            self.assertEqual(request["registered_count"], 2)
            self.assertEqual(request["resume"]["next_action"], "resume-me")
            self.assertEqual(state["control_request"]["request_id"], "topo-12345678")
            self.assertEqual(state["phase"], "NEED_AGENT")
            wake_path = base / "spool" / "wake" / "inbox" / (result["wake_id"] + ".json")
            emitted = json.loads(wake_path.read_text(encoding="utf-8"))
            self.assertEqual(emitted["lane_id"], "lane-00")
            self.assertEqual(emitted["kind"], "topology_reconcile")
            status = topology_status(store, {
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
            })
            self.assertEqual(status["control_request"]["request_id"], "topo-12345678")
            self.assertEqual(status["control_request"]["status"], "PENDING")
            self.assertFalse(status["finalize"]["finalized"])

            # Simulate the semantic Worker reconcile: it writes the desired canonical
            # topology but intentionally does not close state.control_request.
            subprocess.check_call(["git", "-C", str(work), "checkout", "-B", "main", "FETCH_HEAD"], stdout=subprocess.DEVNULL)
            applied = {
                "v": 1,
                "topology_version": 2,
                "updated_at": "2026-09-18T00:10:00Z",
                "source_request_id": "topo-12345678",
                "registered_count": 2,
                "enabled_count": 2,
                "lanes": [
                    {
                        "lane_id": "lane-00",
                        "display_name": "CAH Sandbox0",
                        "project_key": "g-p-examplelane00",
                        "project_root_url": "https://chatgpt.com/g/g-p-examplelane00-cah-sandbox0/project",
                        "enabled": True,
                        "status": "IDLE",
                        "last_pool_takeover_id": "pool-12345678-abcd",
                        "worker_rollover_request": None,
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-examplelane01",
                        "project_root_url": "https://chatgpt.com/g/g-p-examplelane01-cah-sandbox1/project",
                        "enabled": True,
                        "status": "IDLE",
                        "last_pool_takeover_id": None,
                        "worker_rollover_request": None,
                    },
                ],
            }
            (work / "state" / "lanes.json").write_text(json.dumps(applied), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "state/lanes.json"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "worker reconcile"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            finalized = topology_status(store, {
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
            })
            self.assertTrue(finalized["finalize"]["finalized"])
            self.assertEqual(finalized["control_request"]["status"], "DONE")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            final_state = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:state/chatgpt.json"], text=True
            ))
            self.assertEqual(final_state["control_request"]["status"], "DONE")
            self.assertEqual(final_state["phase"], "DONE")
            self.assertEqual(final_state["next_action"], "resume-me")
            self.assertEqual(final_state["next_reads"], [])

    def test_extension_runtime_reports_sanitized_lane_summary(self):
        with tempfile.TemporaryDirectory() as td:
            store = WakeStore(Path(td))
            hello = store.extension_runtime_hello({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
                "version": "0.7.0",
                "extension_id": "extension-test-id",
                "desired_version": 3,
                "desired_lane_count": 2,
                "desired_enabled_count": 2,
                "bootstrap_parallel_status": "WORKERS",
                "lane_runtime": [
                    {
                        "lane_id": "lane-00",
                        "project_key": "g-p-examplelane00",
                        "enabled": True,
                        "current_verified": True,
                        "managed_count": 1,
                        "handoff_status": "verified",
                    },
                    {
                        "lane_id": "lane-01",
                        "project_key": "g-p-examplelane01",
                        "enabled": True,
                        "current_verified": False,
                        "managed_count": 1,
                        "handoff_status": "awaiting_git_takeover",
                    },
                ],
            })
            self.assertTrue(hello["ok"])
            status = store.extension_runtime_status({
                "client_id": "client-test-001",
                "project_id": "git-agent-harness",
            })
            self.assertEqual(status["status"]["desired_lane_count"], 2)
            self.assertEqual(status["status"]["bootstrap_parallel_status"], "WORKERS")
            self.assertEqual([x["lane_id"] for x in status["status"]["lane_runtime"]], ["lane-00", "lane-01"])
            self.assertNotIn("conversation_id", json.dumps(status["status"]))

    def test_extension_manifest_uses_lane_runtime(self):
        root = Path(__file__).resolve().parents[1]
        chromium = json.loads((root / "extension" / "manifest.chromium.json").read_text(encoding="utf-8"))
        firefox = json.loads((root / "extension" / "manifest.firefox.json").read_text(encoding="utf-8"))
        self.assertEqual(chromium["version"], "1.0.4")
        self.assertEqual(chromium["name"], "CAH Wake Bridge")
        self.assertEqual(firefox["name"], "CAH Wake Bridge")
        self.assertIn("lane_registry.js", firefox["background"]["scripts"])
        self.assertIn("task_cell_registry.js", firefox["background"]["scripts"])
        self.assertIn("lane_worker_runtime.js", firefox["background"]["scripts"])
        self.assertIn("lane_clear_runtime.js", firefox["background"]["scripts"])
        self.assertIn("ui_recovery_runtime.js", firefox["background"]["scripts"])
        self.assertNotIn("worker_runtime_v2.js", firefox["background"]["scripts"])
        self.assertNotIn("foreground_monitor.js", firefox["background"]["scripts"])
        bundle = (root / "extension" / "background_bundle.js").read_text(encoding="utf-8")
        self.assertIn("task_cell_registry.js", bundle)
        self.assertIn("ui_recovery_runtime.js", bundle)
        self.assertIn("lane_worker_runtime.js", bundle)
        self.assertIn("lane_clear_runtime.js", bundle)
        self.assertNotIn("foreground_monitor.js", bundle)
        lane_clear = (root / "extension" / "lane_clear_runtime.js").read_text(encoding="utf-8")
        self.assertIn("async function waitForProjectRoot", lane_clear)
        self.assertIn("project_conversation_list", lane_clear)
        self.assertIn("task_cell_prompt", lane_clear)
        self.assertIn("async function processTaskCellPrompt", lane_clear)
        self.assertIn("task_cell_prompt_complete", lane_clear)
        self.assertIn("async function processLanePoolReset", lane_clear)
        self.assertIn("async function processTaskCellClear", lane_clear)
        self.assertIn("lane_pool_reset_complete", lane_clear)
        self.assertIn("task_cell_clear_complete", lane_clear)
        self.assertIn("const target = discovered[0]", lane_clear)
        self.assertNotIn("[...discovered, ...poolTargets(lane)]", lane_clear)
        background = (root / "extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("wake.deferred_for_worker_handoff", background)
        self.assertIn("async function pollEnabledLanes()", background)
        self.assertIn("lane_id: laneId", background)
        self.assertIn("worker_takeover_status", background)
        self.assertIn("Expected exactly one visible enabled send button, found 0", background)
        lane_runtime = (root / "extension" / "lane_worker_runtime.js").read_text(encoding="utf-8")
        ui_recovery = (root / "extension" / "ui_recovery_runtime.js").read_text(encoding="utf-8")
        self.assertIn("globalThis.CAHUiRecovery", ui_recovery)
        self.assertIn("background_managed_tab_maintenance", ui_recovery)
        self.assertIn("semantic_stall", lane_runtime)
        worker_content = (root / "extension" / "worker_content.js").read_text(encoding="utf-8")
        self.assertIn("GAH_TAKEOVER mode=bootstrap-only", worker_content)
        self.assertIn("do not author task artifacts", worker_content)
        registry_source = (root / "extension" / "lane_registry.js").read_text(encoding="utf-8")
        self.assertIn("const RUNTIME_EPOCH = 3", registry_source)
        self.assertIn("CAH Sandbox1", registry_source)
        self.assertIn("g-p-examplelane01", registry_source)
        self.assertIn("project_root_url", registry_source)
        self.assertIn("bootstrap_parallel", registry_source)
        self.assertNotIn("g-p-exampletaskcell", registry_source)
        task_cell_source = (root / "extension" / "task_cell_registry.js").read_text(encoding="utf-8")
        self.assertIn("g-p-exampletaskcell", task_cell_source)
        self.assertIn("CAH Task Cell", task_cell_source)
        self.assertIn("parsed.project_key === DEFAULT_TASK_CELL.project_key", task_cell_source)
        self.assertIn("project_root_url", task_cell_source)
        self.assertIn("foreground_result_delivered", task_cell_source)
        self.assertIn("worker_pool: false", task_cell_source)
        self.assertIn("Task Cell Project must not also be an execution lane", task_cell_source)
        background = (root / "extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("maintainParallelBootstrap", background)
        self.assertIn("runtimeTopologySummary", background)
        self.assertIn("gah-task-cell-status", background)
        self.assertIn("gah-task-cell-open", background)
        host_update = (root / "host" / "host_update.ps1").read_text(encoding="utf-8")
        self.assertIn("ExpectedExtensionVersion = '1.0.4'", host_update)
        installer = (root / "tools" / "configure_install.py").read_text(encoding="utf-8")
        self.assertIn("--runner-root", installer)
        self.assertIn("--task-cell", installer)
        self.assertIn("state/lanes.json", installer)
        popup = (root / "extension" / "popup.html").read_text(encoding="utf-8")
        self.assertIn("Task Cell", popup)
        self.assertIn("task_cell_registry.js", popup)
        self.assertNotIn("conversationUrl", popup)
        self.assertNotIn("Bind current foreground", popup)


if __name__ == "__main__":
    unittest.main()
