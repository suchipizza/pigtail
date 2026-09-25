"""One advisory lock per job, so runs never overlap (M1-T21).

`PgJobLocks` takes a session-level `pg_try_advisory_lock` on a dedicated connection and holds it
for the whole run. It is released when the run ends or, if the scheduler process dies, when
Postgres drops the connection. This protects against overlap within one process (a slow run
still going when the next one is due) and across processes (two schedulers on one database).
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

LOCK_NAMESPACE = "pigtail.scheduler:"


def lock_key(job: str) -> int:
    """Stable signed 64-bit key for `pg_try_advisory_lock(bigint)`."""
    digest = hashlib.sha256((LOCK_NAMESPACE + job).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class JobLocks(Protocol):
    def hold(self, job: str) -> AbstractContextManager[bool]:
        """Yield True if the lock was taken (and hold it until exit), False if it is busy."""
        ...


class MemoryJobLocks:
    """Process-local locks (tests)."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._mu = threading.Lock()

    @contextmanager
    def hold(self, job: str) -> Iterator[bool]:
        with self._mu:
            if job in self._held:
                got = False
            else:
                self._held.add(job)
                got = True
        try:
            yield got
        finally:
            if got:
                with self._mu:
                    self._held.discard(job)


class PgJobLocks:
    def __init__(self, conninfo: str, connect_timeout: int = 5) -> None:
        self.conninfo = conninfo
        self.connect_timeout = connect_timeout

    @contextmanager
    def hold(self, job: str) -> Iterator[bool]:
        import psycopg

        key = lock_key(job)
        with psycopg.connect(
            self.conninfo, autocommit=True, connect_timeout=self.connect_timeout
        ) as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()
            got = bool(row and row[0])
            try:
                yield got
            finally:
                if got and not conn.closed:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
