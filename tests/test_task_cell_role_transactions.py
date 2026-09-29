import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_bridge.server import WakeStore
from local_bridge.task_cell_roles import (
    _drop_exact_worktree,
    _prepare_state_worktree,
    _worktree_registered,
)


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class RepoFixture:
    def __init__(self, root: Path):
        self.root = root
        self.remote = root / "remote.git"
        self.repo = root / "repo"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "checkout", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "remote", "add", "origin", str(self.remote))

    def write(self, rel: str, value) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, (dict, list)):
            p.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        else:
            p.write_text(str(value), encoding="utf-8")

    def commit_push(self, msg: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", msg)
        git(self.repo, "push", "-u", "origin", "main")
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def remote_json(self, rel: str):
        raw = subprocess.run(
            ["git", "--git-dir", str(self.remote), "show", f"main:{rel}"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        ).stdout
        return json.loads(raw)


class TaskCellRoleTransactionTests(unittest.TestCase):
    def fixture(self, root: Path) -> RepoFixture:
        f = RepoFixture(root)
        f.write("state/chatgpt.json", {"v": 1, "control_request": None})
        f.write("state/keep.json", {"keep": True})
        # Materially larger unrelated tree proves sparse preparation does not
        # populate normal repository content.
        f.write("big/deep/unrelated.txt", "x" * (1024 * 1024))
        f.commit_push("seed")
        return f

    def test_state_worktree_materializes_only_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = self.fixture(root)
            store = WakeStore(root / "bridge", f.repo)
            store._git("fetch", "--quiet", "--no-tags", "origin", "main")
            wt = store.runtime / "task-cell-role-test-state-only-0"
            _prepare_state_worktree(store, wt)
            try:
                self.assertTrue((wt / "state" / "chatgpt.json").is_file())
                self.assertTrue((wt / "state" / "keep.json").is_file())
                self.assertFalse((wt / "big").exists())
                self.assertTrue(_worktree_registered(store, wt))
            finally:
                _drop_exact_worktree(store, wt)
            self.assertFalse(wt.exists())
            self.assertFalse(_worktree_registered(store, wt))

    def test_prepare_recovers_missing_directory_with_stale_registration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = self.fixture(root)
            store = WakeStore(root / "bridge", f.repo)
            store._git("fetch", "--quiet", "--no-tags", "origin", "main")
            wt = store.runtime / "task-cell-role-stale-recovery-0"
            store._git("worktree", "add", "--force", "--no-checkout", "--detach", str(wt), "FETCH_HEAD")
            self.assertTrue(_worktree_registered(store, wt))
            shutil.rmtree(wt)
            self.assertTrue(_worktree_registered(store, wt))
            _prepare_state_worktree(store, wt)
            try:
                self.assertTrue((wt / "state" / "chatgpt.json").is_file())
                self.assertFalse((wt / "big").exists())
            finally:
                _drop_exact_worktree(store, wt)
            self.assertFalse(_worktree_registered(store, wt))

if __name__ == "__main__":
    unittest.main()
