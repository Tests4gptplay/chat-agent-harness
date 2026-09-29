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
            repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
            result_ref="state/topology_request.json",
            lane_id="lane-00",
            worker_project_key="g-p-CAHLANE00PLACEHOLDER",
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
                    "project_key": "g-p-CAHLANE00PLACEHOLDER",
                    "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00PLACEHOLDER-cah-sandbox0/project",
                    "enabled": True,
                    "status": "IDLE",
                    "task_pools": {},
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
                "worker_project_key": "g-p-CAHLANE00PLACEHOLDER",
                "lanes": [
                    {
                        "lane_id": "lane-00",
                        "display_name": "CAH Sandbox0",
                        "project_key": "g-p-CAHLANE00PLACEHOLDER",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00PLACEHOLDER-cah-sandbox0/project",
                        "enabled": True,
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-CAHLANE01PLACEHOLDER",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE01PLACEHOLDER-cah-sandbox1/project",
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
                        "project_key": "g-p-CAHLANE00PLACEHOLDER",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00PLACEHOLDER-cah-sandbox0/project",
                        "enabled": True,
                        "status": "IDLE",
                        "task_pools": {
                            "topology-task::1": {
                                "owner_task_id": "topology-task",
                                "owner_control_epoch": 1,
                                "last_pool_takeover_id": "pool-12345678-abcd",
                                "worker_rollover_request": None,
                            },
                        },
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-CAHLANE01PLACEHOLDER",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE01PLACEHOLDER-cah-sandbox1/project",
                        "enabled": True,
                        "status": "IDLE",
                        "task_pools": {},
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


if __name__ == "__main__":
    unittest.main()
