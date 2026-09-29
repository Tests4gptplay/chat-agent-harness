#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from harness.single_thread import publish_foreground_terminal_event
from local_bridge.scheduler import _try_finalize_parallel_branch, reconcile_foreground_terminal
from local_bridge.server import WakeStore


TASK_ID = "foreground-terminal-test-079"
ACTION_ID = "action-foreground-terminal-test-079"
RESULT_ID = "result-action-foreground-terminal-test-079"
ACTION_REF = f"actions/stage0/{TASK_ID}.json"
RESULT_REF = f"results/{TASK_ID}.json"
FG_REF = f"cl/{TASK_ID}.foreground.json"
BG_REF = f"cl/{TASK_ID}.backend.json"
EVENT_ID = "terminal-0123456789abcdef01234567"


def base_action(*, worker_continuation: bool = False) -> dict:
    return {
        "v": 1,
        "action_id": ACTION_ID,
        "task_id": TASK_ID,
        "round": 1,
        "executor": "noop",
        "foreground_cl": FG_REF,
        "backend_cl": BG_REF,
        "worker_continuation": worker_continuation,
        "expected_evidence": [],
        "payload": {},
    }


def base_result(status: str = "ERROR") -> dict:
    return {
        "v": 1,
        "result_id": RESULT_ID,
        "action_id": ACTION_ID,
        "task_id": TASK_ID,
        "round": 1,
        "status": status,
        "summary": "synthetic terminal",
        "evidence": [],
        "artifacts": [],
    }


def base_event(
    *,
    status: str = "ERROR",
    state: str = "PENDING",
    due_at: str = "2099-01-01T00:00:00+00:00",
    dispatch: dict | None = None,
) -> dict:
    event = {
        "v": 1,
        "kind": "executor_terminal",
        "event_id": EVENT_ID,
        "task_id": TASK_ID,
        "control_epoch": 1,
        "action_id": ACTION_ID,
        "action_ref": ACTION_REF,
        "result_id": RESULT_ID,
        "result_ref": RESULT_REF,
        "foreground_cl_ref": FG_REF,
        "backend_cl_ref": BG_REF,
        "terminal_status": status,
        "published_at": "2026-09-20T00:00:00+00:00",
        "delivery": {
            "state": state,
            "claim_id": "claim-existing" if state != "PENDING" else None,
            "claimed_at": "2026-09-20T00:00:01+00:00" if state != "PENDING" else None,
            "delivered_at": "2026-09-20T00:00:02+00:00" if state in {"DELIVERED", "CONSUMED"} else None,
            "consumed_at": "2026-09-20T00:00:03+00:00" if state == "CONSUMED" else None,
        },
        "watchdog": {
            "due_at": due_at,
            "claim_id": None,
            "escalated_at": None,
        },
    }
    if dispatch is not None:
        event["dispatch"] = dispatch
    return event


class TerminalPublishTests(unittest.TestCase):
    def test_publish_pass_error_blocked_and_skip_continuation(self):
        for status, overall, expected in (
            ("PASS", "GREEN", "PASS"),
            ("ERROR", "ERROR", "ERROR"),
            ("BLOCKED", "BLOCKED", "BLOCKED"),
        ):
            with self.subTest(status=status):
                fg = {"scope": "foreground_supervision", "task_id": TASK_ID, "overall": overall}
                bg = {"scope": "backend_execution", "task_id": TASK_ID}
                event = publish_foreground_terminal_event(
                    action=base_action(),
                    action_ref=ACTION_REF,
                    result=base_result(status),
                    result_ref=RESULT_REF,
                    fg=fg,
                    bg=bg,
                    verified=True,
                )
                self.assertIsNotNone(event)
                self.assertEqual(event["terminal_status"], expected)
                self.assertEqual(event["delivery"]["state"], "PENDING")
                self.assertEqual(event["watchdog"]["due_at"][-6:], "+00:00")

        fg = {"scope": "foreground_supervision", "task_id": TASK_ID, "overall": "GREEN"}
        self.assertIsNone(
            publish_foreground_terminal_event(
                action=base_action(worker_continuation=True),
                action_ref=ACTION_REF,
                result=base_result("PASS"),
                result_ref=RESULT_REF,
                fg=fg,
                bg={"scope": "backend_execution", "task_id": TASK_ID},
                verified=True,
            )
        )
        self.assertNotIn("terminal_event", fg)

    def test_verification_failure_shape_does_not_publish_false_terminal(self):
        fg = {"scope": "foreground_supervision", "task_id": TASK_ID, "overall": "ERROR"}
        result = base_result("PASS")
        self.assertIsNone(
            publish_foreground_terminal_event(
                action=base_action(),
                action_ref=ACTION_REF,
                result=result,
                result_ref=RESULT_REF,
                fg=fg,
                bg={"scope": "backend_execution", "task_id": TASK_ID},
                verified=False,
            )
        )


