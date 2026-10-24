#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .git_process import GitProcessTimeout, run_git as bounded_run_git
except ImportError:
    # The Windows workflows also invoke this file directly by its path.
    from git_process import GitProcessTimeout, run_git as bounded_run_git


class GitPublishError(RuntimeError):
    def __init__(self, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.outcome_unknown = bool(outcome_unknown)


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        proc = bounded_run_git(repo, *args, check=False)
    except GitProcessTimeout as exc:
        raise GitPublishError(
            f"git {' '.join(args)} timed out after {exc.timeout:g} seconds",
            outcome_unknown=exc.outcome_unknown,
        ) from exc
    if check and proc.returncode != 0:
        raise GitPublishError(
            f"git {' '.join(args)} failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}"
        )
    return proc


def git_dir(repo: Path) -> Path:
    raw = run_git(repo, "rev-parse", "--git-dir").stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = (repo / path).resolve()
    return path


def recover_stale_rebase(repo: Path, *, gah_owned_workspace: bool) -> dict[str, Any]:
    gd = git_dir(repo)
    rebase_dirs = [gd / "rebase-merge", gd / "rebase-apply"]
    present = [p for p in rebase_dirs if p.exists()]
    if not present:
        return {"recovered": False, "method": None}

    aborted = run_git(repo, "rebase", "--abort", check=False)
    if not any(p.exists() for p in rebase_dirs):
        return {
            "recovered": True,
            "method": "git_rebase_abort",
            "abort_returncode": aborted.returncode,
        }

    if not gah_owned_workspace:
        raise GitPublishError(
            "stale rebase metadata remains and workspace is not explicitly GAH-owned"
        )

    active = run_git(repo, "rev-parse", "-q", "--verify", "REBASE_HEAD", check=False)
    if active.returncode == 0:
        raise GitPublishError(
            "rebase metadata remains with active REBASE_HEAD; refusing destructive cleanup"
        )

    removed: list[str] = []
    for path in rebase_dirs:
        if path.exists():
            shutil.rmtree(path)
            removed.append(path.name)

    if any(p.exists() for p in rebase_dirs):
        raise GitPublishError("failed to remove stale GAH-owned rebase metadata")

    return {
        "recovered": True,
        "method": "gah_owned_metadata_cleanup",
        "removed": removed,
        "abort_returncode": aborted.returncode,
    }


def ensure_clean(repo: Path) -> None:
    proc = run_git(repo, "status", "--porcelain", "--untracked-files=no")
    dirty = [line for line in proc.stdout.splitlines() if line.strip()]
    if dirty:
        raise GitPublishError(
            "publish requires a clean committed working tree; remaining entries: "
            + "; ".join(dirty[:20])
        )


def push_rejected(proc: subprocess.CompletedProcess[str]) -> bool:
    text = f"{proc.stdout}\n{proc.stderr}".lower()
    needles = (
        "non-fast-forward",
        "[rejected]",
        "fetch first",
    )
    return any(n in text for n in needles)


def remote_contains_commit(repo: Path, remote: str, branch: str, head: str) -> bool:
    """Resolve an ambiguous push through Git before retrying its publication."""
    try:
        tracking_ref = f"refs/remotes/{remote}/{branch}"
        run_git(repo, "fetch", "--quiet", "--no-tags", remote, f"+refs/heads/{branch}:{tracking_ref}")
        remote_head = run_git(repo, "rev-parse", tracking_ref).stdout.strip()
        present = run_git(repo, "merge-base", "--is-ancestor", head, remote_head, check=False)
        if present.returncode not in (0, 1):
            raise GitPublishError((present.stderr or present.stdout).strip())
    except GitPublishError as exc:
        raise GitPublishError(
            f"could not confirm the outcome of the previous push: {exc}",
            outcome_unknown=True,
        ) from exc
    return present.returncode == 0


def publish(
    repo: Path,
    *,
    remote: str,
    branch: str,
    attempts: int = 3,
    gah_owned_workspace: bool = False,
    sync_only: bool = False,
) -> dict[str, Any]:
    repo = repo.resolve()
    if attempts < 1 or attempts > 10:
        raise GitPublishError("attempts must be between 1 and 10")

    recovery = recover_stale_rebase(repo, gah_owned_workspace=gah_owned_workspace)
    ensure_clean(repo)

    last_push: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        run_git(repo, "fetch", "--quiet", "--no-tags", remote, branch)

        rebase = run_git(repo, "rebase", f"{remote}/{branch}", check=False)
        if rebase.returncode != 0:
            run_git(repo, "rebase", "--abort", check=False)
            raise GitPublishError(
                f"git rebase {remote}/{branch} failed on attempt {attempt}: "
                f"{(rebase.stderr or rebase.stdout).strip()}"
            )

        head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
        if sync_only:
            return {
                "ok": True,
                "attempt": attempt,
                "head": head,
                "recovery": recovery,
                "sync_only": True,
            }

        try:
            push = run_git(repo, "push", remote, f"HEAD:{branch}", check=False)
        except GitPublishError as exc:
            if not exc.outcome_unknown:
                raise
            if remote_contains_commit(repo, remote, branch, head):
                return {
                    "ok": True,
                    "attempt": attempt,
                    "head": head,
                    "recovery": recovery,
                    "sync_only": False,
                    "push_reconciled": True,
                }
            if attempt < attempts:
                continue
            raise GitPublishError(
                f"git push timed out on attempt {attempt}; commit {head} was not confirmed remotely",
                outcome_unknown=True,
            ) from exc
        last_push = push
        if push.returncode == 0:
            return {
                "ok": True,
                "attempt": attempt,
                "head": head,
                "recovery": recovery,
                "sync_only": False,
            }

        if not push_rejected(push):
            # A broken connection can report failure after the remote accepted
            # the commit. An explicit non-fast-forward rejection needs only the
            # existing bounded fetch/rebase retry on the next attempt.
            if remote_contains_commit(repo, remote, branch, head):
                return {
                    "ok": True,
                    "attempt": attempt,
                    "head": head,
                    "recovery": recovery,
                    "sync_only": False,
                    "push_reconciled": True,
                }
            break
        if attempt >= attempts:
            break

    detail = ""
    if last_push is not None:
        detail = (last_push.stderr or last_push.stdout).strip()
    raise GitPublishError(f"git push failed after {attempts} attempt(s): {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a committed CAH Git transaction safely")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--gah-owned-workspace", action="store_true")
    parser.add_argument("--sync-only", action="store_true")
    args = parser.parse_args()

    try:
        out = publish(
            Path(args.repo),
            remote=args.remote,
            branch=args.branch,
            attempts=args.attempts,
            gah_owned_workspace=args.gah_owned_workspace,
            sync_only=args.sync_only,
        )
    except GitPublishError as exc:
        error: dict[str, Any] = {"ok": False, "error": str(exc)}
        if exc.outcome_unknown:
            error["outcome_unknown"] = True
        print(json.dumps(error, ensure_ascii=False))
        return 1

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
