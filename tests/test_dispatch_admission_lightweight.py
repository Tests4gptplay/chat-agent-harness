from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_bridge.server import WakeStore


class LightweightDispatchAdmissionTests(unittest.TestCase):
    def test_dispatch_accept_updates_git_without_worktree_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            spool = base / "spool"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])

            task_id = "dispatch-lightweight-test"
            backend = f"cl/{task_id}.backend.json"
            lane_id = "lane-00"
            project_key = "g-p-test123"
            worker_ref = "pool-lightweight-test"
            dispatch_id = "dispatch-lightweight-001"
            fence = "fence-lightweight-001"

            files = {
                f"tasks/{task_id}.json": {
                    "v": 1,
                    "task_id": task_id,
                    "backend_cl": backend,
                    "lane_id": lane_id,
                    "worker_project_key": project_key,
                    "owner_task_id": task_id,
                    "owner_control_epoch": 1,
                },
                backend: {
                    "v": 1,
                    "cl_id": "bg-dispatch-lightweight-test",
                    "task_id": task_id,
                    "scope": "backend_execution",
                    "overall": "READY",
                    "created_at": "2026-09-22T00:00:00+00:00",
                    "updated_at": "2026-09-22T00:00:00+00:00",
                    "scheduling": {
                        "lane_id": lane_id,
                        "worker_project_key": project_key,
                        "owner_task_id": task_id,
                        "owner_control_epoch": 1,
                    },
                    "conditions": [{
                        "id": "claimed",
                        "label": "Worker response start",
                        "state": "WAIT",
                        "detail": None,
                        "evidence_ref": None,
                    }],
                    "dispatch": {
                        "dispatch_id": dispatch_id,
                        "wake_id": "wake-lightweight-001",
                        "generation": 1,
                        "fence_token": fence,
                        "state": "READY",
                        "requested_at": "2026-09-22T00:00:00+00:00",
                        "delivered_at": None,
                        "acked_at": None,
                        "acked_by_worker_ref": None,
                        "lease_expires_at": None,
                        "continuation_ref": f"tasks/{task_id}.json",
                        "wait_ref": None,
                        "ack_source": None,
                    },
                },
                "state/chatgpt.json": {"v": 1, "agent": "chatgpt", "updated": "2026-09-22"},
                "state/lanes.json": {
                    "v": 1,
                    "lanes": [{
                        "lane_id": lane_id,
                        "project_key": project_key,
                        "task_pools": {},
                    }],
                },
            }
            for rel, value in files.items():
                path = work / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(spool, repo_root=work)
            original_git = store._git
            calls = []
            def observed_git(*args):
                calls.append(args)
                return original_git(*args)
            store._git = observed_git

            result = store.dispatch_accept({
                "client_id": "dispatch-test-client",
                "project_id": "git-agent-harness",
                "task_id": task_id,
                "backend_cl": backend,
                "dispatch_id": dispatch_id,
                "dispatch_generation": 1,
                "fence_token": fence,
                "worker_ref": worker_ref,
                "lane_id": lane_id,
                "worker_project_key": project_key,
            })
            self.assertTrue(result["ok"])
            self.assertTrue(result["accepted"])
            self.assertFalse(any(args and args[0] == "worktree" for args in calls))

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw = subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{backend}"],
                text=True,
                encoding="utf-8",
            )
            accepted = json.loads(raw)
            self.assertEqual(accepted["dispatch"]["state"], "RUNNING")
            self.assertEqual(accepted["dispatch"]["acked_by_worker_ref"], worker_ref)
            self.assertEqual(accepted["dispatch"]["ack_source"], "extension_response_start")
            self.assertEqual(accepted["conditions"][0]["state"], "GREEN")

            lanes_raw = subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:state/lanes.json"],
                text=True,
                encoding="utf-8",
            )
            lanes = json.loads(lanes_raw)
            pool = lanes["lanes"][0]["task_pools"][f"{task_id}::1"]
            self.assertEqual(pool["last_pool_takeover_id"], worker_ref)

            leftovers = list(spool.joinpath("runtime").glob("dispatch-accept-*"))
            self.assertEqual(leftovers, [])


    def test_recovery_successor_can_replace_exact_predecessor_owner(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            spool = base / "spool"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])

            task_id = "planner-child-replacement"
            backend = f"cl/{task_id}.backend.json"
            lane_id = "lane-01"
            project_key = "g-p-test-replacement"
            predecessor = "pool-predecessor"
            successor = "pool-successor"
            dispatch_id = "dispatch-recovery-g2"
            fence = "fence-recovery-g2"

            files = {
                f"tasks/{task_id}.json": {
                    "v": 1,
                    "task_id": task_id,
                    "kind": "planner_worker_child",
                    "backend_cl": backend,
                    "lane_id": lane_id,
                    "worker_project_key": project_key,
                    "owner_task_id": task_id,
                    "owner_control_epoch": 1,
                },
                backend: {
                    "v": 1,
                    "cl_id": "bg-planner-child-replacement",
                    "task_id": task_id,
                    "scope": "backend_execution",
                    "overall": "READY",
                    "created_at": "2026-09-22T00:00:00+00:00",
                    "updated_at": "2026-09-22T00:00:00+00:00",
                    "scheduling": {
                        "lane_id": lane_id,
                        "worker_project_key": project_key,
                        "owner_task_id": task_id,
                        "owner_control_epoch": 1,
                    },
                    "conditions": [{
                        "id": "claimed",
                        "label": "Worker response start",
                        "state": "WAIT",
                        "detail": None,
                        "evidence_ref": None,
                    }],
                    "dispatch": {
                        "dispatch_id": dispatch_id,
                        "wake_id": "wake-recovery-g2",
                        "generation": 2,
                        "fence_token": fence,
                        "state": "READY",
                        "requested_at": "2026-09-22T00:10:00+00:00",
                        "delivered_at": None,
                        "acked_at": None,
                        "acked_by_worker_ref": None,
                        "lease_expires_at": None,
                        "continuation_ref": f"tasks/{task_id}.json",
                        "wait_ref": None,
                        "ack_source": None,
                        "recovered_from": {
                            "dispatch_id": "dispatch-recovery-g1",
                            "generation": 1,
                            "fence_token": "fence-recovery-g1",
                            "state": "RUNNING",
                            "acked_at": "2026-09-22T00:00:01+00:00",
                            "acked_by_worker_ref": predecessor,
                            "reason": "semantic_lease_expired",
                            "recovered_at": "2026-09-22T00:10:00+00:00",
                        },
                    },
                },
                "state/chatgpt.json": {"v": 1, "agent": "chatgpt", "updated": "2026-09-22"},
                "state/lanes.json": {
                    "v": 1,
                    "lanes": [{
                        "lane_id": lane_id,
                        "project_key": project_key,
                        "task_pools": {
                            f"{task_id}::1": {
                                "owner_task_id": task_id,
                                "owner_control_epoch": 1,
                                "last_pool_takeover_id": predecessor,
                                "worker_rollover_request": None,
                            },
                        },
                    }],
                },
            }
            for rel, value in files.items():
                p = work / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed replacement"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(spool, repo_root=work)
            result = store.dispatch_accept({
                "client_id": "dispatch-test-client",
                "project_id": "git-agent-harness",
                "task_id": task_id,
                "backend_cl": backend,
                "dispatch_id": dispatch_id,
                "dispatch_generation": 2,
                "fence_token": fence,
                "worker_ref": successor,
                "lane_id": lane_id,
                "worker_project_key": project_key,
            })
            self.assertTrue(result["ok"])
            self.assertTrue(result["accepted"])

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            accepted = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{backend}"],
                text=True,
                encoding="utf-8",
            ))
            self.assertEqual(accepted["dispatch"]["state"], "RUNNING")
            self.assertEqual(accepted["dispatch"]["acked_by_worker_ref"], successor)

            lanes = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", "FETCH_HEAD:state/lanes.json"],
                text=True,
                encoding="utf-8",
            ))
            pool = lanes["lanes"][0]["task_pools"][f"{task_id}::1"]
            self.assertEqual(pool["last_pool_takeover_id"], successor)


if __name__ == "__main__":
    unittest.main()
