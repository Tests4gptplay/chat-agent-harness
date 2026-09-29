import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_bridge.scheduler import _try_finalize_planner_worker_child, reconcile_dispatch_liveness
from local_bridge.planner_runtime import stage_worker_watchdog_helper, sync_semantic_turn
from local_bridge.server import WakeStore


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8").strip()


class PlannerChildTerminalLivenessTests(unittest.TestCase):
    def _seed(
        self,
        base: Path,
        *,
        result_generation: int | None = 2,
        lease_expires_at: str = "2099-01-01T00:00:00Z",
    ):
        origin = base / "origin.git"
        work = base / "work"
        subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
        subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])
        for rel in (
            "tasks",
            "cl",
            "evidence/parent/roles/worker",
            "memory/worker/planner-child-001",
            "state/worker_turns/planner-child-001/wake-g2",
            "state",
            "state/task_cells",
        ):
            (work / rel).mkdir(parents=True, exist_ok=True)

        task = {
            "v": 1,
            "task_id": "planner-child-001",
            "kind": "planner_worker_child",
            "parent_task_id": "parent",
            "backend_cl": "cl/planner-child-001.backend.json",
            "lane_id": "lane-00",
            "worker_project_key": "g-p-test00",
            "expected_result_ref": "evidence/parent/roles/worker/planner-child-001/wake-g2.json",
            "result_contract": {
                "v": 1,
                "task_id": "planner-child-001",
            },
            "child_reply_ref": "memory/worker/planner-child-001/reply.md",
            "worker_reply_entry_ref": "state/worker_turns/planner-child-001/wake-g2/reply-entry.md",
        }
        bg = {
            "v": 1,
            "cl_id": "bg-planner-child-001",
            "task_id": "planner-child-001",
            "scope": "backend_execution",
            "overall": "RUNNING",
            "created_at": "x",
            "updated_at": "x",
            "result_ref": None,
            "error": None,
            "wait_ref": None,
            "dispatch": {
                "dispatch_id": "dispatch-g2",
                "wake_id": "wake-g2",
                "generation": 2,
                "fence_token": "fence-g2",
                "state": "RUNNING",
                "requested_at": "2026-09-21T00:00:00Z",
                "delivered_at": "2026-09-21T00:00:01Z",
                "acked_at": "2026-09-21T00:00:01Z",
                "acked_by_worker_ref": "pool-worker",
                "lease_expires_at": lease_expires_at,
                "continuation_ref": "tasks/planner-child-001.json",
                "wait_ref": None,
                "ack_source": "extension_response_start",
            },
            "scheduling": {
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "owner_task_id": "parent",
                "owner_control_epoch": 1,
            },
            "conditions": [
                {"id": "claimed", "state": "GREEN", "detail": "claimed", "evidence_ref": None},
                {"id": "semantic_work", "state": "WAIT", "detail": None, "evidence_ref": None},
                {"id": "branch_output", "state": "WAIT", "detail": None, "evidence_ref": None},
            ],
        }
        result = {
            "v": 1,
            "task_id": "planner-child-001",
            "status": "PASS",
            "summary": "synthetic child complete",
        }
        state = {"v": 1, "phase": "IDLE"}
        parent_cell = {
            "v": 1,
            "task_cell_id": "parent",
            "task_id": "parent",
            "task_cell_project_key": "g-p-task-cell",
            "control_epoch": 1,
            "status": "PARKED_WAIT_EVENT",
            "roles": {},
            "planner_control": {
                "v": 1,
                "feature": "HYBRID_ACTIVE",
                "enabled": True,
                "task_id": "parent",
                "control_epoch": 1,
                "activity": "PARKED_WAIT_EVENT",
                "wait": {
                    "kind": "WAIT_RESULT",
                    "selector": {"task_id": "parent"},
                    "refs": [],
                },
                "runtime": {
                    "owned_children": [{
                        "child_task_id": "planner-child-001",
                        "task_ref": "tasks/planner-child-001.json",
                        "backend_cl": "cl/planner-child-001.backend.json",
                        "lane_id": "lane-00",
                        "worker_project_key": "g-p-test00",
                        "owner_task_id": "parent",
                        "owner_control_epoch": 1,
                        "dispatch_id": "dispatch-g2",
                        "dispatch_generation": 2,
                        "fence_token": "fence-g2",
                        "child_reply_ref": "memory/worker/planner-child-001/reply.md",
                        "worker_reply_entry_ref": "state/worker_turns/planner-child-001/wake-g2/reply-entry.md",
                        "slot_index": 1,
                        "round_doorbell_id": "doorbell-test",
                        "event_state": "WAITING",
                    }],
                    "pending_role_requests": [],
                    "pending_role_outputs": [],
                },
            },
        }
        lanes = {
            "v": 1,
            "lanes": [{
                "lane_id": "lane-00",
                "project_key": "g-p-test00",
                "enabled": True,
                "task_pools": {
                    "parent::1": {
                        "owner_task_id": "parent",
                        "owner_control_epoch": 1,
                        "last_pool_takeover_id": "pool-worker",
                        "worker_rollover_request": None,
                    },
                },
            }],
        }

        (work / "tasks/planner-child-001.json").write_text(json.dumps(task), encoding="utf-8")
        (work / "cl/planner-child-001.backend.json").write_text(json.dumps(bg), encoding="utf-8")
        if result_generation is not None:
            result_path = (
                work / "evidence/parent/roles/worker/planner-child-001"
                / ("wake-g2.json" if result_generation == 2 else "wake-g1.json")
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(result), encoding="utf-8")
        (work / "memory/worker/planner-child-001/reply.md").write_text(
            "# Worker Child Reply\n\n## planner-direction · PLANNER · DIRECTION\n\nDo the child work.\n",
            encoding="utf-8",
        )
        (work / "state/worker_turns/planner-child-001/wake-g2/reply-entry.md").write_text(
            "## wake-g2 · WORKER · WORK_RESULT\n\nProduced the requested child artifact.\n",
            encoding="utf-8",
        )
        (work / "state/chatgpt.json").write_text(json.dumps(state), encoding="utf-8")
        (work / "state/task_cells/parent.json").write_text(json.dumps(parent_cell), encoding="utf-8")
        (work / "state/lanes.json").write_text(json.dumps(lanes), encoding="utf-8")
        subprocess.check_call(["git", "-C", str(work), "add", "."])
        subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed planner child"], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
        subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"])
        return work

    def test_exact_planner_child_result_finalizes_before_redrive(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=2)
            store = WakeStore(Path(td) / "spool", repo_root=work)
            out = reconcile_dispatch_liveness(store, {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "response_running": False,
                "response_ended": True,
            })
            self.assertTrue(out["ok"])
            self.assertFalse(out["recovered"])
            self.assertEqual(out["reason"], "planner_worker_child_terminal_finalized")
            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            bg = json.loads(git(work, "show", "FETCH_HEAD:cl/planner-child-001.backend.json"))
            self.assertEqual(bg["overall"], "DONE")
            self.assertEqual(bg["dispatch"]["state"], "DONE")
            self.assertEqual(bg["dispatch"]["generation"], 2)
            self.assertEqual(bg["result_ref"], "evidence/parent/roles/worker/planner-child-001/wake-g2.json")
            self.assertEqual(next(x for x in bg["conditions"] if x["id"] == "semantic_work")["state"], "GREEN")
            self.assertEqual(next(x for x in bg["conditions"] if x["id"] == "branch_output")["state"], "GREEN")
            child_reply = git(work, "show", "FETCH_HEAD:memory/worker/planner-child-001/reply.md")
            self.assertIn("planner-direction", child_reply)
            self.assertIn("wake-g2", child_reply)
            self.assertIn("Produced the requested child artifact.", child_reply)

    def test_missing_result_waits_for_worker_watchdog_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=None)
            store = WakeStore(Path(td) / "spool", repo_root=work)
            before = git(work, "rev-parse", "HEAD")
            out = reconcile_dispatch_liveness(store, {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "response_running": False,
                "response_ended": True,
            })
            self.assertTrue(out["ok"], out)
            self.assertFalse(out["recovered"])
            self.assertFalse(out["escalated"])
            self.assertEqual(out["reason"], "managed_worker_liveness_wait_worker_watchdog")
            self.assertEqual(git(work, "rev-parse", "HEAD"), before)

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            bg = json.loads(git(work, "show", "FETCH_HEAD:cl/planner-child-001.backend.json"))
            self.assertEqual(bg["overall"], "RUNNING")
            self.assertEqual(bg["dispatch"]["state"], "RUNNING")
            self.assertEqual(bg["dispatch"]["generation"], 2)
            cell = json.loads(git(work, "show", "FETCH_HEAD:state/task_cells/parent.json"))
            self.assertEqual(cell["planner_control"]["wait"]["kind"], "WAIT_RESULT")
            self.assertEqual(cell["planner_control"]["runtime"]["pending_role_requests"], [])

    def test_running_response_with_healthy_lease_does_not_write_renewal(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=None)
            store = WakeStore(Path(td) / "spool", repo_root=work)
            before = git(work, "rev-parse", "HEAD")
            out = reconcile_dispatch_liveness(store, {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "response_running": True,
                "response_ended": False,
            })
            self.assertTrue(out["ok"])
            self.assertFalse(out["recovered"])
            self.assertEqual(out["reason"], "response_still_running")
            self.assertEqual(git(work, "rev-parse", "HEAD"), before)
            bg = json.loads((work / "cl/planner-child-001.backend.json").read_text(encoding="utf-8"))
            self.assertEqual(
                bg["dispatch"]["lease_expires_at"],
                "2099-01-01T00:00:00Z",
            )

    def test_running_response_renews_expired_lease_without_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(
                Path(td),
                result_generation=None,
                lease_expires_at="2000-01-01T00:00:00Z",
            )
            store = WakeStore(Path(td) / "spool", repo_root=work)
            out = reconcile_dispatch_liveness(store, {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "response_running": True,
                "response_ended": False,
            })
            self.assertTrue(out["ok"])
            self.assertFalse(out["recovered"])
            self.assertEqual(out["reason"], "response_still_running_lease_renewed")

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            bg = json.loads(git(work, "show", "FETCH_HEAD:cl/planner-child-001.backend.json"))
            self.assertEqual(bg["overall"], "RUNNING")
            self.assertEqual(bg["dispatch"]["state"], "RUNNING")
            self.assertEqual(bg["dispatch"]["generation"], 2)
            self.assertEqual(bg["dispatch"]["acked_by_worker_ref"], "pool-worker")
            self.assertNotEqual(
                bg["dispatch"]["lease_expires_at"],
                "2000-01-01T00:00:00Z",
            )
            self.assertTrue(bg["updated_at"])

    def test_worker_watchdog_stages_distinct_worker_helper_once(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=None)
            store = WakeStore(Path(td) / "spool", repo_root=work)
            req = {
                "parent_task_id": "parent",
                "parent_control_epoch": 1,
                "child_task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "reason": "worker_watchdog_30m",
                "observed_at": "2026-09-29T00:30:00+00:00",
            }
            first = stage_worker_watchdog_helper(store, req)
            self.assertTrue(first["ok"], first)
            self.assertTrue(first["staged"])
            self.assertTrue(first["helper_request_id"].startswith("worker-helper-"))

            second = stage_worker_watchdog_helper(store, req)
            self.assertTrue(second["ok"], second)
            self.assertTrue(second["duplicate"])
            self.assertEqual(second["helper_request_id"], first["helper_request_id"])

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cell = json.loads(git(work, "show", "FETCH_HEAD:state/task_cells/parent.json"))
            self.assertEqual(cell["planner_control"]["wait"]["kind"], "WAIT_WORKER_HELPER")
            matching = [
                x for x in cell["planner_control"]["runtime"]["pending_role_requests"]
                if x["request_id"] == first["helper_request_id"]
            ]
            self.assertEqual(len(matching), 1)
            helper_req = matching[0]
            self.assertEqual(helper_req["kind"], "worker_helper_result")
            self.assertEqual(helper_req["recovery"]["kind"], "WORKER_WATCHDOG")
            self.assertEqual(helper_req["recovery"]["source_role"], "worker_watchdog")
            self.assertEqual(helper_req["recovery"]["dispatch_generation"], 2)

            state = json.loads(git(work, "show", "FETCH_HEAD:state/chatgpt.json"))
            control = state["control_request"]
            self.assertEqual(control["kind"], "task_cell_worker_helper_prompt")
            self.assertEqual(control["role"], "helper")
            self.assertEqual(control["helper_source"], "worker_watchdog")
            self.assertEqual(control["semantic_output_kind"], "WORKER_HELPER_RESULT")
            self.assertEqual(control["request_id"], first["helper_request_id"])

    def test_old_generation_result_path_cannot_satisfy_current_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=1)
            store = WakeStore(Path(td) / "spool", repo_root=work)
            head = git(work, "rev-parse", "HEAD")
            out = _try_finalize_planner_worker_child(
                store,
                task_id="planner-child-001",
                backend_cl_rel="cl/planner-child-001.backend.json",
                canonical_sha=head,
                attempt=0,
            )
            self.assertIsNone(out)
            bg = json.loads((work / "cl/planner-child-001.backend.json").read_text(encoding="utf-8"))
            self.assertEqual(bg["overall"], "RUNNING")
            self.assertEqual(bg["dispatch"]["generation"], 2)


    def test_active_worker_sync_reads_durable_turn_signal_and_finalizes(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=2)
            result_path = work / "evidence/parent/roles/worker/planner-child-001/wake-g2.json"
            result_path.write_text(json.dumps({
                "v": 1,
                "task_id": "planner-child-001",
                "summary": "synthetic child complete",
                "turn_signal": "complete",
            }), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", str(result_path)])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "write durable worker turn signal"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(Path(td) / "spool", repo_root=work)
            req = {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "role": "worker",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
            }
            out = sync_semantic_turn(store, req)
            self.assertTrue(out["ok"])
            self.assertTrue(out["accepted"])

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = json.loads(git(work, "show", "FETCH_HEAD:evidence/parent/roles/worker/planner-child-001/wake-g2.json"))
            bg = json.loads(git(work, "show", "FETCH_HEAD:cl/planner-child-001.backend.json"))
            self.assertEqual(result["turn_signal"], "complete")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(bg["overall"], "DONE")
            self.assertEqual(bg["dispatch"]["state"], "DONE")

            duplicate = sync_semantic_turn(store, req)
            self.assertTrue(duplicate["accepted"])
            self.assertTrue(duplicate["duplicate"])

    def test_active_worker_sync_rejects_stale_binding(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=2)
            result_path = work / "evidence/parent/roles/worker/planner-child-001/wake-g2.json"
            result_path.write_text(json.dumps({
                "v": 1,
                "task_id": "planner-child-001",
                "summary": "synthetic child complete",
                "turn_signal": "complete",
            }), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", str(result_path)])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "write stale worker turn signal"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(Path(td) / "spool", repo_root=work)
            out = sync_semantic_turn(store, {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "role": "worker",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-stale",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
            })
            self.assertFalse(out["accepted"])
            self.assertEqual(out["reason"], "stale_dispatch")

    def test_active_worker_sync_requires_durable_turn_signal(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._seed(Path(td), result_generation=2)
            store = WakeStore(Path(td) / "spool", repo_root=work)
            out = sync_semantic_turn(store, {
                "client_id": "client-planner-child",
                "project_id": "git-agent-harness",
                "role": "worker",
                "task_id": "planner-child-001",
                "backend_cl": "cl/planner-child-001.backend.json",
                "dispatch_id": "dispatch-g2",
                "dispatch_generation": 2,
                "fence_token": "fence-g2",
                "worker_ref": "pool-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
            })
            self.assertFalse(out["accepted"])
            self.assertEqual(out["reason"], "worker_turn_signal_missing")




if __name__ == "__main__":
    unittest.main()
