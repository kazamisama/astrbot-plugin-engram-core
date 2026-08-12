"""TaskLeaseStore: v1.75 cross-plugin task leases.

Downstream plugins (e.g. astrbot_plugin_your_own_life) serialize
per-persona background tasks through the unified memory host instead of
keeping their own lease table. The lease is a simple
``(persona_id, task_kind)`` row with a TTL; expired rows can be claimed
again immediately. Same SQLite file as the rest of hippocampus so the
lease is visible to every instance sharing the unified memory database.
"""
from __future__ import annotations

import sqlite3
import threading
import time


def _now() -> float:
    return time.time()


class TaskLeaseStore:
    """Persistent per-persona task lease with TTL and single-holder semantics."""

    def __init__(self, sqlite_path: str, busy_timeout_ms: int = 5000):
        self._path = str(sqlite_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path, timeout=max(1.0, busy_timeout_ms / 1000.0)
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS task_leases ("
            "persona_id TEXT NOT NULL,"
            "task_kind TEXT NOT NULL,"
            "holder TEXT NOT NULL,"
            "acquired_at REAL NOT NULL,"
            "expires_at REAL NOT NULL,"
            "PRIMARY KEY (persona_id, task_kind))"
        )
        self._conn.commit()

    def claim(self, persona_id: str, task_kind: str, holder: str,
              ttl_seconds: int = 300) -> bool:
        """Atomically take the lease if free or already expired."""
        now = _now()
        ttl = max(1, int(ttl_seconds))
        with self._lock:
            self._conn.execute(
                "DELETE FROM task_leases WHERE persona_id = ? AND task_kind = ? "
                "AND expires_at <= ?",
                (persona_id, task_kind, now),
            )
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO task_leases "
                "(persona_id, task_kind, holder, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (persona_id, task_kind, holder, now, now + ttl),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def renew(self, persona_id: str, task_kind: str, holder: str,
              ttl_seconds: int = 300) -> bool:
        """Extend the lease; only the current holder can renew."""
        now = _now()
        ttl = max(1, int(ttl_seconds))
        with self._lock:
            cur = self._conn.execute(
                "UPDATE task_leases SET expires_at = ? "
                "WHERE persona_id = ? AND task_kind = ? AND holder = ? "
                "AND expires_at > ?",
                (now + ttl, persona_id, task_kind, holder, now),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def release(self, persona_id: str, task_kind: str, holder: str) -> bool:
        """Release the lease; only the current holder can release."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM task_leases WHERE persona_id = ? AND task_kind = ? "
                "AND holder = ?",
                (persona_id, task_kind, holder),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def owner(self, persona_id: str, task_kind: str) -> str:
        """Return the current holder ('' when free)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT holder FROM task_leases "
                "WHERE persona_id = ? AND task_kind = ? AND expires_at > ?",
                (persona_id, task_kind, _now()),
            ).fetchone()
            return str(row["holder"]) if row else ""

    def cleanup_expired(self) -> int:
        """Drop expired leases; returns rows removed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM task_leases WHERE expires_at <= ?", (_now(),)
            )
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
