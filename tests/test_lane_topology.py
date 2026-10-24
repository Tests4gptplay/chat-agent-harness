import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.wake import make_wake, validate_wake
from local_bridge.server import WakeStore
from local_bridge.topology import stage_topology_request, topology_status


from harness.worker_lineage_compat import (
    LAST_WORKER_REF_KEY,
    WORKER_LINEAGES_KEY,
)


class LaneTopologyTests(unittest.TestCase):
    def test_lane_addressed_wake(self):
        wake = make_wake(
            "git-agent-harness",
            repo="__CAH_REPOSITORY__",
            result_ref="state/topology_request.json",
            lane_id="lane-00",
            worker_project_key="g-p-CAHLANE00",
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
                    "project_key": "g-p-CAHLANE00",
                    "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00-cah-sandbox0/project",
                    "enabled": True,
                    "status": "IDLE",
                    WORKER_LINEAGES_KEY: {},
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
                "worker_project_key": "g-p-CAHLANE00",
                "lanes": [
                    {
                        "lane_id": "lane-00",
                        "display_name": "CAH Sandbox0",
                        "project_key": "g-p-CAHLANE00",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00-cah-sandbox0/project",
                        "enabled": True,
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-CAHLANE01",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE01-cah-sandbox1/project",
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
                        "project_key": "g-p-CAHLANE00",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00-cah-sandbox0/project",
                        "enabled": True,
                        "status": "IDLE",
                        WORKER_LINEAGES_KEY: {
                            "topology-task::1": {
                                "owner_task_id": "topology-task",
                                "owner_control_epoch": 1,
                                LAST_WORKER_REF_KEY: "pool-12345678-abcd",
                                "worker_rollover_request": None,
                            },
                        },
                    },
                    {
                        "lane_id": "lane-01",
                        "display_name": "CAH Sandbox1",
                        "project_key": "g-p-CAHLANE01",
                        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE01-cah-sandbox1/project",
                        "enabled": True,
                        "status": "IDLE",
                        WORKER_LINEAGES_KEY: {},
                    },
                ],
            }
            (work / "state" / "lanes.json").write_text(json.dumps(applied), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "state/lanes.json"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "worker reconcile"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            reconciled_sha = subprocess.check_output(
                ["git", "-C", str(work), "rev-parse", "HEAD"], text=True
            ).strip()
            store.record_git_commit(reconciled_sha)
            with patch("local_bridge.topology.finalize_topology_request") as finalize:
                observed = topology_status(store, {
                    "client_id": "client-test-001",
                    "project_id": "git-agent-harness",
                    "observation": True,
                })
            finalize.assert_not_called()
            self.assertEqual(observed["control_request"]["status"], "PENDING")
            self.assertEqual(observed["lanes"]["source_request_id"], "topo-12345678")
            self.assertIsNone(observed["finalize"])

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


class TopologyObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "work"
        subprocess.check_call(["git", "init", str(self.work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(self.work), "config", "user.name", "gah-test"])
        subprocess.check_call(["git", "-C", str(self.work), "config", "user.email", "gah-test@example.invalid"])
        (self.work / "state").mkdir()
        self._write_state(1, "PENDING")
        self.sha = subprocess.check_output(
            ["git", "-C", str(self.work), "rev-parse", "HEAD"], text=True
        ).strip()
        self.clock = [0.0]
        self.store = WakeStore(
            self.root / "spool", repo_root=self.work,
            git_snapshot_clock=lambda: self.clock[0],
        )
        self.request = {
            "client_id": "client-observation",
            "project_id": "git-agent-harness",
            "observation": True,
        }

    def _write_state(self, version, status):
        for name, value in {
            "lanes": {"topology_version": version, "lanes": []},
            "chatgpt": {"control_request": {"request_id": "topo-observation", "status": status}},
        }.items():
            (self.work / "state" / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        subprocess.check_call(["git", "-C", str(self.work), "add", "state"])
        subprocess.check_call(
            ["git", "-C", str(self.work), "commit", "-m", f"topology {version}"],
            stdout=subprocess.DEVNULL,
        )

    def test_missing_or_expired_snapshot_defers_without_git_or_finalization(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                if expired:
                    self.store.record_git_commit(self.sha)
                    self.clock[0] = 1.0
                with patch.object(self.store, "_git", side_effect=AssertionError("observation performed Git IO")) as git:
                    with patch("local_bridge.topology.finalize_topology_request") as finalize:
                        result = topology_status(self.store, self.request)
                git.assert_not_called()
                finalize.assert_not_called()
                self.assertTrue(result["ok"])
                self.assertEqual(result["idle"], "git_snapshot_unavailable")
                for field in ("lanes", "control_request", "finalize"):
                    self.assertIsNone(result[field])

    def test_observation_reads_one_immutable_commit_while_git_transaction_lock_is_held(self):
        self.store.record_git_commit(self.sha)
        # A later checkout commit must not replace the confirmed observation SHA.
        self._write_state(2, "DONE")
        original_git = self.store._git
        calls = []

        def immutable_read(*args):
            self.assertEqual(args[0], "show")
            self.assertTrue(args[1].startswith(self.sha + ":"))
            calls.append(args)
            return original_git(*args)

        done = threading.Event()
        results = []
        errors = []

        def observe():
            try:
                results.append(topology_status(self.store, self.request))
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        with patch.object(self.store, "_git", side_effect=immutable_read):
            with patch("local_bridge.topology.finalize_topology_request") as finalize:
                with self.store.git_lock:
                    thread = threading.Thread(target=observe, daemon=True)
                    thread.start()
                    completed_without_lock = done.wait(2.0)
                thread.join(timeout=2.0)
        self.assertTrue(completed_without_lock, "routine topology observation waited for the transaction lock")
        self.assertEqual(errors, [])
        finalize.assert_not_called()
        self.assertEqual(len(calls), 2)
        self.assertEqual(results[0]["source_commit"], self.sha)
        self.assertEqual(results[0]["lanes"]["topology_version"], 1)
        self.assertEqual(results[0]["control_request"]["status"], "PENDING")
        self.assertIsNone(results[0]["finalize"])

    def test_failed_snapshot_read_is_not_an_empty_successful_topology(self):
        self.store.record_git_commit(self.sha)
        with patch.object(self.store, "_git", side_effect=RuntimeError("unreadable object")):
            with patch("local_bridge.topology.finalize_topology_request") as finalize:
                result = topology_status(self.store, self.request)
        finalize.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "GIT_TOPOLOGY_OBSERVATION_READ_FAILED")
        self.assertIsNone(result["lanes"])
        self.assertIsNone(result["finalize"])


if __name__ == "__main__":
    unittest.main()
