from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_bridge.planner_memory import make_planner_current
from local_bridge.planner_runtime import sync_semantic_turn
from local_bridge.server import WakeStore
from local_bridge.task_cell_ledgers import (
    make_planner_turn_outcome,
    planner_semantic_memory_path,
    planner_turn_memory_entry_header,
    planner_turn_memory_entry_path,
    planner_turn_outcome_path,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
    ).strip()


def init_repo(base: Path) -> tuple[Path, Path]:
    origin = base / "origin.git"
    work = base / "work"
    subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "-C", str(work), "config", "user.name", "semantic-sync-test"])
    subprocess.check_call(["git", "-C", str(work), "config", "user.email", "semantic-sync-test@example.invalid"])
    return origin, work


def commit_main(work: Path, origin: Path, message: str) -> None:
    subprocess.check_call(["git", "-C", str(work), "add", "."])
    subprocess.check_call(["git", "-C", str(work), "commit", "-m", message], stdout=subprocess.DEVNULL)
    try:
        subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pass
    subprocess.check_call(["git", "-C", str(work), "push", "origin", "HEAD:main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"])


class SemanticSyncKickTests(unittest.TestCase):
    def test_planner_sync_reads_turn_signal_and_reduces_current_bound_turn(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin, work = init_repo(base)
            task_id = "semantic-sync-planner-001"
            epoch = 1
            generation = 1
            fence = "planner-fence-semantic-sync-001"
            doorbell_id = "doorbell-semantic-sync-001"
            request_id = "planner-request-semantic-sync-001"
            conversation_id = "planner-conversation-semantic-sync-001"
            task_ref = f"tasks/{task_id}.json"
            plan_ref = f"tasks/{task_id}.plan.json"
            cell_ref = f"state/task_cells/{task_id}.json"

            initial = {
                task_ref: {
                    "v": 1,
                    "task_id": task_id,
                    "task_contract_revision": 1,
                    "goal": "exercise active Planner semantic sync",
                },
                plan_ref: {
                    "v": 1,
                    "task_id": task_id,
                    "plan_revision": 1,
                    "stages": ["review"],
                },
                "state/chatgpt.json": {
                    "v": 1,
                    "agent": "chatgpt",
                    "control_request": None,
                },
                "state/lanes.json": {
                    "v": 1,
                    "lanes": [{
                        "lane_id": "lane-00",
                        "project_key": "g-p-semantic-sync",
                        "enabled": True,
                        "task_pools": {},
                    }],
                },
            }
            for rel, value in initial.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            commit_main(work, origin, "seed semantic sync planner inputs")

            task_sha = git(work, "rev-parse", f"HEAD:{task_ref}")
            plan_sha = git(work, "rev-parse", f"HEAD:{plan_ref}")
            current = make_planner_current(
                task_id=task_id,
                control_epoch=epoch,
                planner_generation=generation,
                planner_fence_token=fence,
                task_contract_ref=task_ref,
                task_contract_revision=1,
                task_contract_blob_sha=task_sha,
                plan_ref=plan_ref,
                plan_blob_sha=plan_sha,
                written_at="2026-09-28T00:00:00+00:00",
            )
            memory_ref = planner_semantic_memory_path(task_id)
            memory_entry_ref = planner_turn_memory_entry_path(task_id, doorbell_id)
            outcome_ref = planner_turn_outcome_path(task_id, doorbell_id)
            outcome = make_planner_turn_outcome(
                task_id=task_id,
                control_epoch=epoch,
                doorbell_id=doorbell_id,
                planner_generation=generation,
                planner_fence_token=fence,
            )
            outcome["semantic"] = {}
            outcome["turn_signal"] = "done"

            cell = {
                "v": 1,
                "task_cell_id": task_id,
                "task_id": task_id,
                "task_cell_project_key": "g-p-taskcell",
                "control_epoch": epoch,
                "status": "ACTIVE",
                "roles": {
                    "planner": {
                        "role": "planner",
                        "status": "ACTIVE",
                        "conversation_id": conversation_id,
                        "request_id": request_id,
                        "challenge": "planner-challenge-semantic-sync-001",
                    },
                },
                "planner_control": {
                    "v": 1,
                    "feature": "HYBRID_ACTIVE",
                    "enabled": True,
                    "task_id": task_id,
                    "control_epoch": epoch,
                    "activity": "WAKING",
                    "semantic_authority_closed": False,
                    "authority": {
                        "control_epoch": epoch,
                        "planner_generation": generation,
                        "planner_fence_token": fence,
                        "conversation_id": conversation_id,
                        "request_id": request_id,
                        "challenge": "planner-challenge-semantic-sync-001",
                        "project_key": "g-p-taskcell",
                        "semantic_authority": True,
                    },
                    "wait": None,
                    "inbox": {
                        "events": {},
                        "active_doorbell": {
                            "doorbell_id": doorbell_id,
                            "event_ids": [],
                            "planner_generation": generation,
                            "planner_fence_token": fence,
                            "delivery_state": "CLAIMED",
                        },
                    },
                    "successor": {"state": "NONE"},
                    "retired_planners": [],
                    "runtime": {
                        "foreground_runtime_dependency": False,
                        "pending_output": {
                            "kind": "PLANNER_TURN",
                            "ref": outcome_ref,
                            "request_id": request_id,
                            "status": "WAITING",
                        },
                        "active_turn": {
                            "doorbell_id": doorbell_id,
                            "memory_entry_ref": memory_entry_ref,
                            "outcome_ref": outcome_ref,
                            "slots": [],
                        },
                        "owned_children": [],
                    },
                },
            }

            files = {
                cell_ref: cell,
                f"memory/planner/{task_id}/current.json": current,
                memory_ref: "# Planner Memory\n",
                memory_entry_ref: planner_turn_memory_entry_header(
                    entry_id=doorbell_id,
                    planner_generation=generation,
                ) + "Reviewed the current state and no new Worker direction is needed.\n",
                outcome_ref: outcome,
            }
            for rel, value in files.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(value, dict):
                    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                else:
                    target.write_text(str(value), encoding="utf-8")
            commit_main(work, origin, "write durable planner turn signal")

            store = WakeStore(base / "spool", repo_root=work)
            result = sync_semantic_turn(store, {
                "client_id": "client-semantic-sync",
                "project_id": "git-agent-harness",
                "role": "planner",
                "task_id": task_id,
                "control_epoch": epoch,
                "conversation_id": conversation_id,
                "request_id": request_id,
                "output_ref": outcome_ref,
            })
            self.assertTrue(result["ok"])
            self.assertTrue(result["accepted"])
            self.assertEqual(result["result"]["action"], "planner_turn_reduced")
            self.assertEqual(result["result"]["outcome"], "CONTINUE")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            reduced_outcome = json.loads(git(work, "show", f"FETCH_HEAD:{outcome_ref}"))
            reduced_cell = json.loads(git(work, "show", f"FETCH_HEAD:{cell_ref}"))
            self.assertEqual(reduced_outcome["turn_signal"], "done")
            self.assertEqual(reduced_outcome["outcome"], "CONTINUE")
            self.assertEqual(reduced_cell["planner_control"]["last_decision"]["decision_ref"], outcome_ref)
            self.assertIsNone(reduced_cell["planner_control"]["runtime"]["active_turn"])

            duplicate = sync_semantic_turn(store, {
                "client_id": "client-semantic-sync",
                "project_id": "git-agent-harness",
                "role": "planner",
                "task_id": task_id,
                "control_epoch": epoch,
                "conversation_id": conversation_id,
                "request_id": request_id,
                "output_ref": outcome_ref,
            })
            self.assertTrue(duplicate["accepted"])
            self.assertTrue(duplicate["duplicate"])

    def test_helper_sync_reads_done_from_durable_output(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin, work = init_repo(base)
            task_id = "semantic-sync-helper-001"
            output_ref = "evidence/semantic-sync-helper-001/roles/helper/helper-request-001.json"
            cell_ref = f"state/task_cells/{task_id}.json"
            request_id = "helper-request-001"
            conversation_id = "helper-conversation-001"

            cell = {
                "v": 1,
                "task_id": task_id,
                "control_epoch": 1,
                "status": "ACTIVE",
                "roles": {
                    "helper": {
                        "role": "helper",
                        "conversation_id": conversation_id,
                        "request_id": request_id,
                    },
                },
                "planner_control": {
                    "enabled": True,
                    "runtime": {
                        "pending_role_outputs": [{
                            "role": "helper",
                            "kind": "helper_result",
                            "request_id": request_id,
                            "artifact_ref": output_ref,
                            "state": "WAITING",
                        }],
                        "pending_output": {
                            "kind": "PLANNER_TURN",
                            "status": "WAITING",
                        },
                    },
                },
            }
            helper_output = {
                "diagnosis": "Bound Planner input was interrupted.",
                "repair_result": "Recovered the exact bound input and verified response start.",
                "turn_signal": "done",
            }
            for rel, value in {
                cell_ref: cell,
                output_ref: helper_output,
            }.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            commit_main(work, origin, "write durable helper turn signal")

            store = WakeStore(base / "spool", repo_root=work)
            req = {
                "client_id": "client-semantic-sync",
                "project_id": "git-agent-harness",
                "role": "helper",
                "task_id": task_id,
                "control_epoch": 1,
                "conversation_id": conversation_id,
                "request_id": request_id,
                "output_ref": output_ref,
            }
            result = sync_semantic_turn(store, req)
            self.assertTrue(result["ok"])
            self.assertTrue(result["accepted"])
            self.assertEqual(result["role"], "helper")

            subprocess.check_call(["git", "-C", str(work), "fetch", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            updated = json.loads(git(work, "show", f"FETCH_HEAD:{cell_ref}"))
            pending = updated["planner_control"]["runtime"]["pending_role_outputs"][0]
            self.assertTrue(pending["done_observed_at"])

            duplicate = sync_semantic_turn(store, req)
            self.assertTrue(duplicate["accepted"])


if __name__ == "__main__":
    unittest.main()
