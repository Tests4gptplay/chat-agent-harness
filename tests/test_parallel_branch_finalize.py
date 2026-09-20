import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.parallel_branch_finalize import (
    AGGREGATE_SOURCE,
    ParallelBranchFinalizeError,
    apply_branch_result,
    build_evidence_identity,
    run_git,
)


def task(lane=0):
    return {
        "v": 1,
        "task_id": f"parent-lane{lane:02d}",
        "parent_task_id": "parent",
        "kind": "parallel_semantic_branch",
        "lane_id": f"lane-{lane:02d}",
        "work_branch": f"showcase/test-{lane}",
        "backend_cl": f"cl/lane{lane}.backend.json",
        "foreground_cl": "cl/parent.foreground.json",
        "output_root": f"cases/test/work/lane-{lane:02d}",
        "branch_result_contract": {
            "status": "PASS",
            "required_fields": [
                "summary",
                "artifact_refs",
                "dispatch",
                "completed_at",
            ],
        },
    }


def backend(lane=0):
    return {
        "v": 1,
        "cl_id": f"bg-{lane}",
        "task_id": f"parent-lane{lane:02d}",
        "scope": "backend_execution",
        "overall": "RUNNING",
        "created_at": "x",
        "updated_at": "x",
        "result_ref": None,
        "wait_ref": None,
        "dispatch": {
            "dispatch_id": f"dispatch-{lane:04d}",
            "wake_id": f"wake-{lane:04d}",
            "generation": 4,
            "fence_token": f"fence-{lane:04d}",
            "state": "RUNNING",
            "requested_at": "x",
            "delivered_at": "x",
            "acked_at": "x",
            "acked_by_worker_ref": "pool-1",
            "lease_expires_at": "later",
            "continuation_ref": None,
            "wait_ref": None,
            "ack_source": "extension_response_start",
        },
        "scheduling": {"completed_at": None},
        "error": None,
        "conditions": [
            {"id": "semantic_work", "label": "semantic", "state": "RUNNING", "detail": None, "evidence_ref": None},
            {"id": "deterministic_execution", "label": "det", "state": "GREEN", "detail": None, "evidence_ref": None},
            {"id": "branch_output", "label": "branch", "state": "WAIT", "detail": None, "evidence_ref": None},
            {"id": "barrier_acceptance", "label": "barrier", "state": "WAIT", "detail": None, "evidence_ref": None},
        ],
    }


def foreground(lane0="RUNNING", lane1="RUNNING"):
    return {
        "v": 1,
        "cl_id": "fg-parent",
        "task_id": "parent",
        "scope": "foreground_supervision",
        "overall": "RUNNING",
        "created_at": "x",
        "updated_at": "x",
        "error": None,
        "conditions": [
            {"id": "lane00", "label": "a", "state": lane0, "detail": None, "evidence_ref": None},
            {"id": "lane01", "label": "b", "state": lane1, "detail": None, "evidence_ref": None},
            {"id": "barrier", "label": "barrier", "state": "WAIT", "detail": None, "evidence_ref": None},
            {"id": "reducer", "label": "reducer", "state": "WAIT", "detail": None, "evidence_ref": None},
            {"id": "final_acceptance", "label": "final", "state": "WAIT", "detail": None, "evidence_ref": None},
        ],
    }


def result(status="PASS", lane=0):
    value = {
        "v": 1,
        "task_id": f"parent-lane{lane:02d}",
        "status": status,
        "summary": f"branch {lane} {status}",
        "dispatch": {
            "dispatch_id": f"dispatch-{lane:04d}",
            "generation": 4,
            "fence_token": f"fence-{lane:04d}",
        },
        "artifact_refs": [f"cases/test/work/lane-{lane:02d}/file.txt"],
        "completed_at": "2026-09-19T00:00:00Z",
    }
    if status == "BLOCKED":
        value["blocker"] = {"kind": "NEED_SOMETHING", "terminal_until_external_change": True}
    return value


def evidence_identity(lane=0, commit="a" * 40, fingerprint="1" * 64, artifact_blob="2" * 40):
    result_ref = f"cases/test/work/lane-{lane:02d}/branch_result.json"
    artifact_ref = f"cases/test/work/lane-{lane:02d}/file.txt"
    return {
        "v": 1,
        "repository": "example-owner/cah-private",
        "work_branch": f"showcase/test-{lane}",
        "validated_commit": commit,
        "content_fingerprint": fingerprint,
        "result": {
            "path": result_ref,
            "blob_oid": "3" * 40,
            "immutable_ref": f"github://example-owner/cah-private/{commit}/{result_ref}",
        },
        "artifacts": [
            {
                "path": artifact_ref,
                "blob_oid": artifact_blob,
                "immutable_ref": f"github://example-owner/cah-private/{commit}/{artifact_ref}",
            }
        ],
    }


