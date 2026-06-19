"""sqlite_util: shared SQLite connection tuning (v1.7 WAL).

All hippocampus stores open their own sqlite3 connection to the same
hippocampus.db file. Enabling WAL (Write-Ahead Logging) improves
concurrent read/write behaviour and crash recovery, which matters here
because several stores (episodic / atom / graph / semantic / prospective
/ profile) write to one file from possibly different threads.

WAL is a database-level persistent setting, but `synchronous` is
per-connection, so we apply both on every connection. Failures (e.g. an
in-memory or read-only database that cannot use WAL) are swallowed: the
pragmas are an optimisation, never a correctness requirement.
"""
from __future__ import annotations
import sqlite3


def apply_pragmas(conn: sqlite3.Connection) -> None:
    """Best-effort apply WAL + NORMAL synchronous to a connection.

    Safe to call on every store connection. Never raises: if the target
    database cannot honour a pragma (memory/read-only), we skip silently.
    """
    if conn is None:
        return
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception as e:
        print("[hippocampus] PRAGMA journal_mode=WAL skipped: " + repr(e))
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        print("[hippocampus] PRAGMA synchronous=NORMAL skipped: " + repr(e))