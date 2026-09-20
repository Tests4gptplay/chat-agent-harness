import subprocess
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
