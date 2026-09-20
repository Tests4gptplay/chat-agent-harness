import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from local_bridge.scheduler import complete_worker_handoff, reconcile_dispatch_liveness, stage_action_submit  # noqa: E402
from local_bridge.server import WakeStore  # noqa: E402


class ActionSubmitSyscallTests(unittest.TestCase):
    def test_atomic_action_and_wait_result(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])

            (work / "cl").mkdir()
            fg = {
                "v": 1,
                "cl_id": "fg-syscall",
                "task_id": "task-syscall-001",
                "scope": "foreground_supervision",
                "overall": "RUNNING",
                "created_at": "x",
                "updated_at": "x",
                "conditions": [],
                "supervisor_guard": {"state": "HELD", "detail": None},
            }
            bg = {
                "v": 1,
                "cl_id": "bg-syscall",
                "task_id": "task-syscall-001",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "x",
                "updated_at": "x",
                "dispatch": {
                    "dispatch_id": "dispatch-syscall-0001",
                    "wake_id": "wake-syscall-0001",
                    "generation": 1,
                    "fence_token": "fence-syscall-0001",
                    "state": "ACKED",
                    "requested_at": "x",
                    "acked_at": "x",
                    "acked_by_worker_ref": "pool-worker-syscall-0001",
                    "wait_ref": None,
                },
                "conditions": [
                    {"id": "executor", "label": "Executor", "state": "WAIT", "detail": None, "evidence_ref": None},
                ],
            }
            (work / "cl" / "fg.json").write_text(json.dumps(fg), encoding="utf-8")
            (work / "cl" / "bg.json").write_text(json.dumps(bg), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "cl"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            action = {
                "v": 1,
                "action_id": "task-syscall-001-a001",
                "task_id": "task-syscall-001",
                "round": 1,
                "executor": "echo",
                "operation": "echo",
                "payload": {"message": "hello"},
                "expected_evidence": ["echo"],
                "foreground_cl": "cl/fg.json",
                "backend_cl": "cl/bg.json",
                "timeout_seconds": 30,
                "lane_id": "lane-00",
                "worker_project_key": "g-p-examplelane00",
                "worker_continuation": True,
            }
            req = {
                "client_id": "client-syscall-001",
                "project_id": "git-agent-harness",
                "task_id": "task-syscall-001",
                "backend_cl": "cl/bg.json",
                "foreground_cl": "cl/fg.json",
                "dispatch_id": "dispatch-syscall-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-syscall-0001",
                "worker_ref": "pool-worker-syscall-0001",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-examplelane00",
                "action": action,
            }

            first = stage_action_submit(store, req)
            self.assertTrue(first["ok"])
            self.assertFalse(first["duplicate"])
            self.assertEqual(first["dispatch_state"], "WAIT_RESULT")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            got_bg = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:cl/bg.json"],
                text=True, encoding="utf-8"
            ))
            got_action = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:actions/stage0/task-syscall-001.json"],
                text=True, encoding="utf-8"
            ))
            self.assertEqual(got_bg["dispatch"]["state"], "WAIT_RESULT")
            self.assertEqual(got_bg["dispatch"]["wait_ref"], "task-syscall-001-a001")
            self.assertEqual(got_action["action_id"], "task-syscall-001-a001")

            second = stage_action_submit(store, req)
            self.assertTrue(second["ok"])
            self.assertTrue(second["duplicate"])

            stale = stage_action_submit(store, {**req, "fence_token": "fence-stale-0001"})
            self.assertFalse(stale["ok"])
            self.assertEqual(stale["error"], "ACTION_SUBMIT_STALE_DISPATCH")


    def test_handoff_takeover_redispatches_fresh_generation(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])

            (work / "cl").mkdir()
            (work / "state" / "handoffs").mkdir(parents=True)
            outgoing = "pool-outgoing-worker-0001"
            successor = "pool-successor-worker-0002"
            packet_ref = "state/handoffs/task-handoff-001-packet.json"
            backend_ref = "cl/task-handoff-001.backend.json"
            project_key = "g-p-examplelane00"
            old_dispatch = {
                "dispatch_id": "dispatch-handoff-old-0002",
                "wake_id": "wake-handoff-old-0002",
                "generation": 2,
                "fence_token": "fence-handoff-old-0002",
                "state": "RUNNING",
                "requested_at": "2026-09-19T07:00:00Z",
                "delivered_at": "2026-09-19T07:00:01Z",
                "acked_at": "2026-09-19T07:00:01Z",
                "acked_by_worker_ref": outgoing,
                "lease_expires_at": "2099-01-01T00:00:00+00:00",
                "continuation_ref": "tasks/task-handoff-001.json",
                "wait_ref": None,
                "ack_source": "extension_response_start",
            }
            backend = {
                "v": 1,
                "cl_id": "bg-task-handoff-001",
                "task_id": "task-handoff-001",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "2026-09-19T07:00:00Z",
                "updated_at": "2026-09-19T07:00:01Z",
                "result_ref": None,
                "checkpoint_ref": None,
                "memory_ref": None,
                "handoff_packet_ref": packet_ref,
                "wait_ref": None,
                "dispatch": old_dispatch,
                "error": None,
                "conditions": [],
            }
            packet = {
                "v": 1,
                "handoff_id": outgoing,
                "task_id": "task-handoff-001",
                "generation": 2,
                "fence_token": "fence-handoff-old-0002",
                "reason": "context_compacted",
                "work_checkpoint": {
                    "backend_cl_ref": backend_ref,
                    "task_ref": "tasks/task-handoff-001.json",
                    "work_branch": "showcase/test",
                    "dispatch_id": "dispatch-handoff-old-0002",
                    "frontier": "resume exactly here",
                },
                "memory_capsule": {"objective": "resume"},
                "artifact_refs": [],
                "evidence_refs": [],
                "next_action": "continue from durable checkpoint",
            }
            rollover = {
                "v": 1,
                "reason": "context_compacted",
                "status": "PENDING",
                "lane_id": "lane-00",
                "worker_project_key": project_key,
                "outgoing_pool_id": outgoing,
                "task_id": "task-handoff-001",
                "backend_cl_ref": backend_ref,
                "dispatch_id": "dispatch-handoff-old-0002",
                "dispatch_generation": 2,
                "fence_token": "fence-handoff-old-0002",
                "handoff_packet_ref": packet_ref,
            }
            state = {
                "v": 1,
                "agent": "chatgpt",
                "updated": "2026-09-19",
                "phase": "HANDOFF",
                "active_task": "task-handoff-001",
                "next_reads": [packet_ref, backend_ref],
                "next_action": "successor takeover",
                "worker_rollover_request": rollover,
                "last_pool_takeover_id": successor,
                "handoff_packet_ref": packet_ref,
                "active_dispatch_ref": backend_ref,
            }
            lanes = {
                "v": 1,
                "topology_version": 1,
                "updated_at": "2026-09-19T07:01:00Z",
                "registered_count": 1,
                "enabled_count": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "display_name": "CAH Sandbox0",
                    "project_key": project_key,
                    "project_root_url": "https://chatgpt.com/g/test/project",
                    "enabled": True,
                    "status": "IDLE",
                    "last_pool_takeover_id": successor,
                    "worker_rollover_request": None,
                }],
            }

            (work / backend_ref).write_text(json.dumps(backend), encoding="utf-8")
            (work / packet_ref).write_text(json.dumps(packet), encoding="utf-8")
            (work / "state" / "chatgpt.json").write_text(json.dumps(state), encoding="utf-8")
            (work / "state" / "lanes.json").write_text(json.dumps(lanes), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed handoff stall"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            req = {
                "client_id": "client-handoff-001",
                "project_id": "git-agent-harness",
                "lane_id": "lane-00",
                "worker_project_key": project_key,
                "successor_worker_ref": successor,
                "handoff_packet_ref": packet_ref,
            }
            result = complete_worker_handoff(store, req)
            self.assertTrue(result["ok"])
            self.assertFalse(result["already_recovered"])
            self.assertEqual(result["dispatch"]["generation"], 3)
            self.assertEqual(result["dispatch"]["state"], "READY")
            self.assertEqual(result["recovered_from"]["dispatch_id"], old_dispatch["dispatch_id"])
            self.assertEqual(result["recovered_from"]["reason"], "worker_handoff")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            got_bg = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{backend_ref}"],
                text=True, encoding="utf-8"
            ))
            got_state = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:state/chatgpt.json"],
                text=True, encoding="utf-8"
            ))
            got_wake = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{result['wake_ref']}"],
                text=True, encoding="utf-8"
            ))
            self.assertEqual(got_bg["dispatch"]["generation"], 3)
            self.assertIsNone(got_bg["dispatch"]["acked_by_worker_ref"])
            self.assertEqual(got_bg["dispatch"]["recovered_from"]["generation"], 2)
            self.assertEqual(got_state["phase"], "RUNNING")
            self.assertIsNone(got_state["worker_rollover_request"])
            self.assertEqual(got_state["last_pool_takeover_id"], successor)
            self.assertEqual(got_wake["dispatch_generation"], 3)
            self.assertEqual(got_wake["dispatch_id"], got_bg["dispatch"]["dispatch_id"])
            subject = subprocess.check_output(
                ["git", "-C", str(work), "log", "-1", "--format=%s", "FETCH_HEAD"],
                text=True, encoding="utf-8"
            ).strip()
            self.assertEqual(subject, "Resume task-handoff-001 after Worker handoff")
            self.assertNotIn("[skip ci]", subject)

            old_status = store.dispatch_status({
                "client_id": "client-handoff-001",
                "project_id": "git-agent-harness",
                "task_id": "task-handoff-001",
                "backend_cl": backend_ref,
                "dispatch_id": old_dispatch["dispatch_id"],
                "dispatch_generation": 2,
                "fence_token": old_dispatch["fence_token"],
            })
            self.assertFalse(old_status["matched"])
            self.assertTrue(old_status["suppress"])
            self.assertEqual(old_status["reason"], "stale_dispatch")

            again = complete_worker_handoff(store, req)
            self.assertTrue(again["ok"])
            self.assertTrue(again["already_recovered"])

    def test_response_end_recovers_orphaned_running_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])
            (work / "cl").mkdir()
            backend_ref = "cl/task-liveness-001.backend.json"
            worker = "pool-liveness-worker-0001"
            project_key = "g-p-examplelane01"
            backend = {
                "v": 1,
                "cl_id": "bg-task-liveness-001",
                "task_id": "task-liveness-001",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "2026-09-19T07:00:00Z",
                "updated_at": "2026-09-19T07:00:01Z",
                "result_ref": None,
                "checkpoint_ref": None,
                "memory_ref": None,
                "handoff_packet_ref": None,
                "wait_ref": None,
                "dispatch": {
                    "dispatch_id": "dispatch-liveness-0003",
                    "wake_id": "wake-liveness-0003",
                    "generation": 3,
                    "fence_token": "fence-liveness-0003",
                    "state": "RUNNING",
                    "requested_at": "2026-09-19T07:00:00Z",
                    "delivered_at": "2026-09-19T07:00:01Z",
                    "acked_at": "2026-09-19T07:00:01Z",
                    "acked_by_worker_ref": worker,
                    "lease_expires_at": "2099-01-01T00:00:00+00:00",
                    "continuation_ref": "tasks/task-liveness-001.json",
                    "wait_ref": None,
                    "ack_source": "extension_response_start",
                },
                "error": None,
                "conditions": [],
            }
            (work / backend_ref).write_text(json.dumps(backend), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed liveness stall"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            req = {
                "client_id": "client-liveness-001",
                "project_id": "git-agent-harness",
                "task_id": "task-liveness-001",
                "backend_cl": backend_ref,
                "dispatch_id": "dispatch-liveness-0003",
                "dispatch_generation": 3,
                "fence_token": "fence-liveness-0003",
                "worker_ref": worker,
                "lane_id": "lane-01",
                "worker_project_key": project_key,
                "response_running": False,
                "response_ended": True,
            }
            result = reconcile_dispatch_liveness(store, req)
            self.assertTrue(result["ok"])
            self.assertTrue(result["recovered"])
            self.assertEqual(result["reason"], "semantic_response_ended")
            self.assertEqual(result["dispatch"]["generation"], 4)
            self.assertEqual(result["dispatch"]["state"], "READY")
            self.assertEqual(result["recovered_from"]["dispatch_id"], "dispatch-liveness-0003")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            got_bg = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{backend_ref}"],
                text=True, encoding="utf-8"
            ))
            got_wake = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{result['wake_ref']}"],
                text=True, encoding="utf-8"
            ))
            self.assertEqual(got_bg["dispatch"]["generation"], 4)
            self.assertEqual(got_bg["dispatch"]["recovered_from"]["reason"], "semantic_response_ended")
            self.assertEqual(got_wake["dispatch_generation"], 4)
            self.assertEqual(got_wake["lane_id"], "lane-01")
            subject = subprocess.check_output(
                ["git", "-C", str(work), "log", "-1", "--format=%s", "FETCH_HEAD"],
                text=True, encoding="utf-8"
            ).strip()
            self.assertEqual(subject, "Recover stalled semantic dispatch for task-liveness-001")
            self.assertNotIn("[skip ci]", subject)

            stale = reconcile_dispatch_liveness(store, req)
            self.assertTrue(stale["ok"])
            self.assertFalse(stale["matched"])
            self.assertFalse(stale["recovered"])
            self.assertEqual(stale["reason"], "stale_dispatch")

    def test_running_response_is_not_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])
            (work / "cl").mkdir()
            backend_ref = "cl/task-live-001.backend.json"
            backend = {
                "v": 1,
                "cl_id": "bg-task-live-001",
                "task_id": "task-live-001",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "x",
                "updated_at": "x",
                "dispatch": {
                    "dispatch_id": "dispatch-live-0001",
                    "wake_id": "wake-live-0001",
                    "generation": 1,
                    "fence_token": "fence-live-0001",
                    "state": "RUNNING",
                    "requested_at": "x",
                    "delivered_at": "x",
                    "acked_at": "x",
                    "acked_by_worker_ref": "pool-live-worker-0001",
                    "lease_expires_at": "2000-01-01T00:00:00+00:00",
                    "continuation_ref": "tasks/task-live-001.json",
                    "wait_ref": None,
                    "ack_source": "extension_response_start",
                },
                "conditions": [],
            }
            (work / backend_ref).write_text(json.dumps(backend), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed active response"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            store = WakeStore(base / "spool", repo_root=work)
            result = reconcile_dispatch_liveness(store, {
                "client_id": "client-live-001",
                "project_id": "git-agent-harness",
                "task_id": "task-live-001",
                "backend_cl": backend_ref,
                "dispatch_id": "dispatch-live-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-live-0001",
                "worker_ref": "pool-live-worker-0001",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-examplelane00",
                "response_running": True,
                "response_ended": False,
            })
            self.assertTrue(result["ok"])
            self.assertFalse(result["recovered"])
            self.assertEqual(result["reason"], "response_still_running")


if __name__ == "__main__":
    unittest.main()
