import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness.semantic_finalize import exit_code_for_outcome, finalize


REPO_ROOT = Path(__file__).resolve().parents[1]


class SemanticFinalizeTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        *,
        status: str = "PASS",
        current_dispatch: dict | None = None,
        analysis_dispatch: dict | None = None,
        active_task: str = "t-final",
        active_dispatch_ref: str = "cl/t-final.backend.json",
        capture_status: str = "PASS",
        capture_task_id: str = "t-final",
        capture_action_id: str = "t-final-a001",
        capture_raw: str | None = None,
        omit_capture: bool = False,
    ) -> Path:
        for d in ("tasks", "cl", "results", "evidence/t-final", "state", "actions/stage0"):
            (root / d).mkdir(parents=True, exist_ok=True)

        task = {
            "v": 1,
            "task_id": "t-final",
            "kind": "zhihu_review_one_shot",
            "deterministic_prelude": {
                "action_path": "actions/stage0/t-final.json",
                "result_path": "results/t-final.json",
                "artifact_path": "evidence/t-final/review_packet.json",
            },
            "execution_contract": {
                "foreground_cl": "cl/t-final.foreground.json",
                "backend_cl": "cl/t-final.backend.json",
                "capture_result": "results/t-final.json",
                "final_analysis": "results/t-final.analysis.json",
            },
        }
        current_dispatch = current_dispatch or {
            "dispatch_id": "dispatch-final-0001",
            "generation": 1,
            "fence_token": "fence-final-0001",
            "state": "RUNNING",
        }
        analysis_dispatch = analysis_dispatch or {
            "dispatch_id": current_dispatch["dispatch_id"],
            "generation": current_dispatch["generation"],
            "fence_token": current_dispatch["fence_token"],
        }
        bg = {
            "v": 1,
            "cl_id": "bg-t-final",
            "task_id": "t-final",
            "scope": "backend_execution",
            "overall": "RUNNING",
            "created_at": "x",
            "updated_at": "x",
            "result_ref": None,
            "error": None,
            "dispatch": copy.deepcopy(current_dispatch),
            "conditions": [
                {"id": "claimed", "label": "c", "state": "GREEN"},
                {"id": "executor", "label": "e", "state": "GREEN"},
                {"id": "durable_result", "label": "r", "state": "GREEN"},
                {"id": "verification", "label": "v", "state": "WAIT"},
                {"id": "terminal", "label": "t", "state": "WAIT"},
            ],
        }
        fg = {
            "v": 1,
            "cl_id": "fg-t-final",
            "task_id": "t-final",
            "scope": "foreground_supervision",
            "overall": "RUNNING",
            "created_at": "x",
            "updated_at": "x",
            "result_ref": None,
            "error": None,
            "visual": "[● ○ ○ ○ ○ ○]",
            "supervisor_guard": {"state": "HELD", "detail": None},
            "conditions": [
                {"id": "harness_claimed", "label": "h", "state": "GREEN"},
                {"id": "backend_execution", "label": "b", "state": "GREEN"},
                {"id": "durable_result", "label": "r", "state": "GREEN"},
                {"id": "verification", "label": "v", "state": "WAIT"},
                {"id": "final_acceptance", "label": "f", "state": "WAIT"},
            ],
        }
        state = {
            "v": 1,
            "agent": "chatgpt",
            "updated": "2026-09-18",
            "phase": "RUNNING",
            "current": [],
            "verified": [],
            "active_task": active_task,
            "active_action": "actions/stage0/t-final.json",
            "pending_wake_id": "wake-final",
            "active_dispatch_ref": active_dispatch_ref,
            "next_reads": [],
            "next_action": "finish",
        }
        action = {
            "v": 1,
            "action_id": "t-final-a001",
            "task_id": "t-final",
            "executor": "camoufox_zhihu_review",
            "operation": "capture_review_packet",
        }
        capture = {
            "v": 1,
            "result_id": "result-t-final-a001",
            "action_id": capture_action_id,
            "task_id": capture_task_id,
            "status": capture_status,
            "artifacts": ["evidence/t-final/review_packet.json"],
        }

        analysis = {
            "v": 1,
            "task_id": "t-final",
            "status": status,
            "dispatch": copy.deepcopy(analysis_dispatch),
            "summary": f"{status.lower()} outcome",
        }
        if status == "PASS":
            analysis.update(
                {
                    "participants": [
                        "DeepSeek V4.1 Flash",
                        "MiniMax M3",
                        "Xiaomi MiMo V2.5",
                        "Doubao Seed 2.1 Pro",
                        "GLM 5.3 Flash",
                        "Kimi K3",
                        "Qwen 3.8 Max",
                    ],
                    "groups": {
                        "newcomer": ["MiniMax", "Xiaomi", "Doubao"],
                        "strong": ["DeepSeek", "GLM", "Kimi", "Qwen"],
                    },
                    "tasks": ["camera", "winter house"],
                    "evaluation_dimensions": ["creativity", "quality", "detail", "aesthetics"],
                    "quality_findings": [
                        {
                            "claim": "a",
                            "provenance": "author_claim",
                            "evidence_refs": ["evidence/t-final/review_packet.json"],
                        },
                        {
                            "claim": "b",
                            "provenance": "evidence_fact",
                            "evidence_refs": ["results/t-final.json"],
                        },
                        {
                            "claim": "c",
                            "provenance": "worker_inference",
                            "evidence_refs": ["evidence/t-final/review_packet.json"],
                        },
                    ],
                    "timing_findings": [
                        {"model": "DeepSeek", "seconds": 1212},
                        {"model": "GLM", "seconds": 3940},
                    ],
                    "deepseek_basis": {
                        "strengths": ["quality", "speed", "cost"],
                        "tradeoffs": ["creativity"],
                    },
                    "methodology_limitations": [
                        "one-shot",
                        "AI judges",
                        "no independent visual rescoring",
                    ],
                    "evidence_refs": [
                        "results/t-final.json",
                        "evidence/t-final/review_packet.json",
                    ],
                }
            )

        (root / "tasks/t-final.json").write_text(json.dumps(task), encoding="utf-8")
        (root / "cl/t-final.backend.json").write_text(json.dumps(bg), encoding="utf-8")
        (root / "cl/t-final.foreground.json").write_text(json.dumps(fg), encoding="utf-8")
        (root / "state/chatgpt.json").write_text(json.dumps(state), encoding="utf-8")
        (root / "actions/stage0/t-final.json").write_text(json.dumps(action), encoding="utf-8")
        if not omit_capture:
            if capture_raw is None:
                capture_raw = json.dumps(capture)
            (root / "results/t-final.json").write_text(capture_raw, encoding="utf-8")
        (root / "evidence/t-final/review_packet.json").write_text(
            json.dumps({"ok": True}), encoding="utf-8"
        )
        ap = root / "results/t-final.analysis.json"
        ap.write_text(json.dumps(analysis), encoding="utf-8")
        return ap

    def _canonical_bytes(self, root: Path) -> tuple[bytes, bytes, bytes]:
        return tuple(
            (root / rel).read_bytes()
            for rel in (
                "cl/t-final.backend.json",
                "cl/t-final.foreground.json",
                "state/chatgpt.json",
            )
        )

    def test_pass_finalizes_terminal_state_with_valid_capture_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ap = self._fixture(root)
            out = finalize(ap, root=root)
            self.assertEqual(out["outcome"], "PASS")
            self.assertTrue(out["projected"])
            bg = json.loads((root / "cl/t-final.backend.json").read_text())
            fg = json.loads((root / "cl/t-final.foreground.json").read_text())
            state = json.loads((root / "state/chatgpt.json").read_text())
            self.assertEqual(bg["dispatch"]["state"], "DONE")
            self.assertEqual(bg["overall"], "GREEN")
            self.assertEqual(fg["supervisor_guard"]["state"], "RELEASED")
            self.assertEqual(fg["overall"], "GREEN")
            self.assertEqual(state["phase"], "DONE")
            self.assertEqual(state["last_result"], "results/t-final.analysis.json")

    def test_stale_same_task_dispatch_is_byte_equivalent_for_every_business_status(self):
        for status in ("PASS", "ERROR", "BLOCKED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                current = {
                    "dispatch_id": "dispatch-current",
                    "generation": 2,
                    "fence_token": "fence-current",
                    "state": "RUNNING",
                }
                stale = {
                    "dispatch_id": "dispatch-old",
                    "generation": 1,
                    "fence_token": "fence-old",
                }
                ap = self._fixture(
                    root,
                    status=status,
                    current_dispatch=current,
                    analysis_dispatch=stale,
                )
                before = self._canonical_bytes(root)
                out = finalize(ap, root=root)
                after = self._canonical_bytes(root)
                self.assertEqual(out["outcome"], "REJECTED")
                self.assertFalse(out["projected"])
                self.assertEqual(before, after)

    def test_valid_error_and_blocked_are_business_outcomes_and_project(self):
        for status in ("ERROR", "BLOCKED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                ap = self._fixture(root, status=status)
                out = finalize(ap, root=root)
                self.assertEqual(out["outcome"], status)
                self.assertTrue(out["projected"])
                self.assertEqual(exit_code_for_outcome(status), 0)
                bg = json.loads((root / "cl/t-final.backend.json").read_text())
                fg = json.loads((root / "cl/t-final.foreground.json").read_text())
                state = json.loads((root / "state/chatgpt.json").read_text())
                self.assertEqual(bg["overall"], status)
                self.assertEqual(bg["dispatch"]["state"], status)
                self.assertEqual(bg["result_ref"], "results/t-final.analysis.json")
                self.assertEqual(bg["error"]["kind"], "semantic_business_outcome")
                self.assertEqual(fg["overall"], status)
                self.assertEqual(fg["result_ref"], "results/t-final.analysis.json")
                self.assertEqual(state["last_result"], "results/t-final.analysis.json")
                self.assertEqual(
                    state["phase"], "BLOCKED" if status == "BLOCKED" else "DONE"
                )

    def test_invalid_pass_prerequisites_reject_without_canonical_mutation(self):
        cases = [
            ("capture ERROR", {"capture_status": "ERROR"}),
            ("capture BLOCKED", {"capture_status": "BLOCKED"}),
            ("malformed capture", {"capture_raw": "{not-json"}),
            ("wrong capture task", {"capture_task_id": "wrong-task"}),
            ("wrong capture action", {"capture_action_id": "wrong-action"}),
            ("missing capture", {"omit_capture": True}),
        ]
        for label, kwargs in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                ap = self._fixture(root, **kwargs)
                before = self._canonical_bytes(root)
                out = finalize(ap, root=root)
                after = self._canonical_bytes(root)
                self.assertEqual(out["outcome"], "REJECTED")
                self.assertEqual(out["rejection_kind"], "invalid_semantic_artifact")
                self.assertEqual(exit_code_for_outcome(out["outcome"]), 2)
                self.assertEqual(before, after)

    def test_stale_active_task_rejects_without_canonical_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ap = self._fixture(
                root,
                active_task="t-new",
                active_dispatch_ref="cl/t-new.backend.json",
            )
            before = self._canonical_bytes(root)
            out = finalize(ap, root=root)
            after = self._canonical_bytes(root)
            self.assertEqual(out["outcome"], "REJECTED")
            self.assertEqual(out["rejection_kind"], "stale_canonical_activity")
            self.assertEqual(before, after)

    def test_business_outcome_projection_is_idempotent(self):
        for status in ("PASS", "ERROR", "BLOCKED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                ap = self._fixture(root, status=status)
                first = finalize(ap, root=root)
                self.assertEqual(first["outcome"], status)
                after_first = self._canonical_bytes(root)
                second = finalize(ap, root=root)
                after_second = self._canonical_bytes(root)
                self.assertEqual(second["outcome"], status)
                self.assertFalse(second["projected"])
                self.assertTrue(second["idempotent"])
                self.assertEqual(after_first, after_second)

    def test_rejection_exit_code_is_nonzero_but_business_outcomes_are_zero(self):
        for status in ("PASS", "ERROR", "BLOCKED"):
            self.assertEqual(exit_code_for_outcome(status), 0)
        self.assertNotEqual(exit_code_for_outcome("REJECTED"), 0)

    def test_workflow_publishes_business_projection_before_reporting_red_outcome(self):
        workflow = (REPO_ROOT / ".github/workflows/semantic-finalize.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('harness/request_drain.py --kind semantic_finalize', workflow)
        self.assertNotIn('git diff-tree', workflow)
        self.assertNotIn('git rebase', workflow)
        # The real temporary-Git drain suite additionally proves that ERROR is
        # committed together with its receipt before main() reports nonzero.
        driver = (REPO_ROOT / 'harness/request_drain.py').read_text(encoding='utf-8')
        self.assertIn('def transaction(', driver)
        self.assertIn("r.get('outcome') in {'ERROR', 'BLOCKED'}", driver)



if __name__ == "__main__":
    unittest.main()
