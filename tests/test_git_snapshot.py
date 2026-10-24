"""Canonical read coalescing never substitutes for a transaction's fresh read."""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.worker_lineage_compat import LAST_WORKER_REF_KEY, WORKER_LINEAGES_KEY
from local_bridge.git_snapshot import GitSnapshot
from local_bridge.server import WakeStore


FIRST = "a" * 40
SECOND = "b" * 40


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class GitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.refresh = Mock(return_value=FIRST)
        self.snapshot = GitSnapshot(self.refresh, threading.RLock(), clock=self.clock)

    def test_consumers_share_one_immutable_commit_until_next_due_refresh(self):
        self.assertEqual(self.snapshot.read(), FIRST)
        self.refresh.return_value = SECOND
        self.clock.now = 0.99
        self.assertEqual([self.snapshot.read() for _ in range(4)], [FIRST] * 4)
        self.assertEqual(self.refresh.call_count, 1)
        self.clock.now = 1.0
        self.assertEqual(self.snapshot.read(), SECOND)
        self.assertEqual(self.refresh.call_count, 2)

    def test_unchanged_commit_still_refreshes_when_due(self):
        self.snapshot.read()
        self.clock.now = 2.0
        self.assertEqual(self.snapshot.read(), FIRST)
        self.assertEqual(self.refresh.call_count, 2)

    def test_concurrent_consumers_issue_only_one_remote_refresh(self):
        started = threading.Event()
        release = threading.Event()

        def refresh():
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return FIRST

        counted_refresh = Mock(side_effect=refresh)
        snapshot = GitSnapshot(counted_refresh, threading.RLock(), clock=self.clock)
        with ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(snapshot.read)
            self.assertTrue(started.wait(timeout=3))
            others = [pool.submit(snapshot.read) for _ in range(7)]
            release.set()
            self.assertEqual(first.result(timeout=3), FIRST)
            self.assertEqual([future.result(timeout=3) for future in others], [FIRST] * 7)
        self.assertEqual(counted_refresh.call_count, 1)
        # The next force demonstrates fresh state remains explicitly available.
        snapshot.record(SECOND)
        self.assertEqual(snapshot.read(), SECOND)

    def test_force_reads_remain_fresh_transaction_inputs(self):
        self.assertEqual(self.snapshot.read(), FIRST)
        self.refresh.return_value = SECOND
        self.assertEqual(self.snapshot.read(force=True), SECOND)
        self.assertEqual(self.refresh.call_count, 2)

    def test_failed_refresh_is_not_reported_as_a_valid_stale_snapshot(self):
        self.snapshot.read()
        self.clock.now = 1.0
        self.refresh.side_effect = OSError("remote unavailable")
        with self.assertRaisesRegex(OSError, "remote unavailable"):
            self.snapshot.read()
        self.assertIsNone(self.snapshot.confirmed())
        self.clock.now = 1.5
        with self.assertRaisesRegex(OSError, "remote unavailable"):
            self.snapshot.read()
        self.assertEqual(self.refresh.call_count, 2)
        self.clock.now = 2.0
        self.refresh.side_effect = None
        self.refresh.return_value = SECOND
        self.assertEqual(self.snapshot.read(), SECOND)
        self.assertEqual(self.refresh.call_count, 3)

    def test_confirmed_push_is_immediately_visible_without_another_fetch(self):
        self.snapshot.read()
        self.snapshot.record(SECOND)
        self.assertEqual(self.snapshot.read(), SECOND)
        self.assertEqual(self.refresh.call_count, 1)

    def test_unknown_push_outcome_discards_the_hint(self):
        self.snapshot.read()
        self.refresh.return_value = SECOND
        self.snapshot.invalidate()
        self.assertEqual(self.snapshot.read(), SECOND)
        self.assertEqual(self.refresh.call_count, 2)

    def test_confirmed_push_clears_read_failure_backoff(self):
        self.refresh.side_effect = OSError("remote unavailable")
        with self.assertRaises(OSError):
            self.snapshot.read()
        self.snapshot.record(SECOND)
        self.assertEqual(self.snapshot.read(), SECOND)
        self.assertEqual(self.refresh.call_count, 1)

    def test_peek_only_exposes_an_unexpired_confirmed_commit(self):
        self.assertIsNone(self.snapshot.peek())
        self.snapshot.read()
        self.assertEqual(self.snapshot.peek(), FIRST)
        self.clock.now = 1.0
        self.assertIsNone(self.snapshot.peek())
        self.assertEqual(self.snapshot.confirmed(), FIRST)
        self.assertEqual(self.refresh.call_count, 1)
        self.snapshot.record(SECOND)
        self.assertEqual(self.snapshot.peek(), SECOND)
        self.snapshot.invalidate()
        self.assertIsNone(self.snapshot.peek())
        self.assertIsNone(self.snapshot.confirmed())

    def test_expired_peek_does_not_wait_for_inflight_remote_refresh(self):
        self.snapshot.read()
        self.clock.now = 1.0
        started, release = threading.Event(), threading.Event()

        def slow_refresh():
            started.set()
            self.assertTrue(release.wait(timeout=3))
            return SECOND

        self.refresh.side_effect = slow_refresh
        with ThreadPoolExecutor(max_workers=2) as pool:
            refresh = pool.submit(self.snapshot.read)
            self.assertTrue(started.wait(timeout=3))
            try:
                peek = pool.submit(self.snapshot.peek)
                self.assertIsNone(peek.result(timeout=0.5))
            finally:
                release.set()
            self.assertEqual(refresh.result(timeout=3), SECOND)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class WakeStoreSnapshotTests(unittest.TestCase):
    def test_other_fetch_cannot_change_the_cached_commit_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin, repo = root / "origin.git", root / "repo"
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            repo.mkdir()
            git(repo, "init", "-q", "--initial-branch=main")
            git(repo, "config", "user.name", "snapshot-test")
            git(repo, "config", "user.email", "snapshot@example.invalid")
            (repo / "state").mkdir()
            state = repo / "state/chatgpt.json"
            state.write_text(json.dumps({"revision": 1}))
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "first")
            git(repo, "remote", "add", "origin", str(origin))
            git(repo, "push", "-q", "origin", "main")
            first = git(repo, "rev-parse", "HEAD")
            clock = Clock()
            store = WakeStore(root / "bridge", repo, git_snapshot_clock=clock)
            with unittest.mock.patch.object(store, "_git", wraps=store._git) as calls:
                self.assertEqual(store.read_git_snapshot(), first)
                git(repo, "switch", "-qc", "other")
                state.write_text(json.dumps({"revision": 2}))
                git(repo, "add", ".")
                git(repo, "commit", "-qm", "second")
                git(repo, "push", "-q", "origin", "other")
                git(repo, "fetch", "-q", "origin", "other")
                self.assertNotEqual(git(repo, "rev-parse", "FETCH_HEAD"), first)
                cached = store.read_git_snapshot()
                self.assertEqual(cached, first)
                self.assertEqual(json.loads(git(repo, "show", cached + ":state/chatgpt.json")), {"revision": 1})
                fetches = [call for call in calls.call_args_list if call.args[0] == "fetch"]
                self.assertEqual(len(fetches), 1)
                git(repo, "push", "-q", "origin", "HEAD:main")
                self.assertNotEqual(store.read_git_snapshot(force=True), first)


class WakeStoreObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / "repo"
        repo.mkdir()
        self.clock = Clock()
        self.store = WakeStore(root / "bridge", repo, git_snapshot_clock=self.clock)
        self.dispatch = {
            "client_id": "snapshot-observation", "project_id": "test-project",
            "backend_cl": "cl/observation.backend.json", "task_id": "task-observation",
            "dispatch_id": "dispatch-observation", "dispatch_generation": 1,
            "fence_token": "fence-observation", "observation": True,
        }
        self.takeover = {
            "client_id": "snapshot-observation", "project_id": "test-project",
            "handoff_id": "pool-observation", "lane_id": "lane-00",
            "worker_project_key": "g-p-observation", "owner_task_id": "task-observation",
            "owner_control_epoch": 1, "observation": True,
        }

    def test_missing_or_expired_hint_defers_observation_without_any_git_io(self):
        self.store._git = Mock()
        with unittest.mock.patch("local_bridge.server.finalize_topology_request") as finalize:
            for expired in (False, True):
                if expired:
                    self.store.record_git_commit(FIRST)
                    self.clock.now = 1.0
                for endpoint, request in (
                    (self.store.dispatch_status, self.dispatch),
                    (self.store.worker_takeover_status, self.takeover),
                ):
                    result = endpoint(request)
                    self.assertTrue(result["ok"])
                    self.assertIsNone(result["matched"])
                    self.assertEqual(result["idle"], "git_snapshot_unavailable")
            finalize.assert_not_called()
        self.store._git.assert_not_called()

    def test_current_observation_uses_one_commit_without_waiting_for_git_lock(self):
        self.store.record_git_commit(FIRST)
        documents = {
            "cl/observation.backend.json": {
                "scope": "backend_execution", "task_id": "task-observation",
                "dispatch": {"dispatch_id": "dispatch-observation", "generation": 1,
                             "fence_token": "fence-observation", "state": "RUNNING"},
            },
            "state/chatgpt.json": {"agent": "chatgpt"},
            "state/lanes.json": {"lanes": [{
                "lane_id": "lane-00", "project_key": "g-p-observation",
                WORKER_LINEAGES_KEY: {"task-observation::1": {
                    "owner_task_id": "task-observation", "owner_control_epoch": 1,
                    LAST_WORKER_REF_KEY: "pool-observation",
                }},
            }]},
        }

        def show(command, ref):
            self.assertEqual(command, "show")
            commit, path = ref.split(":", 1)
            self.assertEqual(commit, FIRST)
            return SimpleNamespace(stdout=json.dumps(documents[path]))

        self.store._git = Mock(side_effect=show)
        locked, release = threading.Event(), threading.Event()

        def hold_transaction_lock():
            with self.store.git_lock:
                locked.set()
                self.assertTrue(release.wait(timeout=3))

        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(hold_transaction_lock)
            self.assertTrue(locked.wait(timeout=3))
            try:
                with unittest.mock.patch("local_bridge.server.finalize_topology_request") as finalize:
                    status = pool.submit(self.store.dispatch_status, self.dispatch).result(timeout=0.5)
                    takeover = pool.submit(self.store.worker_takeover_status, self.takeover).result(timeout=0.5)
                    finalize.assert_not_called()
                self.assertTrue(status["matched"])
                self.assertEqual(status["state"], "RUNNING")
                self.assertTrue(takeover["matched"])
                self.assertFalse(takeover["rollover_requested"])
                self.assertEqual(self.store._git.call_count, 3)
            finally:
                release.set()
            holder.result(timeout=3)

    def test_new_dispatch_suppression_read_refreshes_even_with_a_current_hint(self):
        self.store.record_git_commit(FIRST)

        def fresh(command, *args):
            if command == "fetch":
                return SimpleNamespace(stdout="")
            if command == "rev-parse":
                self.assertEqual(args, ("FETCH_HEAD",))
                return SimpleNamespace(stdout=SECOND)
            self.assertEqual((command, args), ("show", (SECOND + ":cl/observation.backend.json",)))
            return SimpleNamespace(stdout=json.dumps({
                "scope": "backend_execution", "task_id": "task-observation",
                "dispatch": {"dispatch_id": "dispatch-observation", "generation": 1,
                             "fence_token": "fence-observation", "state": "READY"},
            }))

        self.store._git = Mock(side_effect=fresh)
        result = self.store.dispatch_status({**self.dispatch, "observation": False})
        self.assertTrue(result["matched"])
        self.assertFalse(result["suppress"])
        self.assertEqual(self.store.peek_git_snapshot(), SECOND)
        self.assertEqual(self.store._git.call_count, 3)

    def test_background_batch_keeps_one_confirmed_commit_past_cache_expiry(self):
        def read(command, *args):
            if command == "fetch":
                return SimpleNamespace(stdout="")
            if command == "rev-parse":
                return SimpleNamespace(stdout=FIRST)
            self.assertEqual((command, args), ("show", (FIRST + ":cl/observation.backend.json",)))
            self.clock.now += 1.1
            return SimpleNamespace(stdout=json.dumps({
                "scope": "backend_execution", "task_id": "task-observation",
                "dispatch": {"dispatch_id": "dispatch-observation", "generation": 1,
                             "fence_token": "fence-observation", "state": "RUNNING"},
            }))

        self.store._git = Mock(side_effect=read)
        result = self.store.worker_observation_status({
            "client_id": "snapshot-observation", "project_id": "test-project",
            # A caller-supplied revision must never replace the confirmed SHA.
            "source_commit": SECOND,
            "workers": [{"identity_key": identity, "dispatch": {**self.dispatch, "source_commit": SECOND}}
                        for identity in ("exact-identity-1", "exact-identity-2")],
        })
        self.assertEqual(result["source_commit"], FIRST)
        self.assertEqual([item["identity_key"] for item in result["workers"]],
                         ["exact-identity-1", "exact-identity-2"])
        self.assertTrue(all(item["dispatch"]["matched"] for item in result["workers"]))
        self.assertIsNone(self.store.peek_git_snapshot())
        fetches = [call for call in self.store._git.call_args_list if call.args[0] == "fetch"]
        self.assertEqual(len(fetches), 1)

    def test_batch_liveness_receives_only_the_internal_confirmed_commit(self):
        self.store.record_git_commit(FIRST)
        with unittest.mock.patch("local_bridge.server.reconcile_dispatch_liveness", return_value={"ok": True}) as observe:
            self.store.worker_observation_status({
                "client_id": "snapshot-observation", "project_id": "test-project",
                "workers": [{"identity_key": "exact-identity", "liveness": {
                    "response_running": True, "response_ended": False,
                    "source_commit": SECOND,
                }}],
            })
        request = observe.call_args.args[1]
        self.assertTrue(request["observation"])
        self.assertEqual(observe.call_args.kwargs, {"_snapshot_sha": FIRST})

    def test_batch_rejects_response_end_before_any_fetch_or_transaction(self):
        self.store._git = Mock()
        with self.assertRaisesRegex(ValueError, "running response"):
            self.store.worker_observation_status({
                "client_id": "snapshot-observation", "project_id": "test-project",
                "workers": [{"identity_key": "exact-identity", "liveness": {
                    "response_running": True, "response_ended": True,
                }}],
            })
        self.store._git.assert_not_called()

    def test_read_scope_pins_ordinary_reads_but_force_and_known_push_advance_it(self):
        self.store._git = Mock(side_effect=lambda command, *args: SimpleNamespace(
            stdout=FIRST if command == "rev-parse" else ""))
        with self.store.git_snapshot_read_scope():
            self.assertEqual(self.store.read_git_snapshot(), FIRST)
            self.clock.now = 2.0
            self.assertEqual(self.store.read_git_snapshot(), FIRST)
            self.store.record_git_commit(SECOND)
            with self.store.git_snapshot_read_scope():
                self.assertEqual(self.store.read_git_snapshot(), SECOND)
            self.assertEqual(self.store.read_git_snapshot(force=True), FIRST)
            self.assertEqual(self.store.read_git_snapshot(), FIRST)
        fetches = [call for call in self.store._git.call_args_list if call.args[0] == "fetch"]
        self.assertEqual(len(fetches), 2)

    def test_read_scope_invalidation_requires_a_new_remote_read(self):
        self.store.record_git_commit(FIRST)
        self.store._git = Mock(side_effect=lambda command, *args: SimpleNamespace(
            stdout=SECOND if command == "rev-parse" else ""))
        with self.store.git_snapshot_read_scope():
            self.assertEqual(self.store.read_git_snapshot(), FIRST)
            self.store.invalidate_git_snapshot()
            self.assertEqual(self.store.read_git_snapshot(), SECOND)
        fetches = [call for call in self.store._git.call_args_list if call.args[0] == "fetch"]
        self.assertEqual(len(fetches), 1)

    def test_read_scope_does_not_pin_another_request_thread(self):
        self.store.record_git_commit(FIRST)
        self.store._git = Mock(side_effect=lambda command, *args: SimpleNamespace(
            stdout=SECOND if command == "rev-parse" else ""))
        pinned, release = threading.Event(), threading.Event()

        def scoped_reader():
            with self.store.git_snapshot_read_scope():
                self.assertEqual(self.store.read_git_snapshot(), FIRST)
                pinned.set()
                self.assertTrue(release.wait(timeout=3))
                return self.store.read_git_snapshot()

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(scoped_reader)
            self.assertTrue(pinned.wait(timeout=3))
            try:
                self.clock.now = 2.0
                self.assertEqual(self.store.read_git_snapshot(), SECOND)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3), FIRST)

    def test_control_poll_and_post_ui_status_share_one_read_past_ttl(self):
        seen = []

        def component(store, req):
            seen.append(store.read_git_snapshot())
            self.clock.now += 1.1
            return {"ok": True}

        def read(command, *args):
            if command == "rev-parse":
                return SimpleNamespace(stdout=FIRST)
            if command == "show":
                self.assertEqual(args, (FIRST + ":state/chatgpt.json",))
                return SimpleNamespace(stdout=json.dumps({"agent": "chatgpt"}))
            self.assertEqual(command, "fetch")
            return SimpleNamespace(stdout="")

        self.store._git = Mock(side_effect=read)
        with ExitStack() as patches:
            for operation in ("planner_runtime_tick", "planner_final_delivery_status", "reconcile_foreground_terminal"):
                patches.enter_context(unittest.mock.patch("local_bridge.server." + operation, side_effect=component))
            result = self.store.control_poll({"client_id": "snapshot-observation", "project_id": "test-project"})
        self.assertEqual(seen, [FIRST] * 3)
        self.assertEqual(result["source_commit"], FIRST)
        status = self.store.observed_control_status({
            "client_id": "snapshot-observation", "project_id": "test-project",
            "observed_source_commit": FIRST,
        })
        self.assertEqual(status["source_commit"], FIRST)
        fetches = [call for call in self.store._git.call_args_list if call.args[0] == "fetch"]
        self.assertEqual(len(fetches), 1)

    def test_post_ui_status_prefers_confirmed_write_and_never_client_revision(self):
        self.store.record_git_commit(FIRST)
        self.clock.now = 2.0
        self.store.record_git_commit(SECOND)
        self.store._git = Mock(return_value=SimpleNamespace(stdout=json.dumps({"agent": "chatgpt"})))
        status = self.store.observed_control_status({
            "client_id": "snapshot-observation", "project_id": "test-project",
            "observed_source_commit": "c" * 40,
        })
        self.assertEqual(status["source_commit"], SECOND)
        self.store._git.assert_called_once_with("show", SECOND + ":state/chatgpt.json")

    def test_post_ui_status_refreshes_after_unknown_write_outcome(self):
        self.store.record_git_commit(FIRST)
        self.store.invalidate_git_snapshot()

        def read(command, *args):
            if command == "rev-parse":
                return SimpleNamespace(stdout=SECOND)
            return SimpleNamespace(stdout=json.dumps({"agent": "chatgpt"}) if command == "show" else "")

        self.store._git = Mock(side_effect=read)
        status = self.store.observed_control_status({
            "client_id": "snapshot-observation", "project_id": "test-project",
            "observed_source_commit": FIRST,
        })
        self.assertEqual(status["source_commit"], SECOND)
        self.assertEqual(self.store._git.call_args_list[0].args[0], "fetch")

    def test_aggregated_component_error_still_reaches_existing_helper_mailbox(self):
        with ExitStack() as patches:
            patches.enter_context(unittest.mock.patch("local_bridge.server.planner_runtime_tick",
                return_value={"ok": False, "error": "PLANNER_COMPONENT_FAILED"}))
            for operation in ("planner_final_delivery_status", "reconcile_foreground_terminal"):
                patches.enter_context(unittest.mock.patch("local_bridge.server." + operation, return_value={"ok": True}))
            result = self.store.control_poll({"client_id": "snapshot-observation", "project_id": "test-project"})
        self.assertTrue(result["ok"])
        errors = self.store.harness_errors({"client_id": "snapshot-observation"})["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["event"], "bridge.planner_runtime_tick")
        self.assertEqual(errors[0]["data"]["message"], "PLANNER_COMPONENT_FAILED")


if __name__ == "__main__":
    unittest.main()
