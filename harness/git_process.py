"""Bounded non-interactive Git subprocesses for GAH-owned control transactions."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

_MUTATING_COMMANDS = {"add", "commit", "config", "push", "reset", "worktree"}


class GitProcessTimeout(subprocess.TimeoutExpired):
    """A bounded Git command exceeded its deadline.

    outcome_unknown is true for commands that may already have changed local
    or remote state. Callers must re-read canonical state before retrying an
    idempotent transaction, especially after a timed-out push.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        timeout: float,
        *,
        output=None,
        stderr=None,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.outcome_unknown = bool(outcome_unknown)


def run_git(
    root: Path,
    *args: str,
    timeout: float = 20,
    binary: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    if not 0 < timeout <= 120:
        raise ValueError("Git timeout must be in (0, 120] seconds")
    if not args:
        raise ValueError("Git command required")

    env = os.environ.copy()
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GCM_INTERACTIVE="Never",
        GIT_PAGER="cat",
        GIT_EDITOR="true",
    )
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oConnectTimeout=10")
    kwargs = {} if binary else {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    cmd = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(
            cmd,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitProcessTimeout(
            cmd,
            timeout,
            output=exc.output,
            stderr=exc.stderr,
            outcome_unknown=str(args[0]).lower() in _MUTATING_COMMANDS,
        ) from exc
