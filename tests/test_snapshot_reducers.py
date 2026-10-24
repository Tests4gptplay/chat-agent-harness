"""Shared reads preserve fresh write admission and local post-commit delivery."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from local_bridge.control import control_status
from local_bridge.planner_runtime import planner_final_delivery_status, planner_runtime_tick
from local_bridge.scheduler import reconcile_dispatch_liveness, reconcile_foreground_terminal
from local_bridge.server import WakeStore
from harness.worker_lineage_compat import LAST_WORKER_REF_KEY, WORKER_LINEAGES_KEY
from test_foreground_terminal import (
    FG_REF, base_event, base_result,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL,
    ).strip()


class SnapshotReducerTests(unittest.TestCase):
    def repo(self, files: dict[str, dict]) -> WakeStore:
        temp = tempfile.TemporaryDirectory(prefix="cah-snapshot-reducers-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo, origin = root / "repo", root / "remote.git"
        repo.mkdir()
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        git(repo, "init", "-q", "--initial-branch=main")
        git(repo, "config", "user.name", "snapshot-reducers")
        git(repo, "config", "user.email", "snapshot-reducers@example.invalid")
        for rel, value in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "seed isolated reducers")
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-q", "origin", "HEAD:main")
        return WakeStore(root / "spool", repo_root=repo, git_snapshot_interval=60)

    def test_four_idle_read_endpoints_share_one_immutable_commit(self):
        store = self.repo({"state/chatgpt.json": {"control_request": None}})
        calls = []
        original = store._git

        def counted(*args):
            calls.append(args)
            return original(*args)

        req = {"client_id": "client-shared-reads", "project_id": "git-agent-harness"}
        with patch.object(store, "_git", side_effect=counted):
            results = [control_status(store, req), planner_runtime_tick(store, req),
                       planner_final_delivery_status(store, req),
                       reconcile_foreground_terminal(store, req)]
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(sum(args[0] == "fetch" for args in calls), 1)
        sha = results[0]["source_commit"]
        immutable_reads = [args for args in calls if args[0] in {"show", "ls-tree", "cat-file"}]
        self.assertTrue(immutable_reads)
        self.assertFalse(any("FETCH_HEAD" in arg for args in immutable_reads for arg in args))
        self.assertEqual(results[1]["source_commit"], sha)
        self.assertEqual(results[2]["source_commit"], sha)
        self.assertEqual(results[3]["source_commit"], sha)

    def test_foreground_claim_revalidates_after_cached_candidate_was_consumed(self):
        from test_foreground_terminal import TerminalBridgeTests
        temp, initial_store = TerminalBridgeTests()._repo(event=base_event(), result=base_result())
        self.addCleanup(temp.cleanup)
        store = WakeStore(initial_store.runtime.parent / "other-spool",
                          repo_root=initial_store.repo_root, git_snapshot_interval=60)
        cached_sha = store.read_git_snapshot()
        foreground = store.repo_root / FG_REF
        value = json.loads(foreground.read_text())
        value["terminal_event"] = base_event(state="CONSUMED")
        foreground.write_text(json.dumps(value))
        git(store.repo_root, "add", FG_REF)
        git(store.repo_root, "commit", "-qm", "external actor consumed terminal")
        git(store.repo_root, "push", "-q", "origin", "HEAD:main")
        remote_sha = git(store.repo_root, "rev-parse", "HEAD")
        self.assertNotEqual(cached_sha, remote_sha)
        out = reconcile_foreground_terminal(
            store, {"client_id": "client-fresh-claim", "project_id": "git-agent-harness"},
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "FOREGROUND_TERMINAL_ALREADY_CLAIMED")
        self.assertEqual(git(store.repo_root, "ls-remote", "origin", "refs/heads/main").split()[0], remote_sha)

    def test_planner_claim_revalidates_after_cached_candidate_was_consumed(self):
        cell_ref = "state/task_cells/planner-cached-terminal-001.json"
        cell = {"task_id": "planner-cached-terminal-001", "control_epoch": 1,
                "planner_control": {"activity": "CLEANING", "runtime": {
                    "final_delivery": {"kind": "planner_final_delivery", "state": "PENDING",
                                       "event_id": "planner-terminal-event-001"}}}}
        store = self.repo({"state/chatgpt.json": {"control_request": None}, cell_ref: cell})
        cached_sha = store.read_git_snapshot()
        cell["planner_control"]["runtime"]["final_delivery"]["state"] = "CONSUMED"
        (store.repo_root / cell_ref).write_text(json.dumps(cell))
        git(store.repo_root, "add", cell_ref)
        git(store.repo_root, "commit", "-qm", "external actor consumed Planner delivery")
        git(store.repo_root, "push", "-q", "origin", "HEAD:main")
        remote_sha = git(store.repo_root, "rev-parse", "HEAD")
        self.assertNotEqual(cached_sha, remote_sha)
        out = planner_final_delivery_status(
            store, {"client_id": "client-planner-fresh-claim", "project_id": "git-agent-harness"},
        )
        self.assertTrue(out["ok"])
        self.assertIsNone(out["event"])
        self.assertEqual(out["source_commit"], remote_sha)
        self.assertEqual(git(store.repo_root, "ls-remote", "origin", "refs/heads/main").split()[0], remote_sha)

    def liveness_store(self, *, managed=True, near_lease=False):
        task_id, worker_ref = "worker-observation-001", "worker-observation-ref-001"
        dispatch = {"dispatch_id": "dispatch-observation-001", "generation": 2,
                    "fence_token": "fence-observation-001", "state": "RUNNING",
                    "acked_by_worker_ref": worker_ref,
                    "lease_expires_at": (datetime.now(timezone.utc) + timedelta(
                        seconds=1 if near_lease else 3600)).isoformat()}
        store = self.repo({
            "state/chatgpt.json": {"control_request": None},
            f"tasks/{task_id}.json": {"task_id": task_id,
                "kind": "planner_worker_child" if managed else "semantic_worker"},
            f"cl/{task_id}.backend.json": {"task_id": task_id,
                "scope": "backend_execution", "dispatch": dispatch},
            "state/lanes.json": {"lanes": [{"lane_id": "lane-00", "project_key": "g-p-observation",
                WORKER_LINEAGES_KEY: {f"{task_id}::1": {"owner_task_id": task_id,
                    "owner_control_epoch": 1, LAST_WORKER_REF_KEY: worker_ref}}}]},
        })
        req = {"client_id": "client-observation", "project_id": "git-agent-harness",
               "task_id": task_id, "backend_cl": f"cl/{task_id}.backend.json",
               "dispatch_id": dispatch["dispatch_id"], "dispatch_generation": 2,
               "fence_token": dispatch["fence_token"], "worker_ref": worker_ref,
               "lane_id": "lane-00", "worker_project_key": "g-p-observation",
               "response_running": True, "observation": True}
        return store, req

    def test_managed_running_observation_never_waits_for_git_lock_or_fetch(self):
        store, req = self.liveness_store()
        store.read_git_snapshot()
        started, release = threading.Event(), threading.Event()

        def slow_control_transaction():
            with store.git_lock:
                started.set()
                release.wait(timeout=5)

        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(slow_control_transaction)
            self.assertTrue(started.wait(timeout=2))
            try:
                original = store._git
                def local_only(*args):
                    self.assertNotEqual(args[0], "fetch")
                    return original(*args)
                with patch.object(store, "_git", side_effect=local_only):
                    result = pool.submit(reconcile_dispatch_liveness, store, req).result(timeout=1)
                self.assertEqual(result["reason"], "managed_worker_response_running_watchdog_owned")
            finally:
                release.set()
                slow.result(timeout=2)

    def test_managed_observation_with_no_current_snapshot_defers_projection(self):
        store, req = self.liveness_store()
        with patch.object(store, "_git", side_effect=AssertionError("must not use Git")):
            result = reconcile_dispatch_liveness(store, req)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "git_snapshot_pending")

    def test_one_batch_keeps_its_fixed_observation_sha_after_cache_expiry(self):
        store, req = self.liveness_store()
        sha = store.read_git_snapshot()
        store.invalidate_git_snapshot()
        original = store._git
        def local_only(*args):
            self.assertNotEqual(args[0], "fetch")
            return original(*args)
        with patch.object(store, "_git", side_effect=local_only):
            out = reconcile_dispatch_liveness(store, req, _snapshot_sha=sha)
        self.assertEqual(out["reason"], "managed_worker_response_running_watchdog_owned")
        self.assertIsNone(store.peek_git_snapshot())

    def test_observation_preserves_exact_fence_and_legacy_real_renewal_boundary(self):
        store, req = self.liveness_store(managed=False, near_lease=True)
        store.read_git_snapshot()
        self.assertEqual(reconcile_dispatch_liveness(store, {**req, "fence_token": "another-fence"})["reason"],
                         "stale_dispatch")
        original_sha = store.read_git_snapshot()
        out = reconcile_dispatch_liveness(store, req)
        self.assertTrue(out["requires_fresh_liveness"])
        self.assertEqual(store.read_git_snapshot(), original_sha)
        renewed = reconcile_dispatch_liveness(store, {**req, "observation": False})
        self.assertEqual(renewed["reason"], "response_still_running_lease_renewed")
        self.assertEqual(store.read_git_snapshot(), renewed["commit_sha"])
        self.assertNotEqual(renewed["commit_sha"], original_sha)


class PostCommitWorkerDeliveryTests(unittest.TestCase):
    def test_first_planner_children_are_emitted_only_after_canonical_push(self):
        from test_planner_runtime_integration import PlannerTurnReductionIntegrationTests
        emitted = []
        original = WakeStore.emit

        def verified_emit(store, wake):
            sha = store.read_git_snapshot()
            canonical = json.loads(store._git(
                "show", f"{sha}:requests/worker-wake/{wake['wake_id']}.json",
            ).stdout)
            self.assertEqual(canonical, wake)
            self.assertEqual(store._git("ls-remote", store.git_remote, f"refs/heads/{store.git_branch}")
                             .stdout.split()[0], sha)
            emitted.append(wake["wake_id"])
            return original(store, wake)

        with patch.dict(os.environ, {"CAH_LOCAL_WORKER_LANES": "lane-00,lane-01"}), \
                patch.object(WakeStore, "emit", verified_emit):
            PlannerTurnReductionIntegrationTests().test_tick_reduces_two_worker_directions_into_fixed_five_round()
        self.assertEqual(len(emitted), 2)
        self.assertEqual(len(set(emitted)), 2)

    def test_rejected_planner_push_cannot_emit_uncommitted_children(self):
        from local_bridge import planner_runtime
        from test_planner_runtime_integration import PlannerTurnReductionIntegrationTests
        original = planner_runtime._run
        invalidations = []
        original_invalidate = WakeStore.invalidate_git_snapshot

        def reject_push(repo, *args):
            if args[0] == "push":
                raise subprocess.CalledProcessError(1, ["git", *args], stderr="synthetic non-fast-forward")
            return original(repo, *args)

        def invalidate(store):
            invalidations.append(True)
            original_invalidate(store)

        with patch.dict(os.environ, {"CAH_LOCAL_WORKER_LANES": "lane-00,lane-01"}), \
                patch.object(planner_runtime, "_run", side_effect=reject_push), \
                patch.object(WakeStore, "emit") as emitted, \
                patch.object(WakeStore, "invalidate_git_snapshot", invalidate):
            with self.assertRaises(subprocess.CalledProcessError):
                PlannerTurnReductionIntegrationTests().test_tick_reduces_two_worker_directions_into_fixed_five_round()
        emitted.assert_not_called()
        self.assertTrue(invalidations)

    def test_first_and_review_continuation_run_without_external_request_drain(self):
        from semantic_runtime_fixture import HarnessCase
        case = HarnessCase()
        self.addCleanup(case.close)

        def until(predicate, count=14):
            for _ in range(count):
                case.tick(deliver=False)
                if predicate():
                    return
            self.fail(str(case.ticks[-1]))

        def generation():
            children = case.children()
            return case.read(children[0]["backend_cl"])["dispatch"]["generation"] if children else 0

        # Any accidental use of the old external drainer is a hard test failure.
        with patch("semantic_runtime_fixture.drain", side_effect=AssertionError("external drainer forbidden")):
            case.tick(deliver=False)
            case.decide(["lane-00"])
            until(lambda: bool(case.current("lane-00")) and case.current("lane-00").get("handoff_verified"))
            for expected in range(2, 6):
                case.worker_result("lane-00")
                until(lambda: generation() == expected and (case.current("lane-00").get("worker_watchdog") or {}).get("dispatch_generation") == expected)
            case.worker_result("lane-00")
            until(lambda: bool(case.planner().get("active_request_id")))
            self.assertEqual(generation(), 5)
            case.decide()
            until(lambda: generation() == 6 and (case.current("lane-00").get("worker_watchdog") or {}).get("dispatch_generation") == 6)
        self.assertEqual(len([item for item in case.ui.created if item["role"] == "worker"]), 6)
        self.assertTrue(all(not page.answer for page in case.ui.all))
