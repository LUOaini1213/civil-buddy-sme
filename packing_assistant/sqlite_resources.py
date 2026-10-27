"""Owned SQLite handles with deferred retirement of borrowed cursors.

Retirement refuses new cursors, but an already borrowed cursor can finish.
The connection is actually closed (and uncommitted work rolled back) when
the last cursor closes or is collected. No warning filters are installed.
"""
from __future__ import annotations

import sqlite3
import threading
import weakref


class ManagedCursor(sqlite3.Cursor):
    def _admit_statement(self):
        # Check admission briefly, not for the whole SQL call: retirement
        # must be able to run while an admitted statement is still executing.
        with self.connection._resource_lock:
            if self.connection._retired or self.connection.closed:
                raise sqlite3.ProgrammingError("Cannot operate on a retired database.")

    def execute(self, sql, parameters=()):
        self._admit_statement()
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        self._admit_statement()
        return super().executemany(sql, parameters)

    def executescript(self, sql):
        self._admit_statement()
        return super().executescript(sql)

    def close(self) -> None:
        if getattr(self, "_explicitly_closed", False):
            return
        try:
            super().close()
        finally:
            self._explicitly_closed = True
            lease = getattr(self, "_lease", None)
            if lease is not None:
                lease()


class ManagedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        self._resource_lock = threading.RLock()
        self._cursor_count = 0
        self._retired = self._closed = False
        super().__init__(*args, **kwargs)

    @property
    def closed(self) -> bool:
        return self._closed

    def cursor(self, factory=ManagedCursor):
        with self._resource_lock:
            if self._retired or self._closed:
                raise sqlite3.ProgrammingError("Cannot operate on a retired database.")
            cursor = super().cursor(factory)
            self._cursor_count += 1
            lease = weakref.finalize(cursor, self._release_cursor)
            # A finalizer at interpreter exit must not pretend a live cursor
            # was released. Its real close/collection releases the lease.
            lease.atexit = False
            if isinstance(cursor, ManagedCursor):
                cursor._lease = lease
            return cursor

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql):
        return self.cursor().executescript(sql)

    def _release_cursor(self) -> None:
        with self._resource_lock:
            self._cursor_count -= 1
            if self._retired and not self._cursor_count and not self._closed:
                self.close()

    def retire(self) -> None:
        """Release ownership without interrupting a borrowed live cursor."""
        with self._resource_lock:
            self._retired = True
            if not self._cursor_count and not self._closed:
                self.close()

    def close(self) -> None:
        """Explicit SQLite close still closes immediately, without committing."""
        with self._resource_lock:
            if not self._closed:
                super().close()
                self._retired = self._closed = True

    def __del__(self):
        try:
            self.close()
        except Exception:
            # Partially initialized handles still reach SQLite's own cleanup;
            # exceptions here cannot be usefully raised from finalization.
            pass


class ThreadConnectionOwner:
    """A thread-local owner; dying threads retire only their own handle."""
    def __init__(self, connection: ManagedConnection):
        self.connection = connection

    def __del__(self):
        try:
            self.connection.retire()
        except Exception:
            pass
