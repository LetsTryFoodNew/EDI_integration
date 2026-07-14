"""
Thread-safe SAP B1 session pool.

B1 Service Layer has a hard limit on concurrent sessions (license-based).
This pool:
  - Maintains at most `max_sessions` live sessions
  - Blocks callers when all sessions are busy, up to `acquire_timeout_s`
  - Expires sessions that are older than `session_ttl_s` (B1 default: 30 min)
  - Is safe to use from multiple RQ worker threads

Usage:
    pool = SessionPool(max_sessions=2, login_fn=client._login, logout_fn=client._logout)
    session_id = pool.acquire()
    try:
        # ... use session_id in HTTP requests ...
    finally:
        pool.release(session_id)
"""
from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


_SESSION_TTL_S = 29 * 60    # 29 min — expire 1 min before B1's 30-min timeout
_ACQUIRE_TIMEOUT_S = 30     # wait at most 30s for a free slot


@dataclass
class _SessionEntry:
    session_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used: datetime = field(default_factory=lambda: datetime.now(UTC))
    busy: bool = False

    def is_expired(self) -> bool:
        elapsed = (datetime.now(UTC) - self.last_used).total_seconds()
        return elapsed >= _SESSION_TTL_S


class SessionPool:
    """
    Pool of B1 sessions.

    `login_fn()` → new session_id   (called when a new session is needed)
    `logout_fn(session_id)` → None  (called when a session is removed)
    """

    def __init__(
        self,
        max_sessions: int,
        login_fn: Callable[[], str],
        logout_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._max = max(1, max_sessions)
        self._login_fn = login_fn
        self._logout_fn = logout_fn or (lambda _: None)
        self._sessions: list[_SessionEntry] = []
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)

    # ── Public API ────────────────────────────────────────────────────────────

    def acquire(self) -> str:
        """
        Return a session_id ready for use.
        Blocks until one is available or raises TimeoutError.
        """
        with self._available:
            deadline = datetime.now(UTC).timestamp() + _ACQUIRE_TIMEOUT_S

            while True:
                # Purge expired sessions first
                self._purge_expired()

                # Try to find a free session
                for entry in self._sessions:
                    if not entry.busy:
                        entry.busy = True
                        entry.last_used = datetime.now(UTC)
                        return entry.session_id

                # Can we create a new one?
                if len(self._sessions) < self._max:
                    session_id = self._login_fn()
                    entry = _SessionEntry(session_id=session_id, busy=True)
                    self._sessions.append(entry)
                    return session_id

                # All busy — wait
                remaining = deadline - datetime.now(UTC).timestamp()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Could not acquire a B1 session within {_ACQUIRE_TIMEOUT_S}s "
                        f"(pool size: {self._max})"
                    )
                self._available.wait(timeout=min(remaining, 2.0))

    def release(self, session_id: str) -> None:
        """Mark a session as available again."""
        with self._available:
            for entry in self._sessions:
                if entry.session_id == session_id:
                    entry.busy = False
                    entry.last_used = datetime.now(UTC)
                    self._available.notify_all()
                    return

    def invalidate(self, session_id: str) -> None:
        """Remove a session from the pool (e.g. after a 401 error)."""
        with self._available:
            self._sessions = [e for e in self._sessions if e.session_id != session_id]
            with contextlib.suppress(Exception):
                self._logout_fn(session_id)
            self._available.notify_all()

    def close_all(self) -> None:
        """Logout all sessions; used during graceful shutdown."""
        with self._available:
            for entry in self._sessions:
                with contextlib.suppress(Exception):
                    self._logout_fn(entry.session_id)
            self._sessions.clear()
            self._available.notify_all()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    @property
    def busy_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._sessions if e.busy)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _purge_expired(self) -> None:
        """Must be called with self._lock held."""
        expired = [e for e in self._sessions if not e.busy and e.is_expired()]
        for entry in expired:
            with contextlib.suppress(Exception):
                self._logout_fn(entry.session_id)
        self._sessions = [e for e in self._sessions if e not in expired]
