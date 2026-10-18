import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_bridge.server import WakeStore
from local_bridge.worker_retry import begin_same_generation_retry, complete_same_generation_retry


from harness.worker_lineage_compat import (
    LAST_WORKER_REF_KEY,
    WORKER_LINEAGES_KEY,
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *args],
        text=True,
        encoding="utf-8",
    ).strip()


class WorkerSameGenerationRetryTests(unittest.TestCase):
    def _seed(self, root: Path):
        origin = root / "origin.git"
        work = root / "work"
        subprocess.check_call(
            ["git", "init", "--bare", str(origin)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["git", "clone", str(origin), str(work)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
        subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])

        parent = "parent-task-001"
        child = "child-task-001"
        request_id = "worker-retry-request-001"
        helper_id = "worker-helper-request-001"
        wake_id = "wake-samegen-001"
        dispatch_id = "dispatch-samegen-001"
        fence = "fence-samegen-001"
        worker_ref = "pool-samegen-worker-001"
        lane = "lane-01"
        project = "g-p-worker-test"
        old_conv = "worker-old-conversation-001"
        output_ref = f"evidence/{parent}/roles/helper/{helper_id}.json"

        task = {
            "v": 1,
            "task_id": child,
            "kind": "planner_worker_child",
            "parent_task_id": parent,
            "owner_task_id": parent,
            "owner_control_epoch": 1,
            "backend_cl": f"cl/{child}.backend.json",
            "lane_id": lane,
            "worker_project_key": project,
            "child_reply_ref": f"memory/worker/{child}/reply.md",
        }
        backend = {
            "v": 1,
            "cl_id": f"bg-{child}",
            "task_id": child,
            "scope": "backend_execution",
            "overall": "RUNNING",
            "dispatch": {
                "dispatch_id": dispatch_id,
                "wake_id": wake_id,
                "generation": 32,
                "fence_token": fence,
                "state": "RUNNING",
                "requested_at": "2026-09-29T00:00:00Z",
                "delivered_at": "2026-09-29T00:00:01Z",
                "acked_at": "2026-09-29T00:00:01Z",
                "acked_by_worker_ref": worker_ref,
                "lease_expires_at": "2099-01-01T00:00:00Z",
                "continuation_ref": f"tasks/{child}.json",
            },
            "scheduling": {
                "lane_id": lane,
                "worker_project_key": project,
                "owner_task_id": parent,
                "owner_control_epoch": 1,
            },
            "conditions": [],
        }
        wake = {
            "v": 1,
            "wake_id": wake_id,
            "project_id": "git-agent-harness",
            "repo": "CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
            "state": "NEED_AGENT",
            "created_at": "2026-09-29T00:00:00Z",
            "kind": "planner_worker_child",
            "lane_id": lane,
            "worker_project_key": project,
            "owner_task_id": parent,
            "owner_control_epoch": 1,
            "task_id": child,
            "backend_cl": f"cl/{child}.backend.json",
            "dispatch_id": dispatch_id,
            "dispatch_generation": 32,
            "fence_token": fence,
            "result_ref": f"evidence/{parent}/roles/worker/{child}/{wake_id}.json",
            "child_reply_ref": f"memory/worker/{child}/reply.md",
            "worker_reply_entry_ref": f"state/worker_turns/{child}/{wake_id}/reply-entry.md",
            "replace_conversation": True,
        }
        cell = {
            "v": 1,
            "task_cell_id": parent,
            "task_id": parent,
            "task_cell_project_key": "g-p-task-cell",
            "control_epoch": 1,
            "status": "PARKED_WAIT_EVENT",
            "roles": {
                "helper": {
                    "conversation_id": "helper-conversation-001",
                    "request_id": helper_id,
                    "challenge": "helper-challenge-001",
                    "project_key": "g-p-task-cell",
                }
            },
            "planner_control": {
                "v": 1,
                "enabled": True,
                "activity": "PARKED_WAIT_EVENT",
                "wait": {
                    "kind": "WAIT_WORKER_HELPER",
                    "selector": {"output_ref": output_ref},
                    "refs": [],
                },
                "runtime": {
                    "pending_role_requests": [],
                    "pending_role_outputs": [{
                        "role": "helper",
                        "kind": "worker_helper_result",
                        "request_id": helper_id,
                        "artifact_ref": output_ref,
                        "state": "RETRY_STAGED",
                        "return_mode": "WORKER_SAME_GENERATION",
                        "recovery_action": "same_generation_retry",
                        "retry_request_id": request_id,
                    }],
                },
            },
        }
        state = {
            "v": 1,
            "control_request": {
                "v": 1,
                "kind": "worker_same_generation_retry",
                "request_id": request_id,
                "status": "PENDING",
                "requested_at": "2026-09-29T00:30:00Z",
                "task_cell_project_key": "g-p-task-cell",
                "task_id": parent,
                "control_epoch": 1,
                "role": "helper",
                "challenge": "worker-retry-challenge-001",
                "helper_request_id": helper_id,
                "helper_output_ref": output_ref,
                "child_task_id": child,
                "backend_cl": f"cl/{child}.backend.json",
                "dispatch_id": dispatch_id,
                "dispatch_generation": 32,
                "fence_token": fence,
                "wake_id": wake_id,
                "worker_ref": worker_ref,
                "control_lane_id": lane,
                "worker_project_key": project,
                "old_conversation_id": old_conv,
                "child_reply_ref": f"memory/worker/{child}/reply.md",
                "incident_marker": f"WORKER_HELPER INCIDENT {helper_id}",
                "retry_mode": "same_generation",
                "exact_id_only": True,
            },
        }
        lanes = {
            "v": 1,
            "lanes": [{
                "lane_id": lane,
                "project_key": project,
                "enabled": True,
                WORKER_LINEAGES_KEY: {
                    f"{parent}::1": {
                        "owner_task_id": parent,
                        "owner_control_epoch": 1,
                        LAST_WORKER_REF_KEY: worker_ref,
                        "worker_rollover_request": None,
                    }
                },
            }],
        }

        files = {
            f"tasks/{child}.json": task,
            f"cl/{child}.backend.json": backend,
            f"requests/worker-wake/{wake_id}.json": wake,
            f"state/task_cells/{parent}.json": cell,
            "state/chatgpt.json": state,
            "state/lanes.json": lanes,
            output_ref: {
                "diagnosis": "watchdog incident inspected",
                "repair_result": "old physical Worker deleted; same-generation retry staged",
                "return_target": "worker",
                "recovery_action": "same_generation_retry",
                "deleted_conversation_id": old_conv,
            },
        }
        for rel, value in files.items():
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        reply = work / f"memory/worker/{child}/reply.md"
        reply.parent.mkdir(parents=True, exist_ok=True)
        reply.write_text(
            f"# Worker Child Reply\n\n## WORKER_HELPER INCIDENT {helper_id}\n",
            encoding="utf-8",
        )

        subprocess.check_call(["git", "-C", str(work), "add", "."])
        subprocess.check_call(
            ["git", "-C", str(work), "commit", "-m", "seed same generation retry"],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
        subprocess.check_call(
            ["git", "-C", str(work), "push", "origin", "main"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["git", "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"]
        )
        return work, {
            "parent": parent,
            "child": child,
            "request_id": request_id,
            "helper_id": helper_id,
            "wake_id": wake_id,
            "dispatch_id": dispatch_id,
            "fence": fence,
            "worker_ref": worker_ref,
            "lane": lane,
            "project": project,
            "old_conv": old_conv,
            "wake": wake,
        }

    def test_begin_requeues_exact_consumed_wake_without_generation_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work, x = self._seed(root)
            store = WakeStore(root / "spool", repo_root=work)
            consumed = store._path(store.consumed, x["wake_id"])
            consumed.parent.mkdir(parents=True, exist_ok=True)
            consumed.write_text(
                json.dumps({
                    "wake": x["wake"],
                    "consumed_at": "2026-09-29T00:01:00Z",
                    "client_id": "client-worker",
                }),
                encoding="utf-8",
            )

            result = begin_same_generation_retry(store, {
                "client_id": "client-worker",
                "project_id": "git-agent-harness",
                "request_id": x["request_id"],
                "helper_request_id": x["helper_id"],
                "task_id": x["parent"],
                "child_task_id": x["child"],
                "backend_cl": f"cl/{x['child']}.backend.json",
                "dispatch_id": x["dispatch_id"],
                "dispatch_generation": 32,
                "fence_token": x["fence"],
                "wake_id": x["wake_id"],
                "worker_ref": x["worker_ref"],
                "lane_id": x["lane"],
                "worker_project_key": x["project"],
                "old_conversation_id": x["old_conv"],
            })
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["requeued"])
            self.assertFalse(consumed.exists())
            inbox = json.loads(store._path(store.inbox, x["wake_id"]).read_text(encoding="utf-8"))
            self.assertEqual(inbox["wake_id"], x["wake_id"])
            self.assertEqual(inbox["dispatch_generation"], 32)
            self.assertEqual(inbox["dispatch_id"], x["dispatch_id"])
            self.assertEqual(inbox["fence_token"], x["fence"])
            retry = inbox["_same_generation_retry"]
            self.assertEqual(retry["request_id"], x["request_id"])
            self.assertEqual(retry["old_conversation_id"], x["old_conv"])
            self.assertEqual(retry["worker_ref"], x["worker_ref"])

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            state = json.loads(git(work, "show", "FETCH_HEAD:state/chatgpt.json"))
            self.assertEqual(state["control_request"]["status"], "APPLYING")
            backend = json.loads(git(work, "show", f"FETCH_HEAD:cl/{x['child']}.backend.json"))
            self.assertEqual(backend["dispatch"]["generation"], 32)
            self.assertEqual(backend["dispatch"]["dispatch_id"], x["dispatch_id"])
            self.assertEqual(backend["dispatch"]["fence_token"], x["fence"])
            self.assertEqual(backend["dispatch"]["wake_id"], x["wake_id"])
            self.assertEqual(backend["dispatch"]["acked_by_worker_ref"], x["worker_ref"])

    def test_complete_records_new_physical_conversation_without_changing_scheduler_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work, x = self._seed(root)
            store = WakeStore(root / "spool", repo_root=work)
            begin = begin_same_generation_retry(store, {
                "client_id": "client-worker",
                "project_id": "git-agent-harness",
                "request_id": x["request_id"],
                "helper_request_id": x["helper_id"],
                "task_id": x["parent"],
                "child_task_id": x["child"],
                "backend_cl": f"cl/{x['child']}.backend.json",
                "dispatch_id": x["dispatch_id"],
                "dispatch_generation": 32,
                "fence_token": x["fence"],
                "wake_id": x["wake_id"],
                "worker_ref": x["worker_ref"],
                "lane_id": x["lane"],
                "worker_project_key": x["project"],
                "old_conversation_id": x["old_conv"],
            })
            self.assertTrue(begin["ok"], begin)

            done = complete_same_generation_retry(store, {
                "client_id": "client-worker",
                "project_id": "git-agent-harness",
                "request_id": x["request_id"],
                "helper_request_id": x["helper_id"],
                "task_id": x["parent"],
                "child_task_id": x["child"],
                "backend_cl": f"cl/{x['child']}.backend.json",
                "dispatch_id": x["dispatch_id"],
                "dispatch_generation": 32,
                "fence_token": x["fence"],
                "wake_id": x["wake_id"],
                "worker_ref": x["worker_ref"],
                "old_conversation_id": x["old_conv"],
                "new_conversation_id": "worker-new-conversation-001",
            })
            self.assertTrue(done["ok"], done)
            self.assertTrue(done["completed"])

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            state = json.loads(git(work, "show", "FETCH_HEAD:state/chatgpt.json"))
            control = state["control_request"]
            self.assertEqual(control["status"], "DONE")
            self.assertTrue(control["result"]["same_generation"])
            self.assertEqual(control["result"]["old_conversation_id"], x["old_conv"])
            self.assertEqual(
                control["result"]["new_conversation_id"],
                "worker-new-conversation-001",
            )

            cell = json.loads(git(work, "show", f"FETCH_HEAD:state/task_cells/{x['parent']}.json"))
            pending = cell["planner_control"]["runtime"]["pending_role_outputs"][0]
            self.assertEqual(pending["state"], "WORKER_STARTED")
            self.assertEqual(pending["return_mode"], "WORKER_SAME_GENERATION")
            self.assertEqual(
                pending["retry_conversation_id"],
                "worker-new-conversation-001",
            )

            backend = json.loads(git(work, "show", f"FETCH_HEAD:cl/{x['child']}.backend.json"))
            self.assertEqual(backend["dispatch"]["generation"], 32)
            self.assertEqual(backend["dispatch"]["dispatch_id"], x["dispatch_id"])
            self.assertEqual(backend["dispatch"]["fence_token"], x["fence"])
            self.assertEqual(backend["dispatch"]["wake_id"], x["wake_id"])
            self.assertEqual(backend["dispatch"]["acked_by_worker_ref"], x["worker_ref"])


if __name__ == "__main__":
    unittest.main()
