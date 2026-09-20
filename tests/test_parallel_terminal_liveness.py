import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_bridge.scheduler import reconcile_dispatch_liveness
from local_bridge.server import WakeStore


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
    ).strip()


class ParallelTerminalLivenessTests(unittest.TestCase):
    def test_terminal_branch_result_finalizes_before_redrive(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])

            for rel in ("tasks", "cl"):
                (work / rel).mkdir(parents=True, exist_ok=True)

            task = {
                "v": 1,
                "task_id": "parallel-lane00",
                "parent_task_id": "parallel",
                "kind": "parallel_semantic_branch",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "work_branch": "showcase/parallel-lane00",
                "backend_cl": "cl/parallel-lane00.backend.json",
                "foreground_cl": "cl/parallel.foreground.json",
                "output_root": "cases/parallel/work/lane-00",
                "branch_result_contract": {
                    "status": "PASS",
                    "required_fields": ["summary", "artifact_refs", "dispatch", "completed_at"],
                },
            }
            bg = {
                "v": 1,
                "cl_id": "bg-parallel-lane00",
                "task_id": "parallel-lane00",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "created_at": "x",
                "updated_at": "x",
                "dispatch": {
                    "dispatch_id": "dispatch-parallel-0001",
                    "wake_id": "wake-parallel-0001",
                    "generation": 7,
                    "fence_token": "fence-parallel-0001",
                    "state": "RUNNING",
                    "requested_at": "2026-09-19T00:00:00Z",
                    "delivered_at": "2026-09-19T00:00:01Z",
                    "acked_at": "2026-09-19T00:00:01Z",
                    "acked_by_worker_ref": "pool-parallel-worker",
                    "lease_expires_at": "2099-01-01T00:00:00Z",
                    "continuation_ref": None,
                    "wait_ref": None,
                    "ack_source": "extension_response_start",
                },
                "scheduling": {"completed_at": None},
                "conditions": [
                    {"id": "semantic_work", "label": "semantic", "state": "RUNNING", "detail": None, "evidence_ref": None},
                    {"id": "deterministic_execution", "label": "det", "state": "WAIT", "detail": None, "evidence_ref": None},
                    {"id": "branch_output", "label": "branch", "state": "WAIT", "detail": None, "evidence_ref": None},
                    {"id": "barrier_acceptance", "label": "barrier", "state": "WAIT", "detail": None, "evidence_ref": None},
                ],
                "error": None,
            }
            fg = {
                "v": 1,
                "cl_id": "fg-parallel",
                "task_id": "parallel",
                "scope": "foreground_supervision",
                "overall": "RUNNING",
                "created_at": "x",
                "updated_at": "x",
                "conditions": [
                    {"id": "lane00", "label": "a", "state": "RUNNING", "detail": None, "evidence_ref": None},
                    {"id": "lane01", "label": "b", "state": "RUNNING", "detail": None, "evidence_ref": None},
                    {"id": "barrier", "label": "barrier", "state": "WAIT", "detail": None, "evidence_ref": None},
                    {"id": "reducer", "label": "reducer", "state": "WAIT", "detail": None, "evidence_ref": None},
                    {"id": "final_acceptance", "label": "final", "state": "WAIT", "detail": None, "evidence_ref": None},
                ],
                "error": None,
            }

            (work / "tasks/parallel-lane00.json").write_text(json.dumps(task), encoding="utf-8")
            (work / "cl/parallel-lane00.backend.json").write_text(json.dumps(bg), encoding="utf-8")
            (work / "cl/parallel.foreground.json").write_text(json.dumps(fg), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "tasks", "cl"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed main"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"])

            subprocess.check_call(["git", "-C", str(work), "checkout", "-b", "showcase/parallel-lane00"], stdout=subprocess.DEVNULL)
            result_path = work / "cases/parallel/work/lane-00/branch_result.json"
            result_path.parent.mkdir(parents=True)
            result = {
                "v": 1,
                "task_id": "parallel-lane00",
                "status": "BLOCKED",
                "summary": "external prerequisite required",
                "dispatch": {
                    "dispatch_id": "dispatch-parallel-0001",
                    "generation": 7,
                    "fence_token": "fence-parallel-0001",
                },
                "blocker": {
                    "kind": "HOST_TOOLCHAIN_PRIVILEGE_BOUNDARY",
                    "terminal_until_external_change": True,
                },
            }
            result_path.write_text(json.dumps(result), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", str(result_path.relative_to(work))])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "terminal branch result"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "showcase/parallel-lane00"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "checkout", "main"], stdout=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            out = reconcile_dispatch_liveness(store, {
                "client_id": "client-parallel-test",
                "project_id": "git-agent-harness",
                "task_id": "parallel-lane00",
                "backend_cl": "cl/parallel-lane00.backend.json",
                "dispatch_id": "dispatch-parallel-0001",
                "dispatch_generation": 7,
                "fence_token": "fence-parallel-0001",
                "worker_ref": "pool-parallel-worker",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test00",
                "response_running": False,
                "response_ended": True,
            })

            self.assertTrue(out["ok"])
            self.assertFalse(out["recovered"])
            self.assertEqual(out["reason"], "parallel_branch_terminal_finalized")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            got_bg = json.loads(git(work, "show", "FETCH_HEAD:cl/parallel-lane00.backend.json"))
            got_fg = json.loads(git(work, "show", "FETCH_HEAD:cl/parallel.foreground.json"))
            self.assertEqual(got_bg["overall"], "BLOCKED")
            self.assertEqual(got_bg["dispatch"]["state"], "BLOCKED")
            self.assertEqual(got_bg["dispatch"]["generation"], 7)
            self.assertEqual(got_bg["error"]["kind"], "HOST_TOOLCHAIN_PRIVILEGE_BOUNDARY")
            self.assertEqual(got_fg["overall"], "BLOCKED")
            self.assertEqual(next(x for x in got_fg["conditions"] if x["id"] == "lane00")["state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