class TerminalBridgeTests(unittest.TestCase):
    def _repo(
        self,
        *,
        event: dict,
        result: dict | None = None,
        action: dict | None = None,
        backend: dict | None = None,
    ):
        td = tempfile.TemporaryDirectory()
        base = Path(td.name)
        origin = base / "origin.git"
        work = base / "work"
        subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
        subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

        def write(rel: str, value: dict):
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

        action = action or base_action()
        write(ACTION_REF, action)
        if result is not None:
            write(RESULT_REF, result)
        fg = {
            "v": 1,
            "task_id": TASK_ID,
            "scope": "foreground_supervision",
            "overall": "ERROR" if event["terminal_status"] == "ERROR" else (
                "BLOCKED" if event["terminal_status"] == "BLOCKED" else "GREEN"
            ),
            "terminal_event": event,
            "conditions": [],
        }
        write(FG_REF, fg)
        if backend is not None:
            write(BG_REF, backend)
        subprocess.check_call(["git", "-C", str(work), "add", "."], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed terminal"], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        store = WakeStore(base / "spool", repo_root=work)
        return td, store

    def test_claim_deliver_consume_exactly_once_across_repoll(self):
        td, store = self._repo(event=base_event(), result=base_result("ERROR"))
        self.addCleanup(td.cleanup)
        req = {"client_id": "client-test-079", "project_id": "git-agent-harness"}
        first = reconcile_foreground_terminal(store, req)
        self.assertTrue(first["ok"])
        self.assertEqual(first["event"]["delivery"]["state"], "CLAIMED")
        claim_id = first["event"]["delivery"]["claim_id"]
        self.assertTrue(claim_id.startswith("claim-"))

        second = reconcile_foreground_terminal(store, req)
        self.assertTrue(second["ok"])
        self.assertIsNone(second["event"])

        delivered = reconcile_foreground_terminal(store, {
            **req,
            "terminal_op": "delivered",
            "foreground_cl": FG_REF,
            "event_id": EVENT_ID,
            "claim_id": claim_id,
        })
        self.assertTrue(delivered["ok"])
        self.assertEqual(delivered["event"]["delivery"]["state"], "DELIVERED")

        consumed = reconcile_foreground_terminal(store, {
            **req,
            "terminal_op": "consumed",
            "foreground_cl": FG_REF,
            "event_id": EVENT_ID,
            "claim_id": claim_id,
        })
        self.assertTrue(consumed["ok"])
        self.assertEqual(consumed["event"]["delivery"]["state"], "CONSUMED")

        duplicate = reconcile_foreground_terminal(store, {
            **req,
            "terminal_op": "consumed",
            "foreground_cl": FG_REF,
            "event_id": EVENT_ID,
            "claim_id": claim_id,
        })
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate["duplicate"])

    def test_expired_pending_remains_foreground_claimable(self):
        td, store = self._repo(
            event=base_event(state="PENDING", due_at="2026-09-20T00:02:00+00:00"),
            result=base_result("ERROR"),
        )
        self.addCleanup(td.cleanup)
        req = {"client_id": "client-test-079", "project_id": "git-agent-harness"}
        clock = lambda: datetime(2026, 9, 20, 0, 2, 1, tzinfo=timezone.utc)
        status = reconcile_foreground_terminal(store, req, now_fn=clock)
        self.assertTrue(status["ok"])
        self.assertEqual(status["event"]["event_id"], EVENT_ID)
        self.assertEqual(status["event"]["delivery"]["state"], "CLAIMED")

    def test_missing_result_is_not_consumable(self):
        td, store = self._repo(event=base_event(), result=None)
        self.addCleanup(td.cleanup)
        got = reconcile_foreground_terminal(store, {
            "client_id": "client-test-079",
            "project_id": "git-agent-harness",
        })
        self.assertTrue(got["ok"])
        self.assertIsNone(got["event"])

    def test_terminal_consume_follows_delivery_state(self):
        td, store = self._repo(event=base_event(), result=base_result("ERROR"))
        self.addCleanup(td.cleanup)
        req = {"client_id": "client-test-079", "project_id": "git-agent-harness"}
        claimed = reconcile_foreground_terminal(store, req)
        self.assertTrue(claimed["ok"])
        claim_id = claimed["event"]["delivery"]["claim_id"]
        delivered = reconcile_foreground_terminal(store, {
            **req,
            "terminal_op": "delivered",
            "foreground_cl": FG_REF,
            "event_id": EVENT_ID,
            "claim_id": claim_id,
        })
        self.assertTrue(delivered["ok"])
        consumed = reconcile_foreground_terminal(store, {
            **req,
            "terminal_op": "consumed",
            "foreground_cl": FG_REF,
            "event_id": EVENT_ID,
            "claim_id": claim_id,
        })
        self.assertTrue(consumed["ok"])
        self.assertEqual(consumed["event"]["delivery"]["state"], "CONSUMED")

    def test_stale_result_identity_is_not_claimed(self):
        bad = base_result("ERROR")
        bad["action_id"] = "action-other-079"
        td, store = self._repo(event=base_event(), result=bad)
        self.addCleanup(td.cleanup)
        got = reconcile_foreground_terminal(store, {
            "client_id": "client-test-079",
            "project_id": "git-agent-harness",
        })
        self.assertTrue(got["ok"])
        self.assertIsNone(got["event"])

    def test_worker_continuation_event_is_rejected(self):
        td, store = self._repo(
            event=base_event(),
            result=base_result("ERROR"),
            action=base_action(worker_continuation=True),
        )
        self.addCleanup(td.cleanup)
        got = reconcile_foreground_terminal(store, {
            "client_id": "client-test-079",
            "project_id": "git-agent-harness",
        })
        self.assertTrue(got["ok"])
        self.assertIsNone(got["event"])

    def test_stale_dispatch_fence_rejected_without_mutation(self):
        old = {"dispatch_id": "dispatch-old-079", "generation": 1, "fence_token": "fence-old-079"}
        new = {"dispatch_id": "dispatch-new-079", "generation": 2, "fence_token": "fence-new-079"}
        td, store = self._repo(
            event=base_event(state="CLAIMED", dispatch=old),
            result=base_result("ERROR"),
            backend={
                "v": 1,
                "task_id": TASK_ID,
                "scope": "backend_execution",
                "dispatch": new,
                "conditions": [],
            },
        )
        self.addCleanup(td.cleanup)
        got = reconcile_foreground_terminal(store, {
            "client_id": "client-test-079",
            "project_id": "git-agent-harness",
            "terminal_op": "delivered",
            "foreground_cl": FG_REF,
            "event_id": EVENT_ID,
            "claim_id": "claim-existing",
        })
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "FOREGROUND_TERMINAL_STALE_FENCE")


