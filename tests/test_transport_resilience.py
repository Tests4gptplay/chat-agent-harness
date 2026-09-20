import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from harness.git_process import GitProcessTimeout, run_git
from local_bridge.scheduler import reconcile_dispatch_liveness, stage_action_submit
from local_bridge.server import WakeStore


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def write_json(root: Path, rel: str, value) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class GitFixture:
    def __init__(self, root: Path):
        self.root = root
        self.remote = root / "remote.git"
        self.repo = root / "repo"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "checkout", "-b", "main")
        git(self.repo, "config", "user.name", "gah-test")
        git(self.repo, "config", "user.email", "gah-test@example.invalid")
        git(self.repo, "remote", "add", "origin", str(self.remote))

    def commit_push(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", message)
        git(self.repo, "push", "-u", "origin", "main")
        return git(self.repo, "rev-parse", "HEAD")

    def remote_head(self) -> str:
        return git(self.remote, "rev-parse", "refs/heads/main")


class TransportResilienceTests(unittest.TestCase):
    def test_run_git_timeout_is_bounded_noninteractive_and_classifies_unknown_push(self):
        captured = {}

        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "harness.git_process.subprocess.run", side_effect=fake_run
        ):
            with self.assertRaises(GitProcessTimeout) as cm:
                run_git(Path(td), "push", "origin", "HEAD:main", timeout=0.05)

        self.assertTrue(cm.exception.outcome_unknown)
        self.assertEqual(captured["timeout"], 0.05)
        self.assertEqual(captured["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(captured["env"]["GCM_INTERACTIVE"], "Never")

    def test_blocked_git_lock_does_not_block_health_or_mailbox(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = WakeStore(root / "runtime", root)
            entered = threading.Event()
            release = threading.Event()

            def blocker():
                with store.git_lock:
                    entered.set()
                    release.wait(2)

            thread = threading.Thread(target=blocker, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            health = store.health()
            wake = {
                "v": 1,
                "wake_id": "wake-lock-test-001",
                "project_id": "git-agent-harness",
                "state": "NEED_AGENT",
                "created_at": "2026-09-19T19:00:00Z",
                "payload": {},
            }
            emitted = store.emit(wake)
            claimed = store.claim({
                "client_id": "client-test",
                "project_id": "git-agent-harness",
                "lease_seconds": 60,
            })
            elapsed = time.monotonic() - started
            release.set()
            thread.join(1)

            self.assertTrue(health["ok"])
            self.assertTrue(emitted["ok"])
            self.assertEqual(claimed["wake"]["wake_id"], wake["wake_id"])
            self.assertLess(elapsed, 0.5)

    def _admission_repo(
        self,
        root: Path,
        *,
        lane_owner: str = "pool-owner-0001",
        top_owner: str = "pool-owner-0001",
    ) -> tuple[GitFixture, WakeStore]:
        fx = GitFixture(root)
        task = {
            "v": 1,
            "task_id": "t-admit-001",
            "kind": "parallel_semantic_branch",
            "lane_id": "lane-00",
            "worker_project_key": "g-p-testlane",
            "backend_cl": "cl/t-admit-001.backend.json",
        }
        backend = {
            "v": 1,
            "cl_id": "bg-t-admit-001",
            "task_id": "t-admit-001",
            "scope": "backend_execution",
            "overall": "READY",
            "dispatch": {
                "dispatch_id": "dispatch-admit-0001",
                "generation": 1,
                "fence_token": "fence-admit-0001",
                "state": "READY",
            },
            "conditions": [{"id": "claimed", "state": "WAIT"}],
        }
        state = {
            "v": 1,
            "agent": "chatgpt",
            "last_pool_takeover_id": top_owner,
        }
        lanes = {
            "v": 1,
            "lanes": [{
                "lane_id": "lane-00",
                "project_key": "g-p-testlane",
                "last_pool_takeover_id": lane_owner,
            }],
        }
        write_json(fx.repo, "tasks/t-admit-001.json", task)
        write_json(fx.repo, "cl/t-admit-001.backend.json", backend)
        write_json(fx.repo, "state/chatgpt.json", state)
        write_json(fx.repo, "state/lanes.json", lanes)
        fx.commit_push("seed admission state")
        return fx, WakeStore(root / "bridge", fx.repo)

    def test_dispatch_accept_wrong_owner_lane_or_fence_is_side_effect_free(self):
        with tempfile.TemporaryDirectory() as td:
            fx, store = self._admission_repo(Path(td))
            base_req = {
                "client_id": "client-test",
                "project_id": "git-agent-harness",
                "task_id": "t-admit-001",
                "backend_cl": "cl/t-admit-001.backend.json",
                "dispatch_id": "dispatch-admit-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-admit-0001",
                "worker_ref": "pool-owner-0001",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-testlane",
            }
            before = fx.remote_head()
            cases = [
                ({**base_req, "worker_ref": "pool-wrong-0001"}, "DISPATCH_WORKER_MISMATCH"),
                ({**base_req, "lane_id": "lane-01"}, "DISPATCH_LANE_MISMATCH"),
                ({**base_req, "fence_token": "fence-wrong-0001"}, "DISPATCH_STALE"),
            ]
            for req, expected in cases:
                with self.subTest(expected=expected):
                    result = store.dispatch_accept(req)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], expected)
                    self.assertEqual(fx.remote_head(), before)

    def _admission_request(self, worker_ref: str) -> dict:
        return {
            "client_id": "client-test",
            "project_id": "git-agent-harness",
            "task_id": "t-admit-001",
            "backend_cl": "cl/t-admit-001.backend.json",
            "dispatch_id": "dispatch-admit-0001",
            "dispatch_generation": 1,
            "fence_token": "fence-admit-0001",
            "worker_ref": worker_ref,
            "lane_id": "lane-00",
            "worker_project_key": "g-p-testlane",
        }

    def _remote_projection(self, fx: GitFixture) -> dict[str, str]:
        head = fx.remote_head()
        return {
            "head": head,
            "backend": git(fx.remote, "show", f"{head}:cl/t-admit-001.backend.json"),
            "state": git(fx.remote, "show", f"{head}:state/chatgpt.json"),
            "lanes": git(fx.remote, "show", f"{head}:state/lanes.json"),
            "task": git(fx.remote, "show", f"{head}:tasks/t-admit-001.json"),
        }

    def test_dispatch_accept_lane_owner_beats_stale_legacy_mirror_both_directions(self):
        cases = [
            ("lane-new_top-old_old-rejected", "pool-owner-NEW1", "pool-owner-OLD1", "pool-owner-OLD1", False),
            ("lane-new_top-old_new-valid", "pool-owner-NEW1", "pool-owner-OLD1", "pool-owner-NEW1", True),
            ("lane-old_top-new_new-rejected", "pool-owner-OLD1", "pool-owner-NEW1", "pool-owner-NEW1", False),
            ("lane-old_top-new_old-valid", "pool-owner-OLD1", "pool-owner-NEW1", "pool-owner-OLD1", True),
        ]
        for label, lane_owner, top_owner, claimant, should_accept in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as td:
                fx, store = self._admission_repo(
                    Path(td), lane_owner=lane_owner, top_owner=top_owner
                )
                before = self._remote_projection(fx)
                result = store.dispatch_accept(self._admission_request(claimant))
                if should_accept:
                    self.assertTrue(result["ok"])
                    self.assertTrue(result["accepted"])
                    self.assertEqual(result["worker_ref"], claimant)
                    self.assertEqual(result["owner_source"], "lane_owner")
                    self.assertTrue(result["owner_mirror_conflict"])
                    self.assertNotEqual(fx.remote_head(), before["head"])
                else:
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], "DISPATCH_WORKER_MISMATCH")
                    self.assertEqual(result["canonical_owner"], lane_owner)
                    self.assertEqual(result["owner_source"], "lane_owner")
                    self.assertTrue(result["owner_mirror_conflict"])
                    self.assertEqual(self._remote_projection(fx), before)

    def test_dispatch_accept_explicit_lane00_legacy_owner_fallback_when_lane_owner_absent(self):
        with tempfile.TemporaryDirectory() as td:
            fx, store = self._admission_repo(
                Path(td), lane_owner="", top_owner="pool-owner-LEGACY1"
            )
            result = store.dispatch_accept(self._admission_request("pool-owner-LEGACY1"))
            self.assertTrue(result["ok"])
            self.assertTrue(result["accepted"])
            self.assertEqual(result["owner_source"], "legacy_lane00_mirror_fallback")
            self.assertFalse(result["owner_mirror_conflict"])

    def test_scheduler_action_and_liveness_reject_stale_mirror_owner_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            fx, store = self._admission_repo(
                Path(td), lane_owner="pool-owner-NEW1", top_owner="pool-owner-OLD1"
            )
            store._git("fetch", "--quiet", "--no-tags", "origin", "main")
            backend = json.loads(store._git("show", "FETCH_HEAD:cl/t-admit-001.backend.json").stdout)
            backend["dispatch"].update({
                "state": "RUNNING",
                "acked_by_worker_ref": "pool-owner-OLD1",
                "acked_at": "2026-09-19T20:00:00+00:00",
                "lease_expires_at": "2099-09-19T20:10:00+00:00",
            })
            foreground = {
                "v": 1,
                "cl_id": "fg-t-admit-001",
                "task_id": "t-admit-001",
                "scope": "foreground_supervision",
                "overall": "RUNNING",
                "conditions": [],
            }
            write_json(fx.repo, "cl/t-admit-001.backend.json", backend)
            write_json(fx.repo, "cl/t-admit-001.foreground.json", foreground)
            fx.commit_push("seed stale acknowledged owner")
            before = self._remote_projection(fx)
            before["foreground"] = git(
                fx.remote,
                "show",
                f"{before['head']}:cl/t-admit-001.foreground.json",
            )

            action = {
                "v": 1,
                "action_id": "t-admit-001-a001",
                "task_id": "t-admit-001",
                "round": 1,
                "executor": "echo",
                "operation": "echo",
                "payload": {"message": "hello"},
                "expected_evidence": ["echo"],
                "foreground_cl": "cl/t-admit-001.foreground.json",
                "backend_cl": "cl/t-admit-001.backend.json",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-testlane",
                "worker_continuation": True,
            }
            action_result = stage_action_submit(store, {
                "client_id": "client-test",
                "project_id": "git-agent-harness",
                "task_id": "t-admit-001",
                "backend_cl": "cl/t-admit-001.backend.json",
                "foreground_cl": "cl/t-admit-001.foreground.json",
                "dispatch_id": "dispatch-admit-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-admit-0001",
                "worker_ref": "pool-owner-OLD1",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-testlane",
                "action": action,
            })
            self.assertFalse(action_result["ok"])
            self.assertEqual(action_result["error"], "ACTION_SUBMIT_WORKER_MISMATCH")
            self.assertEqual(action_result["canonical_owner"], "pool-owner-NEW1")
            self.assertTrue(action_result["owner_mirror_conflict"])

            live_result = reconcile_dispatch_liveness(store, {
                "client_id": "client-test",
                "project_id": "git-agent-harness",
                "task_id": "t-admit-001",
                "backend_cl": "cl/t-admit-001.backend.json",
                "dispatch_id": "dispatch-admit-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-admit-0001",
                "worker_ref": "pool-owner-OLD1",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-testlane",
                "response_running": False,
                "response_ended": True,
            })
            self.assertTrue(live_result["ok"])
            self.assertFalse(live_result["recovered"])
            self.assertEqual(live_result["reason"], "canonical_worker_owner_mismatch")
            self.assertEqual(live_result["canonical_owner"], "pool-owner-NEW1")
            self.assertTrue(live_result["owner_mirror_conflict"])

            after = self._remote_projection(fx)
            after["foreground"] = git(
                fx.remote,
                "show",
                f"{after['head']}:cl/t-admit-001.foreground.json",
            )
            self.assertEqual(after, before)

    def test_dispatch_accept_unknown_push_outcome_refetches_before_retry(self):
        with tempfile.TemporaryDirectory() as td:
            fx, store = self._admission_repo(Path(td))
            request = {
                "client_id": "client-test",
                "project_id": "git-agent-harness",
                "task_id": "t-admit-001",
                "backend_cl": "cl/t-admit-001.backend.json",
                "dispatch_id": "dispatch-admit-0001",
                "dispatch_generation": 1,
                "fence_token": "fence-admit-0001",
                "worker_ref": "pool-owner-0001",
                "lane_id": "lane-00",
                "worker_project_key": "g-p-testlane",
            }
            real_run_git = run_git
            pushed_then_timed_out = {"done": False}

            def flaky_run_git(root, *args, **kwargs):
                result = real_run_git(root, *args, **kwargs)
                if args and args[0] == "push" and not pushed_then_timed_out["done"]:
                    pushed_then_timed_out["done"] = True
                    raise GitProcessTimeout(
                        ["git", "-C", str(root), *args],
                        kwargs.get("timeout", 30),
                        outcome_unknown=True,
                    )
                return result

            with mock.patch("local_bridge.server.run_git", side_effect=flaky_run_git):
                result = store.dispatch_accept(request)

            self.assertTrue(pushed_then_timed_out["done"])
            self.assertTrue(result["ok"])
            self.assertFalse(result["accepted"])
            self.assertEqual(result["idle"], "already_accepted")
            self.assertEqual(result["state"], "RUNNING")
            self.assertEqual(int(git(fx.remote, "rev-list", "--all", "--count")), 2)
            store._git("fetch", "--quiet", "--no-tags", "origin", "main")
            backend = json.loads(store._git("show", "FETCH_HEAD:cl/t-admit-001.backend.json").stdout)
            self.assertEqual(backend["dispatch"]["state"], "RUNNING")
            self.assertEqual(backend["dispatch"]["acked_by_worker_ref"], "pool-owner-0001")

    def _semantic_repo(self, root: Path, *, stale: bool = False, malformed: bool = False):
        fx = GitFixture(root)
        task_id = "t-semantic"
        dispatch = {
            "dispatch_id": "dispatch-semantic-0001",
            "generation": 1,
            "fence_token": "fence-semantic-0001",
            "state": "RUNNING",
            "acked_at": "2026-09-19T19:00:00Z",
            "acked_by_worker_ref": "pool-owner-0001",
            "lease_expires_at": "2026-09-19T19:10:00Z",
        }
        task = {
            "v": 1,
            "task_id": task_id,
            "kind": "zhihu_review_one_shot",
            "deterministic_prelude": {
                "action_path": "actions/stage0/t-semantic.json",
                "result_path": "results/t-semantic.json",
                "artifact_path": "evidence/t-semantic/review_packet.json",
            },
            "execution_contract": {
                "foreground_cl": "cl/t-semantic.foreground.json",
                "backend_cl": "cl/t-semantic.backend.json",
                "capture_result": "results/t-semantic.json",
                "final_analysis": "results/t-semantic.analysis.json",
            },
            "semantic_reduce": {
                "lane_id": "lane-00",
                "worker_project_key": "g-p-testlane",
                "analysis_path": "results/t-semantic.analysis.json",
            },
        }
        backend = {
            "v": 1,
            "cl_id": "bg-t-semantic",
            "task_id": task_id,
            "scope": "backend_execution",
            "overall": "RUNNING",
            "created_at": "x",
            "updated_at": "x",
            "result_ref": None,
            "error": None,
            "dispatch": dispatch,
            "conditions": [
                {"id": "claimed", "state": "GREEN"},
                {"id": "executor", "state": "GREEN"},
                {"id": "durable_result", "state": "GREEN"},
                {"id": "verification", "state": "WAIT"},
                {"id": "terminal", "state": "WAIT"},
            ],
        }
        foreground = {
            "v": 1,
            "cl_id": "fg-t-semantic",
            "task_id": task_id,
            "scope": "foreground_supervision",
            "overall": "RUNNING",
            "result_ref": None,
            "error": None,
            "supervisor_guard": {"state": "HELD", "detail": None},
            "conditions": [
                {"id": "harness_claimed", "state": "GREEN"},
                {"id": "backend_execution", "state": "GREEN"},
                {"id": "durable_result", "state": "GREEN"},
                {"id": "verification", "state": "WAIT"},
                {"id": "final_acceptance", "state": "WAIT"},
            ],
        }
        state = {
            "v": 1,
            "agent": "chatgpt",
            "updated": "2026-09-19",
            "phase": "RUNNING",
            "verified": [],
            "active_task": task_id,
            "active_action": "actions/stage0/t-semantic.json",
            "pending_wake_id": None,
            "active_dispatch_ref": "cl/t-semantic.backend.json",
            "next_reads": [],
            "next_action": "finish",
        }
        action = {
            "v": 1,
            "action_id": "t-semantic-a001",
            "task_id": task_id,
            "executor": "camoufox_zhihu_review",
            "operation": "capture_review_packet",
        }
        capture = {
            "v": 1,
            "result_id": "result-t-semantic-a001",
            "action_id": "t-semantic-a001",
            "task_id": task_id,
            "status": "PASS",
            "artifacts": ["evidence/t-semantic/review_packet.json"],
        }
        for rel, value in [
            ("tasks/t-semantic.json", task),
            ("cl/t-semantic.backend.json", backend),
            ("cl/t-semantic.foreground.json", foreground),
            ("state/chatgpt.json", state),
            ("actions/stage0/t-semantic.json", action),
            ("results/t-semantic.json", capture),
            ("evidence/t-semantic/review_packet.json", {"ok": True}),
        ]:
            write_json(fx.repo, rel, value)
        fx.commit_push("seed running semantic task")

        analysis_dispatch = {
            "dispatch_id": "dispatch-old-0001" if stale else dispatch["dispatch_id"],
            "generation": 0 if stale else 1,
            "fence_token": "fence-old-0001" if stale else dispatch["fence_token"],
        }
        analysis = {
            "v": 1,
            "task_id": task_id,
            "status": "PASS",
            "dispatch": analysis_dispatch,
            "summary": "complete",
            "participants": ["DeepSeek", "MiniMax", "Xiaomi", "Doubao", "GLM", "Kimi", "Qwen"],
            "groups": {"a": ["MiniMax"], "b": ["DeepSeek"]},
            "tasks": ["camera", "house"],
            "evaluation_dimensions": ["a", "b", "c", "d"],
            "quality_findings": [
                {"claim": "a", "provenance": "author_claim", "evidence_refs": ["results/t-semantic.json"]},
                {"claim": "b", "provenance": "evidence_fact", "evidence_refs": ["evidence/t-semantic/review_packet.json"]},
                {"claim": "c", "provenance": "worker_inference", "evidence_refs": ["results/t-semantic.json"]},
            ],
            "timing_findings": [{}, {}],
            "deepseek_basis": {"strengths": ["a", "b", "c"], "tradeoffs": ["d"]},
            "methodology_limitations": ["a", "b", "c"],
            "evidence_refs": ["results/t-semantic.json", "evidence/t-semantic/review_packet.json"],
        }
        path = fx.repo / "results/t-semantic.analysis.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken" if malformed else json.dumps(analysis), encoding="utf-8")
        fx.commit_push("commit semantic terminal artifact")
        store = WakeStore(root / "bridge", fx.repo)
        req = {
            "client_id": "client-test",
            "project_id": "git-agent-harness",
            "task_id": task_id,
            "backend_cl": "cl/t-semantic.backend.json",
            "dispatch_id": dispatch["dispatch_id"],
            "dispatch_generation": 1,
            "fence_token": dispatch["fence_token"],
            "worker_ref": "pool-owner-0001",
            "lane_id": "lane-00",
            "worker_project_key": "g-p-testlane",
            "response_running": False,
            "response_ended": True,
            "observed_at": "2026-09-19T19:01:00Z",
        }
        return fx, store, req

    def test_nonparallel_terminal_artifact_finalizes_before_generation_bump(self):
        with tempfile.TemporaryDirectory() as td:
            fx, store, req = self._semantic_repo(Path(td))
            result = reconcile_dispatch_liveness(store, req)
            self.assertTrue(result["ok"])
            self.assertFalse(result["recovered"])
            self.assertEqual(result["reason"], "semantic_result_terminal_finalized")
            store._git("fetch", "--quiet", "--no-tags", "origin", "main")
            backend = json.loads(store._git("show", "FETCH_HEAD:cl/t-semantic.backend.json").stdout)
            self.assertEqual(backend["dispatch"]["generation"], 1)
            self.assertEqual(backend["dispatch"]["state"], "DONE")
            self.assertEqual(backend["overall"], "GREEN")

    def test_stale_or_malformed_nonparallel_artifact_does_not_suppress_redrive(self):
        for label, kwargs in [
            ("stale", {"stale": True}),
            ("malformed", {"malformed": True}),
        ]:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                _fx, store, req = self._semantic_repo(Path(td), **kwargs)
                result = reconcile_dispatch_liveness(store, req)
                self.assertTrue(result["ok"])
                self.assertTrue(result["recovered"])
                self.assertEqual(result["dispatch"]["generation"], 2)
                self.assertNotEqual(result["dispatch"]["fence_token"], req["fence_token"])


if __name__ == "__main__":
    unittest.main()
