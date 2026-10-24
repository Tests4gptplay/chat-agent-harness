import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import git_publish
from harness.git_process import GitProcessTimeout
from harness.git_publish import GitPublishError, publish


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
    subprocess.check_call(["git", "-C", str(work), "config", "user.name", "test"])
    subprocess.check_call(["git", "-C", str(work), "config", "user.email", "test@example.invalid"])
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.check_call(["git", "-C", str(work), "add", "seed.txt"])
    subprocess.check_call(["git", "-C", str(work), "commit", "-m", "seed"], stdout=subprocess.DEVNULL)
    subprocess.check_call(["git", "-C", str(work), "branch", "-M", "main"])
    subprocess.check_call(["git", "-C", str(work), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"])
    return origin, work


class GitPublishTests(unittest.TestCase):
    @staticmethod
    def local_commit(work: Path) -> str:
        (work / "next.txt").write_text("next\n", encoding="utf-8")
        git(work, "add", "next.txt")
        git(work, "commit", "-m", "next")
        return git(work, "rev-parse", "HEAD")

    def test_recovers_stale_gah_owned_rebase_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _origin, work = init_repo(base)
            (work / "next.txt").write_text("next\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "next.txt"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "next"], stdout=subprocess.DEVNULL)

            gd = Path(git(work, "rev-parse", "--git-dir"))
            if not gd.is_absolute():
                gd = work / gd
            stale = gd / "rebase-merge"
            stale.mkdir(parents=True)
            (stale / "stale-marker").write_text("stale\n", encoding="utf-8")

            out = publish(
                work,
                remote="origin",
                branch="main",
                attempts=3,
                gah_owned_workspace=True,
            )
            self.assertTrue(out["ok"])
            self.assertTrue(out["recovery"]["recovered"])
            self.assertFalse(stale.exists())
            self.assertEqual(git(work, "rev-parse", "HEAD"), git(work, "rev-parse", "origin/main"))

    def test_refuses_metadata_cleanup_without_owned_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _origin, work = init_repo(base)
            gd = Path(git(work, "rev-parse", "--git-dir"))
            if not gd.is_absolute():
                gd = work / gd
            stale = gd / "rebase-merge"
            stale.mkdir(parents=True)
            (stale / "stale-marker").write_text("stale\n", encoding="utf-8")

            with self.assertRaises(GitPublishError):
                publish(
                    work,
                    remote="origin",
                    branch="main",
                    attempts=1,
                    gah_owned_workspace=False,
                )

    def test_sync_only_rebases_without_pushing_local_commit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin, work = init_repo(base)
            peer = base / "peer"
            subprocess.check_call(["git", "clone", str(origin), str(peer)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(peer), "config", "user.name", "peer"])
            subprocess.check_call(["git", "-C", str(peer), "config", "user.email", "peer@example.invalid"])

            (peer / "peer.txt").write_text("peer\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(peer), "add", "peer.txt"])
            subprocess.check_call(["git", "-C", str(peer), "commit", "-m", "peer"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(peer), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            out = publish(
                work,
                remote="origin",
                branch="main",
                attempts=3,
                gah_owned_workspace=True,
                sync_only=True,
            )
            self.assertTrue(out["ok"])
            self.assertTrue(out["sync_only"])
            self.assertTrue((work / "peer.txt").exists())
            self.assertEqual(git(work, "rev-parse", "HEAD"), git(work, "rev-parse", "origin/main"))

    def test_rebases_over_concurrent_remote_advance(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin, work = init_repo(base)
            peer = base / "peer"
            subprocess.check_call(["git", "clone", str(origin), str(peer)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(peer), "config", "user.name", "peer"])
            subprocess.check_call(["git", "-C", str(peer), "config", "user.email", "peer@example.invalid"])

            (work / "work.txt").write_text("work\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(work), "add", "work.txt"])
            subprocess.check_call(["git", "-C", str(work), "commit", "-m", "work"], stdout=subprocess.DEVNULL)

            (peer / "peer.txt").write_text("peer\n", encoding="utf-8")
            subprocess.check_call(["git", "-C", str(peer), "add", "peer.txt"])
            subprocess.check_call(["git", "-C", str(peer), "commit", "-m", "peer"], stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(peer), "push", "origin", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            out = publish(
                work,
                remote="origin",
                branch="main",
                attempts=3,
                gah_owned_workspace=True,
            )
            self.assertTrue(out["ok"])
            self.assertTrue((work / "peer.txt").exists())
            self.assertTrue((work / "work.txt").exists())
            self.assertEqual(git(work, "rev-parse", "HEAD"), git(work, "rev-parse", "origin/main"))

    def test_push_timeout_after_acceptance_is_confirmed_without_another_push(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = init_repo(Path(td))
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git
            pushes = []

            def lose_ack(repo, *args, **kwargs):
                result = real_run(repo, *args, **kwargs)
                if args[0] == "push":
                    pushes.append(args)
                    raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
                return result

            with patch.object(git_publish, "bounded_run_git", side_effect=lose_ack):
                result = publish(work, remote="origin", branch="main", attempts=1)

            self.assertTrue(result["ok"])
            self.assertTrue(result["push_reconciled"])
            self.assertEqual(len(pushes), 1)
            self.assertEqual(git(origin, "rev-parse", "refs/heads/main"), head)

    def test_push_timeout_before_acceptance_retries_after_remote_readback(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = init_repo(Path(td))
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git
            calls = []
            pushes = 0

            def timeout_once(repo, *args, **kwargs):
                nonlocal pushes
                calls.append(args[0])
                if args[0] == "push":
                    pushes += 1
                    if pushes == 1:
                        raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
                return real_run(repo, *args, **kwargs)

            with patch.object(git_publish, "bounded_run_git", side_effect=timeout_once):
                result = publish(work, remote="origin", branch="main", attempts=2)

            first_push = calls.index("push")
            second_push = calls.index("push", first_push + 1)
            self.assertIn("fetch", calls[first_push + 1:second_push])
            self.assertIn("merge-base", calls[first_push + 1:second_push])
            self.assertEqual(result["attempt"], 2)
            self.assertEqual(pushes, 2)
            self.assertEqual(git(origin, "rev-parse", "refs/heads/main"), head)

    def test_lost_ack_is_confirmed_when_peer_has_advanced_beyond_the_commit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin, work = init_repo(base)
            peer = base / "peer"
            git(base, "clone", str(origin), str(peer))
            git(peer, "config", "user.name", "peer")
            git(peer, "config", "user.email", "peer@example.invalid")
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git
            pushes = 0

            def accept_then_advance(repo, *args, **kwargs):
                nonlocal pushes
                result = real_run(repo, *args, **kwargs)
                if args[0] == "push":
                    pushes += 1
                    git(peer, "pull", "--ff-only")
                    (peer / "peer.txt").write_text("peer\n", encoding="utf-8")
                    git(peer, "add", "peer.txt")
                    git(peer, "commit", "-m", "peer")
                    git(peer, "push", "origin", "main")
                    raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
                return result

            with patch.object(git_publish, "bounded_run_git", side_effect=accept_then_advance):
                result = publish(work, remote="origin", branch="main", attempts=1)

            remote_head = git(origin, "rev-parse", "refs/heads/main")
            self.assertNotEqual(remote_head, head)
            self.assertTrue(result["push_reconciled"])
            self.assertEqual(pushes, 1)
            git(origin, "merge-base", "--is-ancestor", head, remote_head)

    def test_unknown_push_outcome_does_not_retry_when_readback_is_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = init_repo(Path(td))
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git
            pushes = 0

            def lose_ack_and_readback(repo, *args, **kwargs):
                nonlocal pushes
                if args[0] == "fetch" and pushes:
                    return subprocess.CompletedProcess(["git", *args], 128, "", "remote unavailable")
                result = real_run(repo, *args, **kwargs)
                if args[0] == "push":
                    pushes += 1
                    raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
                return result

            with patch.object(git_publish, "bounded_run_git", side_effect=lose_ack_and_readback):
                with self.assertRaises(GitPublishError) as raised:
                    publish(work, remote="origin", branch="main", attempts=3)

            self.assertTrue(raised.exception.outcome_unknown)
            self.assertEqual(pushes, 1)
            self.assertEqual(git(origin, "rev-parse", "refs/heads/main"), head)

    def test_push_readback_is_not_confused_by_another_fetch_head(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = init_repo(Path(td))
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git
            pushes = 0

            def overwrite_fetch_head(repo, *args, **kwargs):
                nonlocal pushes
                if args[0] == "push":
                    pushes += 1
                    raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
                result = real_run(repo, *args, **kwargs)
                if args[0] == "fetch" and pushes:
                    # An unrelated fetch sees our local, unpublished commit.
                    real_run(repo, "fetch", "--quiet", ".", "HEAD")
                return result

            with patch.object(git_publish, "bounded_run_git", side_effect=overwrite_fetch_head):
                with self.assertRaises(GitPublishError) as raised:
                    publish(work, remote="origin", branch="main", attempts=1)

            self.assertTrue(raised.exception.outcome_unknown)
            self.assertEqual(pushes, 1)
            self.assertNotEqual(git(origin, "rev-parse", "refs/heads/main"), head)

    def test_non_fast_forward_rejection_retries_with_the_peer_commit(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin, work = init_repo(base)
            peer = base / "peer"
            git(base, "clone", str(origin), str(peer))
            git(peer, "config", "user.name", "peer")
            git(peer, "config", "user.email", "peer@example.invalid")
            self.local_commit(work)
            real_run = git_publish.bounded_run_git
            pushes = 0

            def advance_before_first_push(repo, *args, **kwargs):
                nonlocal pushes
                if args[0] == "push":
                    pushes += 1
                    if pushes == 1:
                        (peer / "peer.txt").write_text("peer\n", encoding="utf-8")
                        git(peer, "add", "peer.txt")
                        git(peer, "commit", "-m", "peer")
                        git(peer, "push", "origin", "main")
                return real_run(repo, *args, **kwargs)

            with patch.object(git_publish, "bounded_run_git", side_effect=advance_before_first_push):
                result = publish(work, remote="origin", branch="main", attempts=2)

            self.assertEqual(result["attempt"], 2)
            self.assertEqual(pushes, 2)
            self.assertTrue((work / "peer.txt").exists())
            self.assertEqual(git(work, "rev-parse", "HEAD"), git(origin, "rev-parse", "refs/heads/main"))

    def test_exhausted_push_timeout_preserves_uncertainty(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = init_repo(Path(td))
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git

            def no_ack(repo, *args, **kwargs):
                if args[0] == "push":
                    raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
                return real_run(repo, *args, **kwargs)

            with patch.object(git_publish, "bounded_run_git", side_effect=no_ack):
                with self.assertRaises(GitPublishError) as raised:
                    publish(work, remote="origin", branch="main", attempts=1)

            self.assertTrue(raised.exception.outcome_unknown)
            self.assertNotEqual(git(origin, "rev-parse", "refs/heads/main"), head)

    def test_nonzero_push_with_lost_reply_is_also_confirmed(self):
        with tempfile.TemporaryDirectory() as td:
            origin, work = init_repo(Path(td))
            head = self.local_commit(work)
            real_run = git_publish.bounded_run_git
            pushes = 0

            def broken_reply(repo, *args, **kwargs):
                nonlocal pushes
                result = real_run(repo, *args, **kwargs)
                if args[0] == "push":
                    pushes += 1
                    return subprocess.CompletedProcess(
                        ["git", *args], 128, "", "RPC failed; failed to push some refs"
                    )
                return result

            with patch.object(git_publish, "bounded_run_git", side_effect=broken_reply):
                result = publish(work, remote="origin", branch="main", attempts=1)

            self.assertTrue(result["push_reconciled"])
            self.assertEqual(pushes, 1)
            self.assertEqual(git(origin, "rev-parse", "refs/heads/main"), head)

    def test_rebase_timeout_is_bounded_noninteractive_and_outcome_unknown(self):
        with patch("harness.git_process.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 20)) as run:
            with self.assertRaises(GitPublishError) as raised:
                git_publish.run_git(Path("unused"), "rebase", "origin/main")

        self.assertTrue(raised.exception.outcome_unknown)
        options = run.call_args.kwargs
        self.assertEqual(options["timeout"], 20)
        self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(options["env"]["GCM_INTERACTIVE"], "Never")

    def test_direct_script_entrypoint_works_outside_the_project_directory(self):
        with tempfile.TemporaryDirectory() as td:
            _origin, work = init_repo(Path(td))
            script = Path(git_publish.__file__).resolve()
            result = subprocess.run(
                [sys.executable, str(script), "--repo", str(work), "--branch", "main", "--sync-only"],
                cwd=td, text=True, capture_output=True, timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(json.loads(result.stdout)["sync_only"])


if __name__ == "__main__":
    unittest.main()
