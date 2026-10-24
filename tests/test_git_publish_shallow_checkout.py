"""Real file-transport shallow checkout regressions for the Git publisher."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import git_publish
from harness.git_process import GitProcessTimeout
from harness.git_publish import publish


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, encoding="utf-8",
        stderr=subprocess.DEVNULL,
    ).strip()


def init_repo(base: Path) -> tuple[Path, Path]:
    origin, peer = base / "origin.git", base / "peer"
    git(base, "init", "--bare", str(origin))
    git(base, "clone", str(origin), str(peer))
    git(peer, "config", "user.name", "shallow-test")
    git(peer, "config", "user.email", "shallow-test@example.invalid")
    (peer / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(peer, "add", "seed.txt")
    git(peer, "commit", "-m", "seed")
    git(peer, "branch", "-M", "main")
    git(peer, "push", "origin", "main")
    git(base, "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main")
    return origin, peer


def local_commit(work: Path) -> str:
    (work / "next.txt").write_text("next\n", encoding="utf-8")
    git(work, "add", "next.txt")
    git(work, "commit", "-m", "next")
    return git(work, "rev-parse", "HEAD")


class ShallowGitPublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.origin, self.peer = init_repo(self.base)
        # Seed enough history that depth=2 cannot accidentally be a full clone.
        self.root_commit = git(self.peer, "rev-parse", "HEAD")
        for number in range(1, 6):
            (self.peer / "seed.txt").write_text(f"seed {number}\n", encoding="utf-8")
            git(self.peer, "add", "seed.txt")
            git(self.peer, "commit", "-m", f"seed {number}")
        git(self.peer, "push", "origin", "main")
        self.work = self.base / "shallow"
        # A plain local-path clone ignores --depth. file:// uses Git's transport
        # and creates the same real shallow boundary as actions/checkout.
        origin_url = self.origin.resolve().as_uri()
        git(self.base, "clone", "--quiet", "--depth", "2", "--branch", "main", origin_url, str(self.work))
        git(self.work, "config", "user.name", "shallow-test")
        git(self.work, "config", "user.email", "shallow-test@example.invalid")
        self.assertEqual(git(self.work, "config", "--get", "remote.origin.url"), origin_url)
        self.assertEqual(git(self.work, "rev-parse", "--is-shallow-repository"), "true")
        self.assertEqual(git(self.work, "rev-list", "--count", "HEAD"), "2")
        absent_root = subprocess.run(
            ["git", "-C", str(self.work), "cat-file", "-e", self.root_commit],
            capture_output=True, text=True,
        )
        self.assertNotEqual(absent_root.returncode, 0, "old history must really be absent")

    def advance_peer(self, name: str) -> str:
        (self.peer / name).write_text(f"{name}\n", encoding="utf-8")
        git(self.peer, "add", name)
        git(self.peer, "commit", "-m", name)
        git(self.peer, "push", "origin", "main")
        return git(self.peer, "rev-parse", "HEAD")

    def test_depth_two_sync_fast_forwards_over_remote_advance_without_push(self):
        self.advance_peer("peer-first.txt")
        remote_head = self.advance_peer("peer-second.txt")
        real_run = git_publish.bounded_run_git
        commands = []

        def observe(repo, *args, **kwargs):
            commands.append(args[0])
            return real_run(repo, *args, **kwargs)

        with patch.object(git_publish, "bounded_run_git", side_effect=observe):
            result = publish(self.work, remote="origin", branch="main", sync_only=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["sync_only"])
        self.assertNotIn("push", commands)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), remote_head)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/main"), remote_head)
        self.assertTrue((self.work / "peer-first.txt").is_file())
        self.assertTrue((self.work / "peer-second.txt").is_file())
        self.assertEqual(git(self.work, "rev-parse", "--is-shallow-repository"), "true")

    def test_depth_two_rebases_local_commit_over_concurrent_remote_advance(self):
        original_head = local_commit(self.work)
        self.advance_peer("peer-first.txt")
        peer_head = self.advance_peer("peer-second.txt")

        result = publish(self.work, remote="origin", branch="main", gah_owned_workspace=True)

        head = git(self.work, "rev-parse", "HEAD")
        self.assertTrue(result["ok"])
        self.assertNotEqual(head, original_head, "the local commit must really be rebased")
        self.assertEqual(git(self.work, "rev-parse", "HEAD^"), peer_head)
        self.assertEqual(git(self.origin, "rev-parse", "refs/heads/main"), head)
        self.assertEqual((self.work / "next.txt").read_text(encoding="utf-8"), "next\n")
        self.assertTrue((self.work / "peer-first.txt").is_file())
        self.assertTrue((self.work / "peer-second.txt").is_file())
        self.assertEqual(git(self.work, "rev-list", "--count", f"{peer_head}..HEAD"), "1")
        self.assertEqual(git(self.work, "rev-parse", "--is-shallow-repository"), "true")

    def test_depth_two_confirms_accepted_push_with_lost_ack_and_later_peer_commit(self):
        head = local_commit(self.work)
        real_run = git_publish.bounded_run_git
        pushes = []

        def accept_then_lose_ack(repo, *args, **kwargs):
            result = real_run(repo, *args, **kwargs)
            if args[0] == "push":
                pushes.append(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(git(self.origin, "rev-parse", "refs/heads/main"), head)
                git(self.peer, "pull", "--ff-only", "origin", "main")
                self.advance_peer("after-accepted-push.txt")
                raise GitProcessTimeout(["git", *args], 20, outcome_unknown=True)
            return result

        with patch.object(git_publish, "bounded_run_git", side_effect=accept_then_lose_ack):
            result = publish(self.work, remote="origin", branch="main", attempts=1)

        remote_head = git(self.origin, "rev-parse", "refs/heads/main")
        self.assertTrue(result["ok"])
        self.assertTrue(result["push_reconciled"])
        self.assertEqual(len(pushes), 1, "confirmation must not publish the effect again")
        self.assertNotEqual(remote_head, head)
        git(self.work, "merge-base", "--is-ancestor", head, remote_head)
        self.assertEqual(git(self.work, "rev-parse", "--is-shallow-repository"), "true")


if __name__ == "__main__":
    unittest.main()