class SingleLaneFinalizerTests(unittest.TestCase):
    def test_single_lane_branch_result_is_finalized_instead_of_redispatched(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
            subprocess.check_call(["git", "init", "--bare", str(origin)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "clone", str(origin), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

            (work / "README.md").write_text("source\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "README.md"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "source"], stdout=subprocess.DEVNULL)
            source_commit = subprocess.check_output(
                ["git", "-C", str(work), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            task_id = "single-lane-finalize-079"
            parent_id = "single-lane-parent-079"
            backend_ref = f"cl/{task_id}.backend.json"
            foreground_ref = f"cl/{parent_id}.foreground.json"
            work_branch = "work/single-lane-finalize-079"
            result_ref = f"cases/{task_id}/implementation/branch_result.json"
            artifact_ref = f"cases/{task_id}/implementation/proof.txt"
            dispatch = {
                "dispatch_id": "dispatch-single-lane-finalize-079-g5",
                "generation": 5,
                "fence_token": "fence-single-lane-finalize-079-g5",
                "state": "RUNNING",
                "lease_expires_at": "2099-01-01T00:00:00+00:00",
                "wait_ref": None,
            }

            subprocess.check_call(["git", "-C", str(work), "switch", "-c", work_branch], stdout=subprocess.DEVNULL)
            proof = work / artifact_ref
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text("proof\n", encoding="utf-8")
            branch_result = {
                "v": 1,
                "task_id": task_id,
                "status": "PASS",
                "summary": "single lane implementation complete",
                "artifact_refs": [artifact_ref],
                "tests_run": [{"name": "synthetic", "result": "PASS"}],
                "dispatch": {
                    "dispatch_id": dispatch["dispatch_id"],
                    "generation": dispatch["generation"],
                    "fence_token": dispatch["fence_token"],
                },
                "completed_at": "2026-09-21T00:00:00+00:00",
            }
            result_path = work / result_ref
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(branch_result, indent=2) + "\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", artifact_ref, result_ref])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "branch result"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", work_branch], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            subprocess.check_call(["git", "-C", str(work), "switch", "main"], stdout=subprocess.DEVNULL)
            task = {
                "v": 1,
                "task_id": task_id,
                "parent_task_id": parent_id,
                "kind": "single_lane_semantic_branch",
                "lane_id": "lane-00",
                "work_branch": work_branch,
                "backend_cl": backend_ref,
                "foreground_cl": foreground_ref,
                "source_commit": source_commit,
                "branch_result_contract": {
                    "required_fields": ["summary", "artifact_refs", "tests_run", "dispatch", "completed_at"]
                },
            }
            backend = {
                "v": 1,
                "task_id": task_id,
                "scope": "backend_execution",
                "overall": "RUNNING",
                "dispatch": dispatch,
                "conditions": [
                    {"id": "semantic_work", "state": "WAIT", "detail": "pending", "evidence_ref": None},
                    {"id": "branch_output", "state": "WAIT", "detail": "pending", "evidence_ref": None},
                    {"id": "barrier_acceptance", "state": "WAIT", "detail": "pending", "evidence_ref": None},
                ],
                "scheduling": {"lane_id": "lane-00"},
            }
            foreground = {
                "v": 1,
                "task_id": parent_id,
                "scope": "foreground_supervision",
                "overall": "RUNNING",
                "conditions": [
                    {
                        "id": "implementation_worker",
                        "state": "WAIT",
                        "detail": "lane-00 implementation pending",
                        "evidence_ref": backend_ref,
                    },
                    {"id": "final_acceptance", "state": "WAIT", "detail": "pending", "evidence_ref": None},
                ],
            }

            for rel, value in (
                (f"tasks/{task_id}.json", task),
                (backend_ref, backend),
                (foreground_ref, foreground),
            ):
                p = work / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "tasks", "cl"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed single lane task"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            store = WakeStore(base / "spool", repo_root=work)
            subprocess.check_call(["git", "-C", str(work), "fetch", "--quiet", "origin", "main"])
            canonical_sha = subprocess.check_output(
                ["git", "-C", str(work), "rev-parse", "FETCH_HEAD"],
                text=True,
            ).strip()
            finalized = _try_finalize_parallel_branch(
                store,
                task_id=task_id,
                backend_cl_rel=backend_ref,
                canonical_sha=canonical_sha,
                attempt=0,
            )
            self.assertIsNotNone(finalized)
            self.assertTrue(finalized["finalized"])
            self.assertFalse(finalized["already_finalized"])
            self.assertEqual(finalized["outcome"], "PASS")

            subprocess.check_call(["git", "-C", str(work), "fetch", "--quiet", "origin", "main"])
            main_sha = subprocess.check_output(
                ["git", "-C", str(work), "rev-parse", "FETCH_HEAD"],
                text=True,
            ).strip()
            accepted_backend = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"{main_sha}:{backend_ref}"],
                text=True,
            ))
            accepted_foreground = json.loads(subprocess.check_output(
                ["git", "-C", str(work), "show", f"{main_sha}:{foreground_ref}"],
                text=True,
            ))
            self.assertEqual(accepted_backend["dispatch"]["state"], "DONE")
            self.assertEqual(accepted_backend["overall"], "GREEN")
            worker_condition = next(
                item for item in accepted_foreground["conditions"]
                if item["id"] == "implementation_worker"
            )
            self.assertEqual(worker_condition["state"], "GREEN")
            self.assertIn("single lane implementation complete", worker_condition["detail"])


class PackagingContractTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1] / '.github/workflows/stage0-single-thread.yml').is_file(), 'Workflow payload deliberately omitted')
    def test_stage0_false_continuation_never_wakes_worker(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "stage0-single-thread.yml").read_text(encoding="utf-8")
        wake_at = workflow.index("- name: Wake managed Worker after durable result")
        tail = workflow[wake_at:]
        guard_at = tail.index("if (-not $action.worker_continuation)")
        emit_at = tail.index("emit_wake.py")
        self.assertLess(guard_at, emit_at)
        self.assertIn("terminal is Foreground-owned and no Worker result wake is emitted", tail)


if __name__ == "__main__":
    unittest.main()
