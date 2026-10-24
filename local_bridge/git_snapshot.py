"""One bounded, rebuildable canonical Git read snapshot for a Bridge store."""
from __future__ import annotations

import re
import time
from typing import Any, Callable


class GitSnapshot:
    """Coalesce reads under the store's existing Git transaction lock.

    This cache is not an authorization or transaction boundary. Mutations still
    fetch and compare canonical state in their original transactions. A caller
    that has already confirmed a push can publish its immutable commit directly.
    """

    def __init__(
        self,
        refresh: Callable[[], str],
        lock: Any,
        *,
        interval: float = 1.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if interval < 0:
            raise ValueError("snapshot interval must be nonnegative")
        self._refresh = refresh
        self._lock = lock
        self._interval = float(interval)
        self._clock = clock or time.monotonic
        self._sha: str | None = None
        self._hint: tuple[str, float] | None = None
        self._expires_at = 0.0
        self._retry_at = 0.0
        self._failures = 0
        self._error: Exception | None = None
        self._version = 0

    @staticmethod
    def _commit(sha: str) -> str:
        value = str(sha).strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value) is None:
            raise ValueError("snapshot requires an immutable full Git commit")
        return value

    def _record(self, sha: str) -> str:
        self._sha = self._commit(sha)
        self._expires_at = self._clock() + self._interval
        self._hint = (self._sha, self._expires_at)
        self._retry_at = 0.0
        self._failures = 0
        self._error = None
        self._version += 1
        return self._sha

    def read(self, *, force: bool = False) -> str:
        # A caller queued behind a refresh uses that refresh, including its
        # failure, rather than starting another identical remote request.
        version = self._version
        with self._lock:
            now = self._clock()
            if force and self._version != version:
                if self._error is not None:
                    raise self._error
                if self._sha is not None:
                    return self._sha
            if not force and self._sha is not None and now < self._expires_at:
                return self._sha
            if not force and self._error is not None and now < self._retry_at:
                raise self._error
            try:
                return self._record(self._refresh())
            except Exception as exc:
                self._hint = None
                self._version += 1
                self._failures += 1
                self._expires_at = 0.0
                # Backoff only normal monitoring. A transaction explicitly
                # requesting fresh state retains its own bounded retry rules.
                self._retry_at = self._clock() + min(30.0, 2.0 ** min(self._failures - 1, 5))
                self._error = exc
                raise

    def peek(self) -> str | None:
        """Read an unexpired confirmed hint without waiting for a Git lock.

        Routine physical observation may use this value. Missing/expired data
        does not establish a new dispatch's admission or authorize side effects.
        """
        hint = self._hint
        if hint is not None and self._clock() < hint[1]:
            return hint[0]
        return None

    def confirmed(self) -> str | None:
        """Return the last confirmed revision for a continuing read-only poll.

        Unlike ``peek``, this may outlive the refresh interval. It is only for
        continuing an already-started pure read; admission and mutations never
        use it. An unknown outcome or failed refresh removes the value.
        """
        hint = self._hint
        return hint[0] if hint is not None else None

    def record(self, sha: str) -> None:
        """Publish the exact commit from an already-confirmed local push."""
        with self._lock:
            self._record(sha)

    def invalidate(self) -> None:
        """Discard a hint whose remote outcome is not confirmed."""
        with self._lock:
            self._hint = None
            self._sha = None
            self._expires_at = 0.0
            self._version += 1