def cond(fg, cond_id):
    return next(x for x in fg["conditions"] if x["id"] == cond_id)


def apply_lane(fg, lane, status, *, identity=None):
    bg = backend(lane)
    out = apply_branch_result(
        task=task(lane),
        backend=bg,
        foreground=fg,
        result=result(status, lane),
        result_ref=f"cases/test/work/lane-{lane:02d}/branch_result.json",
        artifact_exists=lambda _rel: True,
        branch_head=(identity or {}).get("validated_commit") if identity else None,
        evidence_identity=identity,
        now=f"2026-09-19T00:0{lane}:00Z",
    )
    return bg, out


class ParallelBranchFinalizeTests(unittest.TestCase):
    def test_pass_projects_lane_and_releases_barrier_when_peer_green(self):
        bg = backend(0)
        fg = foreground(lane1="GREEN")
        out = apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            now="2026-09-19T00:01:00Z",
        )
        self.assertEqual(out["outcome"], "PASS")
        self.assertEqual(bg["overall"], "GREEN")
        self.assertEqual(bg["dispatch"]["state"], "DONE")
        self.assertEqual(cond(fg, "lane00")["state"], "GREEN")
        self.assertEqual(cond(fg, "barrier")["state"], "GREEN")
        self.assertEqual(cond(fg, "reducer")["state"], "READY")
        self.assertEqual(fg["parallel_branch_aggregate"]["state"], "PASS")

    def test_blocked_becomes_canonical_blocked_and_stops_running(self):
        bg = backend(0)
        fg = foreground()
        out = apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("BLOCKED", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
        )
        self.assertEqual(out["outcome"], "BLOCKED")
        self.assertEqual(bg["overall"], "BLOCKED")
        self.assertEqual(bg["dispatch"]["state"], "BLOCKED")
        self.assertEqual(bg["error"]["kind"], "NEED_SOMETHING")
        self.assertEqual(fg["overall"], "BLOCKED")
        self.assertEqual(cond(fg, "barrier")["state"], "BLOCKED")

    def test_error_becomes_canonical_error(self):
        bg = backend(0)
        fg = foreground()
        out = apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("ERROR", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
        )
        self.assertEqual(out["outcome"], "ERROR")
        self.assertEqual(bg["overall"], "ERROR")
        self.assertEqual(bg["dispatch"]["state"], "ERROR")
        self.assertEqual(fg["overall"], "ERROR")

    def test_permutations_are_order_independent(self):
        cases = [
            (("ERROR", "PASS"), "ERROR"),
            (("PASS", "ERROR"), "ERROR"),
            (("BLOCKED", "PASS"), "BLOCKED"),
            (("PASS", "BLOCKED"), "BLOCKED"),
            (("PASS", "PASS"), "RUNNING"),
        ]
        for statuses, expected_overall in cases:
            with self.subTest(statuses=statuses):
                fg = foreground()
                apply_lane(fg, 0, statuses[0])
                apply_lane(fg, 1, statuses[1])
                self.assertEqual(fg["overall"], expected_overall)
                expected_barrier = {
                    "ERROR": "ERROR",
                    "BLOCKED": "BLOCKED",
                    "RUNNING": "GREEN",
                }[expected_overall]
                self.assertEqual(cond(fg, "barrier")["state"], expected_barrier)
                if statuses == ("PASS", "PASS"):
                    self.assertEqual(cond(fg, "reducer")["state"], "READY")

    def test_error_precedes_blocked_independent_of_order(self):
        for statuses in (("ERROR", "BLOCKED"), ("BLOCKED", "ERROR")):
            with self.subTest(statuses=statuses):
                fg = foreground()
                apply_lane(fg, 0, statuses[0])
                apply_lane(fg, 1, statuses[1])
                self.assertEqual(fg["overall"], "ERROR")
                self.assertEqual(cond(fg, "barrier")["state"], "ERROR")

    def test_downstream_failure_is_not_erased_by_late_branch_pass(self):
        fg = foreground(lane1="GREEN")
        cond(fg, "reducer").update(
            {
                "state": "ERROR",
                "detail": "reducer integration failed",
                "evidence_ref": "github://immutable/reducer/result",
                "source": "reducer_runtime",
            }
        )
        fg["overall"] = "ERROR"
        fg["error"] = {
            "kind": "reducer_error",
            "summary": "reducer integration failed",
            "evidence_ref": "github://immutable/reducer/result",
        }
        apply_lane(fg, 0, "PASS")
        self.assertEqual(fg["overall"], "ERROR")
        self.assertEqual(cond(fg, "barrier")["state"], "GREEN")
        self.assertEqual(cond(fg, "reducer")["state"], "ERROR")
        self.assertEqual(fg["error"]["kind"], "reducer_error")

    def test_independent_downstream_terminal_survives_every_branch_outcome(self):
        for cond_id in ("reducer", "final_acceptance"):
            for downstream_state in ("ERROR", "BLOCKED"):
                for incoming in ("PASS", "ERROR", "BLOCKED"):
                    with self.subTest(
                        cond_id=cond_id,
                        downstream_state=downstream_state,
                        incoming=incoming,
                    ):
                        fg = foreground(lane1="GREEN")
                        evidence = f"evidence/{cond_id}-{downstream_state.lower()}.json"
                        target = cond(fg, cond_id)
                        target.update(
                            {
                                "state": downstream_state,
                                "source": f"{cond_id}_runtime",
                                "detail": f"{cond_id} {downstream_state.lower()}",
                                "evidence_ref": evidence,
                            }
                        )
                        fg["overall"] = downstream_state
                        fg["error"] = {
                            "kind": f"{cond_id}_{downstream_state.lower()}",
                            "summary": target["detail"],
                            "evidence_ref": evidence,
                        }
                        before_target = copy.deepcopy(target)

                        apply_lane(fg, 0, incoming)

                        self.assertEqual(cond(fg, cond_id), before_target)
                        if "ERROR" in {downstream_state, incoming}:
                            expected_overall = "ERROR"
                        else:
                            expected_overall = "BLOCKED"
                        self.assertEqual(fg["overall"], expected_overall)
                        self.assertEqual(
                            cond(fg, "barrier")["state"],
                            {"PASS": "GREEN", "ERROR": "ERROR", "BLOCKED": "BLOCKED"}[incoming],
                        )

    def test_explicit_branch_retry_cannot_clear_independent_downstream_error(self):
        fg = foreground(lane1="GREEN")
        cond(fg, "reducer").update(
            {
                "state": "ERROR",
                "source": "reducer_runtime",
                "detail": "integration failed",
                "evidence_ref": "evidence/integration.json",
            }
        )
        cond(fg, "final_acceptance").update(
            {
                "state": "ERROR",
                "source": "foreground",
                "detail": "rejected",
                "evidence_ref": "evidence/rejected.json",
            }
        )
        fg["overall"] = "ERROR"
        reducer_before = copy.deepcopy(cond(fg, "reducer"))
        final_before = copy.deepcopy(cond(fg, "final_acceptance"))

        apply_lane(fg, 0, "BLOCKED")
        self.assertEqual(fg["overall"], "ERROR")
        self.assertEqual(cond(fg, "reducer"), reducer_before)
        self.assertEqual(cond(fg, "final_acceptance"), final_before)
        self.assertEqual(cond(fg, "barrier")["state"], "BLOCKED")

        retry_bg = backend(0)
        retry_bg["dispatch"].update(
            {
                "dispatch_id": "dispatch-retry-independent",
                "generation": 5,
                "fence_token": "fence-retry-independent",
            }
        )
        retry_result = result("PASS", 0)
        retry_result["dispatch"] = {
            "dispatch_id": "dispatch-retry-independent",
            "generation": 5,
            "fence_token": "fence-retry-independent",
        }
        apply_branch_result(
            task=task(0),
            backend=retry_bg,
            foreground=fg,
            result=retry_result,
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
        )
        self.assertEqual(fg["overall"], "ERROR")
        self.assertEqual(cond(fg, "barrier")["state"], "GREEN")
        self.assertEqual(cond(fg, "reducer"), reducer_before)
        self.assertEqual(cond(fg, "final_acceptance"), final_before)

    def test_branch_owned_failure_can_clear_after_explicit_retry_transition(self):
        fg = foreground()
        apply_lane(fg, 0, "ERROR")
        self.assertEqual(fg["overall"], "ERROR")
        self.assertEqual(cond(fg, "reducer")["source"], AGGREGATE_SOURCE)

        cond(fg, "lane01")["state"] = "GREEN"
        retry_bg = backend(0)
        retry_bg["dispatch"].update(
            {
                "dispatch_id": "dispatch-retry",
                "generation": 5,
                "fence_token": "fence-retry",
            }
        )
        retry_result = result("PASS", 0)
        retry_result["dispatch"] = {
            "dispatch_id": "dispatch-retry",
            "generation": 5,
            "fence_token": "fence-retry",
        }
        apply_branch_result(
            task=task(0),
            backend=retry_bg,
            foreground=fg,
            result=retry_result,
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
        )
        self.assertEqual(fg["overall"], "RUNNING")
        self.assertEqual(cond(fg, "barrier")["state"], "GREEN")
        self.assertEqual(cond(fg, "reducer")["state"], "READY")
        self.assertIsNone(fg["error"])

    def test_stale_generation_is_rejected_without_mutation(self):
        bg = backend(0)
        fg = foreground()
        before_bg = copy.deepcopy(bg)
        before_fg = copy.deepcopy(fg)
        stale = result("PASS", 0)
        stale["dispatch"]["generation"] = 3
        with self.assertRaises(ParallelBranchFinalizeError):
            apply_branch_result(
                task=task(0),
                backend=bg,
                foreground=fg,
                result=stale,
                result_ref="cases/test/work/lane-00/branch_result.json",
                artifact_exists=lambda _rel: True,
            )
        self.assertEqual(bg, before_bg)
        self.assertEqual(fg, before_fg)

    def test_invalid_identity_rejected_without_mutation(self):
        cases = (
            "backend_task",
            "backend_dispatch_id",
            "backend_generation",
            "backend_fence",
            "result_dispatch_id",
            "result_generation",
            "result_fence",
            "foreground_parent",
            "foreground_scope",
        )
        for case in cases:
            with self.subTest(case=case):
                bg = backend(0)
                fg = foreground()
                res = result("PASS", 0)
                if case == "backend_task":
                    bg["task_id"] = "other-task"
                elif case == "backend_dispatch_id":
                    bg["dispatch"]["dispatch_id"] = ""
                elif case == "backend_generation":
                    bg["dispatch"]["generation"] = 0
                elif case == "backend_fence":
                    bg["dispatch"]["fence_token"] = ""
                elif case == "result_dispatch_id":
                    res["dispatch"]["dispatch_id"] = ""
                elif case == "result_generation":
                    res["dispatch"]["generation"] = 0
                elif case == "result_fence":
                    res["dispatch"]["fence_token"] = ""
                elif case == "foreground_parent":
                    fg["task_id"] = "other-parent"
                elif case == "foreground_scope":
                    fg["scope"] = "backend_execution"

                before_bg = copy.deepcopy(bg)
                before_fg = copy.deepcopy(fg)
                with self.assertRaises(ParallelBranchFinalizeError):
                    apply_branch_result(
                        task=task(0),
                        backend=bg,
                        foreground=fg,
                        result=res,
                        result_ref="cases/test/work/lane-00/branch_result.json",
                        artifact_exists=lambda _rel: True,
                    )
                self.assertEqual(bg, before_bg)
                self.assertEqual(fg, before_fg)

    def test_git_wrapper_is_bounded_and_noninteractive(self):
        completed = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch(
            "harness.parallel_branch_finalize.subprocess.run",
            return_value=completed,
        ) as mocked:
            run_git(Path("."), "status")
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 30)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "Never")
            self.assertEqual(kwargs["env"]["SSH_ASKPASS_REQUIRE"], "never")

        with patch(
            "harness.parallel_branch_finalize.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "status"], timeout=30),
        ):
            with self.assertRaisesRegex(ParallelBranchFinalizeError, "timed out after 30s"):
                run_git(Path("."), "status")

    def test_pass_requires_declared_artifacts_to_exist(self):
        bg = backend(0)
        fg = foreground()
        with self.assertRaises(ParallelBranchFinalizeError):
            apply_branch_result(
                task=task(0),
                backend=bg,
                foreground=fg,
                result=result("PASS", 0),
                result_ref="cases/test/work/lane-00/branch_result.json",
                artifact_exists=lambda _rel: False,
            )

    def test_acceptance_pins_commit_and_content_identity(self):
        bg = backend(0)
        fg = foreground(lane1="GREEN")
        identity = evidence_identity(0, commit="a" * 40, fingerprint="1" * 64)
        out = apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            branch_head="a" * 40,
            evidence_identity=identity,
        )
        self.assertFalse(out["already_finalized"])
        self.assertEqual(bg["accepted_evidence"]["validated_commit"], "a" * 40)
        self.assertEqual(bg["accepted_evidence"]["content_fingerprint"], "1" * 64)
        self.assertIn("/" + "a" * 40 + "/", bg["result_ref"])

    def test_unrelated_branch_advance_same_content_is_idempotent(self):
        bg = backend(0)
        fg = foreground(lane1="GREEN")
        first = evidence_identity(0, commit="a" * 40, fingerprint="1" * 64)
        apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            branch_head="a" * 40,
            evidence_identity=first,
        )
        snapshot_bg = copy.deepcopy(bg)
        snapshot_fg = copy.deepcopy(fg)

        advanced = evidence_identity(0, commit="b" * 40, fingerprint="1" * 64)
        out = apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            branch_head="b" * 40,
            evidence_identity=advanced,
        )
        self.assertTrue(out["already_finalized"])
        self.assertEqual(bg, snapshot_bg)
        self.assertEqual(fg, snapshot_fg)
        self.assertEqual(out["accepted_evidence"]["validated_commit"], "a" * 40)

    def test_same_dispatch_changed_content_is_rejected_without_mutation(self):
        bg = backend(0)
        fg = foreground(lane1="GREEN")
        first = evidence_identity(0, commit="a" * 40, fingerprint="1" * 64)
        apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            branch_head="a" * 40,
            evidence_identity=first,
        )
        before_bg = copy.deepcopy(bg)
        before_fg = copy.deepcopy(fg)
        changed = evidence_identity(0, commit="b" * 40, fingerprint="9" * 64)
        with self.assertRaisesRegex(ParallelBranchFinalizeError, "evidence changed"):
            apply_branch_result(
                task=task(0),
                backend=bg,
                foreground=fg,
                result=result("PASS", 0),
                result_ref="cases/test/work/lane-00/branch_result.json",
                artifact_exists=lambda _rel: True,
                branch_head="b" * 40,
                evidence_identity=changed,
            )
        self.assertEqual(bg, before_bg)
        self.assertEqual(fg, before_fg)

    def test_replay_does_not_overwrite_downstream_terminal_state(self):
        bg = backend(0)
        fg = foreground(lane1="GREEN")
        identity = evidence_identity(0)
        apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            branch_head="a" * 40,
            evidence_identity=identity,
        )
        fg["overall"] = "ERROR"
        fg["error"] = {
            "kind": "reducer_error",
            "summary": "later reducer failed",
            "evidence_ref": "github://immutable/reducer",
        }
        cond(fg, "reducer").update(
            {
                "state": "ERROR",
                "detail": "later reducer failed",
                "evidence_ref": "github://immutable/reducer",
                "source": "reducer_runtime",
            }
        )
        before = copy.deepcopy(fg)
        out = apply_branch_result(
            task=task(0),
            backend=bg,
            foreground=fg,
            result=result("PASS", 0),
            result_ref="cases/test/work/lane-00/branch_result.json",
            artifact_exists=lambda _rel: True,
            branch_head="a" * 40,
            evidence_identity=identity,
        )
        self.assertTrue(out["already_finalized"])
        self.assertEqual(fg, before)

    def test_git_identity_survives_branch_advance_and_detects_content_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)

            result_path = root / "cases/test/work/lane-00/branch_result.json"
            artifact_path = root / "cases/test/work/lane-00/file.txt"
            result_path.parent.mkdir(parents=True)
            artifact_path.write_text("artifact-v1\n", encoding="utf-8")
            r = result("PASS", 0)
            result_path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "accepted"], check=True)
            commit_a = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True
            ).stdout.strip()

            identity_a = build_evidence_identity(
                root,
                task=task(0),
                branch_head=commit_a,
                result_ref="cases/test/work/lane-00/branch_result.json",
                result=r,
            )

            (root / "unrelated.txt").write_text("advance\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "unrelated"], check=True)
            commit_b = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True
            ).stdout.strip()

            identity_b = build_evidence_identity(
                root,
                task=task(0),
                branch_head=commit_b,
                result_ref="cases/test/work/lane-00/branch_result.json",
                result=r,
            )
            self.assertEqual(identity_a["content_fingerprint"], identity_b["content_fingerprint"])
            original = subprocess.run(
                ["git", "-C", str(root), "show", f"{identity_a['validated_commit']}:cases/test/work/lane-00/file.txt"],
                check=True, capture_output=True, text=True
            ).stdout
            self.assertEqual(original, "artifact-v1\n")

            artifact_path.write_text("artifact-v2\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "change artifact"], check=True)
            commit_c = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True
            ).stdout.strip()
            identity_c = build_evidence_identity(
                root,
                task=task(0),
                branch_head=commit_c,
                result_ref="cases/test/work/lane-00/branch_result.json",
                result=r,
            )
            self.assertNotEqual(identity_a["content_fingerprint"], identity_c["content_fingerprint"])


if __name__ == "__main__":
    unittest.main()
