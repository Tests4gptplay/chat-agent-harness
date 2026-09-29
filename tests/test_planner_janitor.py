#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from local_bridge.planner_janitor import (
    PlannerJanitorError,
    _managed_roots,
    _safe_git_cleanup_path,
    compact_terminal_control_records,
)


TASK = "planner-hybrid-control-082"


class PlannerJanitorScopeTests(unittest.TestCase):
    def test_managed_roots_are_task_scoped_and_never_bridge_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "bridge"
            runtime = root / "runtime"
            logs = root / "logs"
            extension_events = root / "extension-events"
            errors = root / "errors"
            for path in (root, runtime, logs, extension_events, errors):
                path.mkdir(parents=True, exist_ok=True)
            store = SimpleNamespace(
                root=root,
                runtime=runtime,
                logs=logs,
                extension_events=extension_events,
                errors=errors,
            )
            roots = _managed_roots(store, TASK)
            self.assertNotIn("bridge_root", roots)
            self.assertEqual(
                set(roots),
                {"task_runtime", "task_logs", "task_extension_events", "task_errors"},
            )
            for value in roots.values():
                path = Path(value)
                self.assertEqual(path.name, TASK)
                self.assertEqual(path.parent.name, "tasks")
                self.assertNotEqual(path, root.resolve())

    def test_terminal_cleaner_compacts_exact_task_owned_control_records(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tasks").mkdir()
            (root / "cl").mkdir()
            (root / "state" / "handoffs").mkdir(parents=True)
            (root / "state" / "request_receipts").mkdir(parents=True)
            (root / "state" / "task_cells").mkdir(parents=True)
            (root / "requests" / "worker-wake").mkdir(parents=True)

            child = TASK + "-child-a"
            (root / "tasks" / f"{TASK}.json").write_text(
                '{"v":1,"task_id":"' + TASK + '","tracking_issue":82,"goal":"large active contract"}',
                encoding="utf-8",
            )
            (root / "tasks" / f"{TASK}.plan.json").write_text(
                '{"v":1,"task_id":"' + TASK + '","plan_revision":10,"status":"RUNNING","steps":["many"]}',
                encoding="utf-8",
            )
            (root / "tasks" / f"{child}.json").write_text(
                '{"v":1,"task_id":"' + child + '","parent_task_id":"' + TASK + '","worker_instructions":["large"]}',
                encoding="utf-8",
            )
            (root / "cl" / f"{child}.backend.json").write_text(
                json.dumps({
                    "task_id": child,
                    "overall": "GREEN",
                    "result_ref": f"cases/{child}/branch_result.json",
                    "dispatch": {"dispatch_id": "d1", "generation": 2, "fence_token": "f2", "state": "DONE"},
                }),
                encoding="utf-8",
            )
            handoff = root / "state" / "handoffs" / f"{child}-pool-old.json"
            handoff.write_text(json.dumps({
                "v": 1, "kind": "worker_handoff_packet", "task_id": child,
                "handoff_id": "pool-old", "lane_id": "lane-00", "generation": 2,
                "fence_token": "f2", "reason": "context_compacted", "memory_capsule": {"large": "payload"},
            }), encoding="utf-8")
            unrelated = root / "state" / "handoffs" / "other-task.json"
            unrelated.write_text(json.dumps({"v": 1, "task_id": "other-task", "payload": "keep"}), encoding="utf-8")

            request_ref = f"requests/worker-wake/{child}.json"
            (root / request_ref).write_text(json.dumps({"task_id": child}), encoding="utf-8")
            receipt = root / "state" / "request_receipts" / "abc.json"
            receipt.write_text(json.dumps({
                "v": 1, "kind": "worker_wake", "path": request_ref, "blob_oid": "blob",
                "request_key": "abc", "phase": "NEEDS_RECONCILE", "diagnostic": {"large": "payload"},
            }), encoding="utf-8")
            (root / "state" / "foreground-next-task.json").write_text(
                json.dumps({"v": 1, "kind": "foreground_next_task", "task_id": TASK, "status": "ACTIVE"}),
                encoding="utf-8",
            )
            task_cell = root / "state" / "task_cells" / f"{TASK}.json"
            task_cell.write_text(json.dumps({
                "v": 1,
                "task_id": TASK,
                "status": "CLEANUP",
                "planner_control": {"enabled": True, "activity": "CLEANUP"},
                "inbox": {"events": {"stale": {"state": "PENDING"}}},
            }), encoding="utf-8")

            result = compact_terminal_control_records(
                root,
                task_id=TASK,
                runtime={"owned_children": [{"child_task_id": child, "backend_cl": f"cl/{child}.backend.json"}]},
                terminal_refs={
                    "final_result_ref": f"evidence/{TASK}/final-result.json",
                    "cleanup_result_ref": f"evidence/{TASK}/cleanup/final.json",
                },
                completed_at="2026-09-22T00:00:00+00:00",
            )

            self.assertEqual(result["status"], "COMPACTED")
            self.assertEqual(set(result["owned_task_ids"]), {TASK, child})
            parent = json.loads((root / "tasks" / f"{TASK}.json").read_text(encoding="utf-8"))
            self.assertEqual(parent["status"], "DONE")
            self.assertNotIn("goal", parent)
            plan = json.loads((root / "tasks" / f"{TASK}.plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["status"], "DONE")
            self.assertEqual(plan["plan_revision"], 10)
            compact_child = json.loads((root / "tasks" / f"{child}.json").read_text(encoding="utf-8"))
            self.assertEqual(compact_child["status"], "DONE")
            self.assertEqual(compact_child["dispatch"]["generation"], 2)
            retired_handoff = json.loads(handoff.read_text(encoding="utf-8"))
            self.assertEqual(retired_handoff["status"], "RETIRED")
            self.assertNotIn("memory_capsule", retired_handoff)
            self.assertEqual(json.loads(unrelated.read_text(encoding="utf-8"))["payload"], "keep")
            compact_receipt = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(compact_receipt["phase"], "SUPERSEDED")
            self.assertTrue(compact_receipt["terminal_compacted"])
            next_task = json.loads((root / "state" / "foreground-next-task.json").read_text(encoding="utf-8"))
            self.assertEqual(next_task["status"], "DONE")
            self.assertFalse(task_cell.exists())
            self.assertIn(f"state/task_cells/{TASK}.json", result["compacted_paths"])

    def test_git_cleanup_is_limited_to_exact_task_owned_prefixes(self):
        allowed = [
            f"memory/planner/{TASK}/current.json",
            f"evidence/{TASK}/cleanup/raw.log",
            f"cases/{TASK}/scratch/result.json",
        ]
        for path in allowed:
            self.assertEqual(_safe_git_cleanup_path(TASK, path), path)

        denied = [
            "state/chatgpt.json",
            "AGENTS.md",
            "extension/background.js",
            f"memory/planner/other-task/current.json",
            f"evidence/other-task/cleanup/raw.log",
            f"cases/other-task/scratch/result.json",
        ]
        for path in denied:
            with self.subTest(path=path):
                with self.assertRaises(PlannerJanitorError) as ctx:
                    _safe_git_cleanup_path(TASK, path)
                self.assertEqual(ctx.exception.code, "CLEANUP_GIT_PATH_NOT_TASK_OWNED")


if __name__ == "__main__":
    unittest.main()
