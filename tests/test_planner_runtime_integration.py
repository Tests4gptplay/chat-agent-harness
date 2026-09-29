#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from local_bridge.planner_runtime import (
    DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS,
    PlannerRuntimeError,
    _build_worker_child,
    _continue_worker_child,
    _emit_local_worker_rollover,
    _planner_helper_prompt,
    _canonical_break_glass_allowed,
    _planner_turn_prompt,
    _pending_helper_request,
    _stage_pending_helper_request,
    planner_final_delivery_status,
    planner_final_delivery_update,
    planner_runtime_tick,
)
from local_bridge.planner_janitor import execute_planner_cleanup
from local_bridge.planner_memory import cleanup_manifest_digest, make_planner_current
from local_bridge.task_cell_ledgers import (
    make_planner_turn_outcome,
    make_planner_turn_slot,
    planner_turn_memory_entry_header,
    planner_turn_memory_entry_path,
    planner_turn_outcome_path,
)
from local_bridge.task_cell_roles import complete_task_cell_role_prompt
from local_bridge.server import WakeStore


TASK = "planner-hybrid-control-082"


class PlannerShortTurnProtocolTests(unittest.TestCase):
    def test_default_semantic_output_timeout_is_eight_minutes(self):
        self.assertEqual(DEFAULT_SEMANTIC_OUTPUT_TIMEOUT_SECONDS, 8 * 60)

    def test_worker_child_requires_wait_admission_before_wait_result(self):
        base = {
            "kind": "DISPATCH_WORKER_CHILD",
            "child_task_id": "planner-child-001",
            "lane_id": "lane-00",
            "worker_project_key": "g-p-plannertest",
            "task_payload": {
                "goal": "bounded child",
                "kind": "semantic_payload_kind_must_not_override_runtime",
                "task_id": "semantic-payload-task-id",
                "owner_task_id": "semantic-payload-owner",
                "owner_control_epoch": 99,
                "backend_cl": "cl/semantic-payload.backend.json",
                "lane_id": "lane-99",
                "worker_project_key": "g-p-semantic-payload",
                "expected_result_ref": "semantic/payload/result.json",
                "result_contract": {"semantic": "payload"},
                "execution_allowed": False,
                "status": "BLOCKED",
            },
        }
        with self.assertRaises(PlannerRuntimeError) as ctx:
            _build_worker_child(
                parent_task_id=TASK,
                parent_control_epoch=1,
                decision_ref="evidence/planner/decision.json",
                action={
                    **base,
                    "wait": {"kind": "WAIT_RESULT", "selector": {"child_task_id": "planner-child-001"}},
                },
                project_id="git-agent-harness",
                repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
            )
        self.assertEqual(ctx.exception.code, "PLANNER_CHILD_WAIT_ADMISSION_REQUIRED")

        child = _build_worker_child(
            parent_task_id=TASK,
            parent_control_epoch=1,
            decision_ref="evidence/planner/decision.json",
            action={
                **base,
                "wait": {"kind": "WAIT_ADMISSION", "selector": {"child_task_id": "planner-child-001"}},
            },
            project_id="git-agent-harness",
            repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
        )
        expected_result_ref = child["wake"]["result_ref"]
        self.assertIn(
            f"evidence/{TASK}/roles/worker/planner-child-001/",
            expected_result_ref,
        )
        self.assertEqual(child["task"]["parent_task_id"], TASK)
        self.assertEqual(
            child["task"]["parent_planner_decision_ref"],
            "evidence/planner/decision.json",
        )
        self.assertEqual(child["dispatch"]["state"], "READY")
        self.assertEqual(child["task"]["task_id"], "planner-child-001")
        self.assertEqual(child["task"]["kind"], "planner_worker_child")
        self.assertEqual(child["task"]["owner_task_id"], TASK)
        self.assertEqual(child["task"]["owner_control_epoch"], 1)
        self.assertEqual(child["task"]["backend_cl"], "cl/planner-child-001.backend.json")
        self.assertEqual(child["task"]["lane_id"], "lane-00")
        self.assertEqual(child["task"]["worker_project_key"], "g-p-plannertest")
        self.assertTrue(child["task"]["execution_allowed"])
        self.assertEqual(child["task"]["status"], "READY")
        self.assertEqual(child["task"]["expected_result_ref"], expected_result_ref)
        self.assertEqual(
            child["task"]["result_contract"],
            {
                "v": 1,
                "task_id": "planner-child-001",
            },
        )
        self.assertEqual(child["wake"]["result_ref"], expected_result_ref)
        self.assertEqual(
            child["child_reply_ref"],
            "memory/worker/planner-child-001/reply.md",
        )
        self.assertEqual(child["task"]["child_reply_ref"], child["child_reply_ref"])
        self.assertEqual(child["wake"]["child_reply_ref"], child["child_reply_ref"])
        self.assertEqual(
            child["wake"]["worker_reply_entry_ref"],
            child["worker_reply_entry_ref"],
        )
        self.assertIn("PLANNER", child["child_reply_initial"])
        self.assertIn("DIRECTION", child["child_reply_initial"])

    def test_worker_child_continuation_reuses_child_and_refreshes_dispatch(self):
        first = _build_worker_child(
            parent_task_id=TASK,
            parent_control_epoch=1,
            decision_ref="evidence/planner/decision.json",
            action={
                "kind": "DISPATCH_WORKER_CHILD",
                "child_task_id": "planner-child-001",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-plannertest",
                "task_payload": {"goal": "bounded child"},
                "wait": {"kind": "WAIT_ADMISSION", "selector": {"child_task_id": "planner-child-001"}},
            },
            project_id="git-agent-harness",
            repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
        )
        terminal_cl = json.loads(json.dumps(first["cl"]))
        terminal_cl["overall"] = "DONE"
        terminal_cl["dispatch"]["state"] = "DONE"
        task = json.loads(json.dumps(first["task"]))

        def read_json(_store, _base, path):
            if path == first["task_ref"]:
                return task
            if path == first["backend_cl"]:
                return terminal_cl
            raise AssertionError(path)

        with patch("local_bridge.planner_runtime._read_json", side_effect=read_json):
            nxt = _continue_worker_child(
                object(),
                "base",
                parent_task_id=TASK,
                parent_control_epoch=1,
                child={
                    "child_task_id": first["child_task_id"],
                    "task_ref": first["task_ref"],
                    "backend_cl": first["backend_cl"],
                    "lane_id": first["lane_id"],
                    "worker_project_key": first["worker_project_key"],
                },
                direction_id="direction-2",
                project_id="git-agent-harness",
                repo="CAH_OWNER/CAH_OPERATIONAL_REPOSITORY",
            )

        self.assertEqual(nxt["child_task_id"], first["child_task_id"])
        self.assertEqual(nxt["child_reply_ref"], first["child_reply_ref"])
        self.assertEqual(nxt["dispatch"]["generation"], 2)
        self.assertNotEqual(nxt["dispatch"]["dispatch_id"], first["dispatch"]["dispatch_id"])
        self.assertNotEqual(nxt["wake"]["result_ref"], first["wake"]["result_ref"])
        self.assertEqual(nxt["task"]["worker_reply_entry_ref"], nxt["worker_reply_entry_ref"])

    def test_local_worker_rollover_requires_canonical_commit_and_local_lane(self):
        emitted = []

        class Store:
            def emit(self, wake):
                emitted.append(wake["wake_id"])
                return {"ok": True, "wake_id": wake["wake_id"], "duplicate": False}

        continuation = {
            "lane_id": "lane-00",
            "dispatch": {"generation": 2},
            "wake": {
                "v": 1,
                "wake_id": "wake-rollover-0001",
                "replace_conversation": True,
            },
        }

        with patch.dict(os.environ, {"CAH_LOCAL_WORKER_LANES": "lane-00"}, clear=False):
            missing_commit = _emit_local_worker_rollover(Store(), continuation, commit_sha="")
            delivered = _emit_local_worker_rollover(Store(), continuation, commit_sha="abc123")

        self.assertFalse(missing_commit["attempted"])
        self.assertEqual(emitted, ["wake-rollover-0001"])
        self.assertTrue(delivered["ok"])
        self.assertEqual(delivered["reason"], "local_emit")
        self.assertEqual(delivered["canonical_commit_sha"], "abc123")

    def test_local_worker_rollover_remote_lane_keeps_durable_transport(self):
        class Store:
            def emit(self, _wake):
                raise AssertionError("remote lane must not emit locally")

        continuation = {
            "lane_id": "lane-01",
            "dispatch": {"generation": 4},
            "wake": {
                "v": 1,
                "wake_id": "wake-rollover-remote",
                "replace_conversation": True,
            },
        }
        with patch.dict(os.environ, {"CAH_LOCAL_WORKER_LANES": "lane-00"}, clear=False):
            result = _emit_local_worker_rollover(Store(), continuation, commit_sha="def456")

        self.assertFalse(result["attempted"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "lane_not_local")
        self.assertEqual(result["fallback"], "canonical_worker_wake")

    def test_local_worker_rollover_emit_failure_preserves_fallback(self):
        class Store:
            def emit(self, _wake):
                raise OSError("spool unavailable")

        continuation = {
            "lane_id": "lane-00",
            "dispatch": {"generation": 3},
            "wake": {
                "v": 1,
                "wake_id": "wake-rollover-fail",
                "replace_conversation": True,
            },
        }
        with patch.dict(os.environ, {"CAH_LOCAL_WORKER_LANES": "lane-00"}, clear=False):
            result = _emit_local_worker_rollover(Store(), continuation, commit_sha="fed987")

        self.assertTrue(result["attempted"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "local_emit_failed")
        self.assertEqual(result["fallback"], "canonical_worker_wake")

    def test_helper_prompt_has_no_evidence_read(self):
        helper_prompt = _planner_helper_prompt(
            "Diagnose the bounded anomaly.",
            f"evidence/{TASK}/roles/helper/result.json",
        )
        self.assertIn("ACTIVE RECOVERY REQUIRED", helper_prompt)
        self.assertIn("PLANNER AUTHORITY", helper_prompt)
        self.assertIn("TWO MANDATORY MISSIONS", helper_prompt)
        self.assertIn("TWO-PHASE HELPER RESULT", helper_prompt)
        self.assertIn("OMIT turn_signal", helper_prompt)
        self.assertIn("Harness may route this payload while this Helper remains alive", helper_prompt)
        self.assertIn("ONLY AFTER the correct next semantic role has actually started", helper_prompt)
        self.assertIn("turn_signal=done", helper_prompt)
        self.assertIn("FINAL Git write", helper_prompt)
        self.assertIn("does NOT wait for Helper response-end", helper_prompt)
        self.assertIn("GAH_WAKE v=1 id=<planner-request-id> project=<task-cell-project-id>", helper_prompt)
        self.assertIn("GAH_WAKE v=1 id=<wake-id> project=<worker-project-id>", helper_prompt)
        self.assertIn("GAH_DISPATCH task_id=<child-task-id>", helper_prompt)
        self.assertNotIn("evidence_read", helper_prompt)
        self.assertNotIn("CAH_TOOL_CALL", helper_prompt)
        self.assertNotIn("CAH_TOOL_RESULT", helper_prompt)
        self.assertNotIn("browser.recover_input", helper_prompt)
        self.assertNotIn("TOOL_SYSTEM", helper_prompt)
        self.assertIn("Harness owns identity, routing, and Helper cleanup", helper_prompt)

    def test_planner_turn_prompt_uses_prebound_surfaces_and_outcome_last(self):
        prompt = _planner_turn_prompt(
            task_id=TASK,
            doorbell={"doorbell_id": "doorbell-1", "event_ids": []},
            control={"inbox": {"events": {}}},
            plan_ref=f"tasks/{TASK}.plan.json",
            memory_ref=f"memory/planner/{TASK}/memory.md",
            memory_entry_ref=f"state/planner_turns/{TASK}/doorbell-1/memory-entry.md",
            slots=[
                {
                    "slot_index": 1,
                    "slot_ref": f"state/planner_turns/{TASK}/doorbell-1/worker-slot-1.json",
                    "bound_child_task_id": None,
                    "bound_child_reply_ref": None,
                }
            ],
            outcome_ref=f"state/planner_turns/{TASK}/doorbell-1/outcome.json",
        )
        self.assertIn("Current events=", prompt)
        self.assertIn(f"memory/planner/{TASK}/plan_note.md", prompt)
        self.assertIn("response timeout, or a stalled Task Cell", prompt)
        self.assertIn("proactively use Plan Note", prompt)
        self.assertIn("use Plan Note early enough to avoid an oversized risky turn", prompt)
        self.assertIn("Stop at a useful frontier", prompt)
        self.assertIn("dispatch already-bounded work", prompt)
        self.assertIn("meaningful note delta or decision and the remaining frontier", prompt)
        self.assertIn("If changing Plan Note", prompt)
        self.assertIn("Leave unused slot files unchanged", prompt)
        self.assertIn("outcome.json LAST", prompt)
        self.assertIn("set turn_signal to exactly one of done, rework, complete, handoff", prompt)
        self.assertIn('"kind":"semantic_sync"', prompt)
        self.assertIn("Do not fill the Harness-owned internal outcome field", prompt)
        self.assertIn("Do not append the legacy one-word ending", prompt)
        self.assertNotIn("evidence_read", prompt)
        self.assertNotIn("worker_count", prompt)

    def test_pending_helper_request_stages_exactly_once(self):
        runtime = {
            "pending_role_requests": [{
                "role": "helper",
                "kind": "helper_result",
                "request_id": "planner-helper-request-0001",
                "challenge": "helper-challenge-0001",
                "prompt": "Diagnose one bounded ambiguity.",
                "artifact_ref": "evidence/planner-hybrid-control-082/roles/helper/result.json",
                "state": "REQUEST_PENDING",
                "decision_ref": "evidence/planner/decision-helper.json",
            }],
            "pending_role_outputs": [],
        }
        state = {"control_request": None}
        cell = {
            "task_id": TASK,
            "control_epoch": 1,
            "task_cell_project_key": "g-p-taskcell",
        }
        self.assertIsNotNone(_pending_helper_request(runtime))
        staged, changed = _stage_pending_helper_request(state, cell, runtime)
        self.assertTrue(changed)
        self.assertEqual(staged["pending_role_requests"][0]["state"], "REQUESTED")
        self.assertEqual(len(staged["pending_role_outputs"]), 1)
        self.assertEqual(state["control_request"]["role"], "helper")
        self.assertEqual(state["control_request"]["status"], "PENDING")

        # Replay uses the durable REQUESTED state and does not create a second
        # control request/output record.
        replay_state = {"control_request": {"status": "DONE"}}
        replay, changed_again = _stage_pending_helper_request(replay_state, cell, staged)
        self.assertFalse(changed_again)
        self.assertEqual(len(replay["pending_role_outputs"]), 1)

    def test_break_glass_comes_from_canonical_task_contract_shape(self):
        task = {
            "status": "WAIT_PLANNER_REPLAN_ROTATION_AMENDMENT",
            "architecture": {
                "foreground_bootstrap_exception": {
                    "mode": "BUILD_AND_REPAIR_BREAK_GLASS",
                    "applies_when": ["control plane is under construction"],
                }
            },
        }
        self.assertTrue(_canonical_break_glass_allowed(task))
        self.assertFalse(_canonical_break_glass_allowed({**task, "status": "DONE"}))
        self.assertFalse(_canonical_break_glass_allowed({
            "status": "RUNNING",
            "architecture": {"foreground_bootstrap_exception": {"mode": "REQUEST_BOOLEAN"}},
        }))


class PlannerWorkerAdmissionIntegrationTests(unittest.TestCase):
    def test_dispatch_accept_atomically_emits_parent_planner_admission_event(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
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
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

            for rel in ("cl", "tasks", "state/task_cells"):
                (work / rel).mkdir(parents=True, exist_ok=True)

            parent_task = "planner-parent-001"
            child_task = "planner-child-001"
            backend_rel = f"cl/{child_task}.backend.json"
            worker_ref = "pool-planner-worker-0001"
            decision_ref = "evidence/planner-parent-001/roles/planner/decisions/decision-001.json"

            child = {
                "v": 1,
                "task_id": child_task,
                "kind": "planner_child",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-plannertest",
                "backend_cl": backend_rel,
                "parent_task_id": parent_task,
                "parent_planner_decision_ref": decision_ref,
            }
            backend = {
                "v": 1,
                "cl_id": f"bg-{child_task}",
                "task_id": child_task,
                "scope": "backend_execution",
                "overall": "READY",
                "created_at": "2026-09-21T00:00:00Z",
                "updated_at": "2026-09-21T00:00:00Z",
                "dispatch": {
                    "dispatch_id": "dispatch-planner-child-0001",
                    "wake_id": "wake-planner-child-0001",
                    "generation": 1,
                    "fence_token": "fence-planner-child-0001",
                    "state": "READY",
                    "requested_at": "2026-09-21T00:00:00Z",
                    "delivered_at": None,
                    "acked_at": None,
                    "acked_by_worker_ref": None,
                    "lease_expires_at": None,
                    "continuation_ref": f"tasks/{child_task}.json",
                    "wait_ref": None,
                    "ack_source": None,
                },
                "conditions": [
                    {"id": "claimed", "state": "WAIT", "detail": None, "evidence_ref": None}
                ],
            }
            parent_cell = {
                "v": 1,
                "task_cell_id": parent_task,
                "task_id": parent_task,
                "task_cell_project_key": "g-p-taskcell",
                "control_epoch": 1,
                "status": "ACTIVE",
                "roles": {},
                "planner_control": {
                    "v": 1,
                    "feature": "HYBRID_ACTIVE",
                    "enabled": True,
                    "task_id": parent_task,
                    "control_epoch": 1,
                    "activity": "PARKED_WAIT_EVENT",
                    "authority": {
                        "control_epoch": 1,
                        "planner_generation": 1,
                        "planner_fence_token": "planner-fence-parent-g1",
                        "conversation_id": "planner-conversation-parent",
                        "request_id": "planner-request-parent",
                        "challenge": "planner-challenge-parent",
                    },
                    "wait": {
                        "kind": "WAIT_ADMISSION",
                        "selector": {"child_task_id": child_task},
                    },
                    "inbox": {"events": {}, "active_doorbell": None},
                    "successor": {"state": "NONE"},
                    "retired_planners": [],
                    "semantic_authority_closed": False,
                },
            }
            lanes = {
                "v": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "project_key": "g-p-plannertest",
                    "last_pool_takeover_id": worker_ref,
                }],
            }
            state = {
                "v": 1,
                "agent": "chatgpt",
                "last_pool_takeover_id": worker_ref,
            }

            (work / backend_rel).write_text(json.dumps(backend), encoding="utf-8")
            (work / "tasks" / f"{child_task}.json").write_text(json.dumps(child), encoding="utf-8")
            (work / "state" / "task_cells" / f"{parent_task}.json").write_text(
                json.dumps(parent_cell), encoding="utf-8"
            )
            (work / "state" / "lanes.json").write_text(json.dumps(lanes), encoding="utf-8")
            (work / "state" / "chatgpt.json").write_text(json.dumps(state), encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(work), "commit", "-m", "seed Planner admission"],
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(
                ["git", "-C", str(work), "push", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            store = WakeStore(base / "spool", repo_root=work)
            req = {
                "client_id": "client-planner-admission-001",
                "project_id": "git-agent-harness",
                "task_id": child_task,
                "backend_cl": backend_rel,
                "dispatch_id": "dispatch-planner-child-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-planner-child-0001",
                "worker_ref": worker_ref,
                "lane_id": "lane-00",
                "worker_project_key": "g-p-plannertest",
            }
            accepted = store.dispatch_accept(req)
            self.assertTrue(accepted["ok"])
            self.assertTrue(accepted["accepted"])
            self.assertTrue(str(accepted.get("planner_admission_event_id") or "").startswith("event-"))

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            parent_raw = subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:state/task_cells/{parent_task}.json"],
                text=True,
                encoding="utf-8",
            )
            got_parent = json.loads(parent_raw)
            events = got_parent["planner_control"]["inbox"]["events"]
            self.assertEqual(len(events), 1)
            event = next(iter(events.values()))
            self.assertEqual(event["kind"], "admission_changed")
            self.assertEqual(event["source_role"], "harness")
            self.assertEqual(event["source_identity"]["new_state"], "RUNNING")
            self.assertEqual(event["source_identity"]["child_task_id"], child_task)
            self.assertEqual(event["source_identity"]["dispatch_id"], req["dispatch_id"])
            self.assertEqual(event["source_identity"]["fence_token"], req["fence_token"])
            self.assertEqual(event["source_identity"]["worker_ref"], worker_ref)

            duplicate = store.dispatch_accept(req)
            self.assertTrue(duplicate["ok"])
            self.assertFalse(duplicate["accepted"])
            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            parent_raw_2 = subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:state/task_cells/{parent_task}.json"],
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(
                len(json.loads(parent_raw_2)["planner_control"]["inbox"]["events"]),
                1,
            )




class PlannerTurnReductionIntegrationTests(unittest.TestCase):
    def test_tick_reduces_two_worker_directions_into_fixed_five_round(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
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
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

            task_id = "planner-fixed-five-001"
            epoch = 1
            generation = 1
            fence = "planner-fence-fixed-five-001"
            doorbell_id = "doorbell-fixed-five-001"
            task_ref = f"tasks/{task_id}.json"
            plan_ref = f"tasks/{task_id}.plan.json"
            cell_ref = f"state/task_cells/{task_id}.json"

            task = {
                "v": 1,
                "task_id": task_id,
                "task_contract_revision": 1,
                "goal": "materialize two bounded Worker directions",
            }
            plan = {"v": 1, "task_id": task_id, "plan_revision": 1, "stages": ["dispatch"]}
            lanes = {
                "v": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "project_key": "g-p-plannertest",
                    "enabled": True,
                    "task_pools": {},
                }],
            }
            state = {"v": 1, "agent": "chatgpt", "control_request": None}

            for rel, value in {
                task_ref: task,
                plan_ref: plan,
                "state/lanes.json": lanes,
                "state/chatgpt.json": state,
            }.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(work), "commit", "-m", "seed fixed-five semantic inputs"],
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(
                ["git", "-C", str(work), "push", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            task_sha = subprocess.check_output(
                ["git", "-C", str(work), "rev-parse", f"HEAD:{task_ref}"],
                text=True,
                encoding="utf-8",
            ).strip()
            plan_sha = subprocess.check_output(
                ["git", "-C", str(work), "rev-parse", f"HEAD:{plan_ref}"],
                text=True,
                encoding="utf-8",
            ).strip()

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
                written_at="2026-09-26T00:00:00+00:00",
            )
            memory_ref = f"memory/planner/{task_id}/memory.md"
            memory_entry_ref = planner_turn_memory_entry_path(task_id, doorbell_id)
            outcome_ref = planner_turn_outcome_path(task_id, doorbell_id)
            slots = [
                make_planner_turn_slot(
                    task_id=task_id,
                    control_epoch=epoch,
                    doorbell_id=doorbell_id,
                    planner_generation=generation,
                    planner_fence_token=fence,
                    slot_index=i,
                )
                for i in range(1, 3)
            ]
            for i, slot in enumerate(slots):
                slot["lane_id"] = f"lane-{i:02d}"
            lanes["lanes"].append({**lanes["lanes"][0], "lane_id": "lane-01", "project_key": "g-p-plannertest1"})
            (work / "state/lanes.json").write_text(json.dumps(lanes), encoding="utf-8")
            for i in (0, 1):
                slots[i]["entry_type"] = "DIRECTION"
                slots[i]["semantic"] = {"goal": f"bounded child {i + 1}"}

            outcome = make_planner_turn_outcome(
                task_id=task_id,
                control_epoch=epoch,
                doorbell_id=doorbell_id,
                planner_generation=generation,
                planner_fence_token=fence,
            )
            outcome["outcome"] = "CONTINUE"
            outcome["semantic"] = {}

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
                        "conversation_id": "planner-conversation-fixed-five",
                        "request_id": "planner-request-fixed-five",
                        "challenge": "planner-challenge-fixed-five",
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
                        "conversation_id": "planner-conversation-fixed-five",
                        "request_id": "planner-request-fixed-five",
                        "challenge": "planner-challenge-fixed-five",
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
                            "request_id": "planner-request-fixed-five",
                            "status": "WAITING",
                        },
                        "active_turn": {
                            "doorbell_id": doorbell_id,
                            "memory_entry_ref": memory_entry_ref,
                            "outcome_ref": outcome_ref,
                            "slots": [
                                {
                                    "lane_id": slot["lane_id"],
                                    "slot_index": slot["slot_index"],
                                    "slot_ref": slot["slot_ref"],
                                    "bound_child_task_id": None,
                                    "bound_child_reply_ref": None,
                                }
                                for slot in slots
                            ],
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
                ) + "Dispatch two independent bounded children.\n",
                outcome_ref: outcome,
            }
            for slot in slots:
                files[slot["slot_ref"]] = slot

            for rel, value in files.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(value, dict):
                    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                else:
                    target.write_text(str(value), encoding="utf-8")

            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(work), "commit", "-m", "commit Planner fixed-five turn"],
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(
                ["git", "-C", str(work), "push", "origin", "HEAD:main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            store = WakeStore(base / "spool", repo_root=work)
            result = planner_runtime_tick(
                store,
                {"client_id": "client-fixed-five", "project_id": "git-agent-harness"},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "planner_turn_reduced")
            self.assertEqual(result["dispatched_workers"], 2)
            self.assertEqual(result["settled_slots"], 0)

            subprocess.check_call(
                ["git", "-C", str(work), "fetch", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            reduced_cell = json.loads(
                subprocess.check_output(
                    ["git", "-C", str(work), "show", f"FETCH_HEAD:{cell_ref}"],
                    text=True,
                    encoding="utf-8",
                )
            )
            round_state = reduced_cell["planner_control"]["runtime"]["worker_round"]
            self.assertIsNone(round_state)
            self.assertEqual(
                len(reduced_cell["planner_control"]["runtime"]["owned_children"]),
                2,
            )
            memory = subprocess.check_output(
                ["git", "-C", str(work), "show", f"FETCH_HEAD:{memory_ref}"],
                text=True,
                encoding="utf-8",
            )
            self.assertIn("Dispatch two independent bounded children.", memory)


class PlannerTerminalRestoreIntegrationTests(unittest.TestCase):
    @staticmethod
    def _read_remote_json(work: Path, rel: str) -> dict:
        subprocess.check_call(
            ["git", "-C", str(work), "fetch", "origin", "main"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raw = subprocess.check_output(
            ["git", "-C", str(work), "show", f"FETCH_HEAD:{rel}"],
            text=True,
            encoding="utf-8",
        )
        return json.loads(raw)

    def test_final_delivery_restores_hot_state_and_cleanup_remains_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            work = base / "work"
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
            subprocess.check_call(["git", "-C", str(work), "config", "user.name", "gah-test"])
            subprocess.check_call(["git", "-C", str(work), "config", "user.email", "gah-test@example.invalid"])

            task_id = "planner-terminal-restore-001"
            epoch = 1
            project_key = "g-p-taskcellrestore"
            lane_project_key = "g-p-workerrestore"
            api = {"client_id": "client-restore-001", "project_id": "git-agent-harness"}
            cell_ref = f"state/task_cells/{task_id}.json"
            manifest_ref = f"evidence/{task_id}/cleanup/manual-cleanup.json"
            final_result_ref = f"evidence/{task_id}/final-result.json"
            event_id = "terminal-planner-restore-0001"

            store = WakeStore(base / "spool", repo_root=work)
            runtime_root = store.runtime / "tasks" / task_id
            runtime_root.mkdir(parents=True, exist_ok=True)
            disposable = runtime_root / "ephemeral.txt"
            disposable.write_text("delete only on explicit cleanup", encoding="utf-8")

            candidate = {
                "candidate_id": "runtime-ephemeral-001",
                "action": "DELETE",
                "resource_kind": "MANAGED_PATH",
                "path_or_exact_resource_identity": str(disposable.resolve()),
                "task_owner": task_id,
                "retention_class": "EPHEMERAL",
                "storage_policy_class": "SCRATCH",
                "managed_root_id_or_task_cell_project_key": "task_runtime",
                "reason": "synthetic explicit cleanup",
                "dependency_refs": [],
                "expected_blob_sha_or_identity_digest": "",
                "delete_after_gate": None,
            }
            manifest = {
                "schema_version": 1,
                "role": "planner",
                "task_id": task_id,
                "control_epoch": epoch,
                "cleanup_generation": 1,
                "manifest_id": "cleanup-explicit-test-0001",
                "manifest_digest": "",
                "terminal_decision_ref": f"evidence/{task_id}/decision.json",
                "terminal_decision_blob_sha": "decision-sha",
                "final_planner_generation": 1,
                "final_planner_fence_token": "planner-fence-g1",
                "final_terminal_memory_ref": f"memory/planner/{task_id}/current.json",
                "final_terminal_memory_blob_sha": "terminal-memory-sha",
                "delete_candidates": [candidate],
                "preserve_candidates": [],
                "protected_refs_snapshot": [final_result_ref],
                "rollback_window_refs": [],
                "dependency_checks": {},
                "created_at": "2026-09-21T00:00:00+00:00",
                "cleanup_status": "PLANNED",
            }
            manifest["manifest_digest"] = cleanup_manifest_digest(manifest)

            roles = {
                "planner": {
                    "role": "planner",
                    "status": "ACTIVE",
                    "request_id": "planner-request-0001",
                    "challenge": "planner-challenge-0001",
                    "conversation_id": "planner-conversation-0001",
                },
                "helper": {
                    "role": "helper",
                    "status": "BOUND",
                    "request_id": "helper-request-0001",
                    "challenge": "helper-challenge-0001",
                    "conversation_id": "helper-conversation-0001",
                },
            }
            control = {
                "v": 1,
                "feature": "HYBRID_ACTIVE",
                "enabled": True,
                "task_id": task_id,
                "control_epoch": epoch,
                "activity": "DONE",
                "semantic_authority_closed": True,
                "authority": {
                    "control_epoch": epoch,
                    "planner_generation": 1,
                    "planner_fence_token": "planner-fence-g1",
                    "conversation_id": roles["planner"]["conversation_id"],
                    "request_id": roles["planner"]["request_id"],
                    "challenge": roles["planner"]["challenge"],
                    "project_key": project_key,
                    "semantic_authority": False,
                },
                "successor": {"state": "NONE"},
                "wait": None,
                "inbox": {"events": {}, "active_doorbell": None},
                "terminal_refs": {
                    "terminal_decision_ref": f"evidence/{task_id}/decision.json",
                    "final_result_ref": final_result_ref,
                },
                "runtime": {
                    "final_delivery": {
                        "v": 1,
                        "kind": "planner_final_delivery",
                        "event_id": event_id,
                        "task_id": task_id,
                        "control_epoch": epoch,
                        "decision_ref": f"evidence/{task_id}/decision.json",
                        "result_ref": final_result_ref,
                        "result_blob_sha": "final-result-sha",
                        "state": "PENDING",
                        "claim_id": None,
                        "claimed_at": None,
                        "delivered_at": None,
                        "consumed_at": None,
                        "published_at": "2026-09-21T00:00:00+00:00",
                        "terminal_status": "PASS",
                    },
                },
            }
            cell = {
                "v": 1,
                "task_cell_id": task_id,
                "task_id": task_id,
                "task_cell_project_key": project_key,
                "control_epoch": epoch,
                "status": "DONE",
                "roles": roles,
                "planner_control": control,
            }
            state = {"v": 1, "agent": "chatgpt", "control_request": {"status": "DONE"},
                     "active_task": task_id, "phase": "EXECUTING",
                     "next_action": f"Process pending handoff for {task_id}.",
                     "next_reads": [cell_ref], "writeback_reason": "old Helper wake",
                     "paused_work": {"task_id": "unrelated-paused-task"}}
            lanes = {
                "v": 1,
                "lanes": [{
                    "lane_id": "lane-00",
                    "project_key": lane_project_key,
                    "enabled": True,
                    "task_pools": {
                        f"{task_id}::{epoch}": {
                            "owner_task_id": task_id,
                            "owner_control_epoch": epoch,
                            "last_pool_takeover_id": "pool-current",
                            "worker_rollover_request": {
                                "handoff_id": "pool-current",
                                "reason": "context_compacted",
                            },
                        },
                    },
                }],
            }

            for rel, value in {
                cell_ref: cell,
                "state/chatgpt.json": state,
                "state/lanes.json": lanes,
                manifest_ref: manifest,
                final_result_ref: {"status": "PASS"},
            }.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            subprocess.check_call(["git", "-C", str(work), "add", "."])
            subprocess.check_call(
                ["git", "-C", str(work), "commit", "-m", "seed terminal restore"],
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
            subprocess.check_call(
                ["git", "-C", str(work), "push", "origin", "main"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            claimed = planner_final_delivery_status(store, api)
            self.assertTrue(claimed["ok"])
            event = claimed["event"]
            self.assertEqual(event["state"], "CLAIMED")

            delivered = planner_final_delivery_update(
                store,
                {
                    **api,
                    "task_cell_ref": cell_ref,
                    "event_id": event_id,
                    "claim_id": event["claim_id"],
                },
                operation="delivered",
            )
            self.assertTrue(delivered["ok"])
            self.assertTrue(disposable.exists())

            denied = planner_final_delivery_update(store, {**api, "task_cell_ref": cell_ref, "event_id": event_id, "claim_id": event["claim_id"]}, operation="consumed")
            self.assertEqual(denied["error"], "PLANNER_CHAT_CLEANUP_REQUIRED")
            cleaned = planner_final_delivery_update(store, {**api, "task_cell_ref": cell_ref, "event_id": event_id, "claim_id": event["claim_id"], "cleanup_receipt": {"all_deleted": True, "deleted": ["test-fixture-owned-chat"]}}, operation="cleaned")
            self.assertTrue(cleaned["ok"])
            consumed = planner_final_delivery_update(
                store,
                {
                    **api,
                    "task_cell_ref": cell_ref,
                    "event_id": event_id,
                    "claim_id": event["claim_id"],
                },
                operation="consumed",
            )
            self.assertTrue(consumed["ok"])
            self.assertTrue(disposable.exists(), "Terminal State Restore must not delete task history/runtime residue")

            terminal = self._read_remote_json(work, cell_ref)
            self.assertEqual(terminal["status"], "DONE")
            self.assertEqual(terminal["roles"], {})
            self.assertFalse(terminal["planner_control"]["enabled"])
            self.assertTrue(terminal["planner_control"]["semantic_authority_closed"])
            self.assertEqual(terminal["planner_control"]["activity"], "DONE")
            self.assertNotIn("runtime", terminal["planner_control"])
            self.assertTrue(terminal["planner_control"]["authority"]["authority_revoked"])

            frontier = self._read_remote_json(work, "state/chatgpt.json")
            self.assertEqual(frontier["phase"], "IDLE")
            self.assertIsNone(frontier["active_task"])
            self.assertEqual(frontier["next_action"], "Await the next current user task.")
            self.assertEqual(frontier["next_reads"], [])
            self.assertIn("Terminal reset completed", frontier["writeback_reason"])
            self.assertEqual(frontier["paused_work"], {"task_id": "unrelated-paused-task"})

            lanes_after = self._read_remote_json(work, "state/lanes.json")
            self.assertNotIn(f"{task_id}::{epoch}", lanes_after["lanes"][0]["task_pools"])

            janitor = execute_planner_cleanup(
                store,
                {
                    **api,
                    "task_id": task_id,
                    "control_epoch": epoch,
                    "manifest_ref": manifest_ref,
                    "started_manifest_digest": manifest["manifest_digest"],
                    "protected_conversation_ids": [],
                    "dry_run": False,
                },
            )
            self.assertTrue(janitor["ok"])
            self.assertEqual(janitor["status"], "CLEANUP_COMPLETE")
            self.assertFalse(disposable.exists())

            quiet = planner_runtime_tick(store, api)
            self.assertEqual(quiet.get("idle"), "no_planner_maintenance_action")


if __name__ == "__main__":
    unittest.main()
