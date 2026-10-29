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
    try:
        conn.execute("PRAGMA wal_autocheckpoint=1000")
    except Exception as e:
        print("[hippocampus] PRAGMA wal_autocheckpoint skipped: " + repr(e))


def ensure_columns(conn: sqlite3.Connection, table: str, spec) -> list[str]:
    """Append columns declared in `spec` that `table` does not have yet.

    This is the single schema-upgrade path in the package: each store
    declares the columns current code needs, and a database created by an
    older version gets the missing ones appended on open. Idempotent -- a
    current database pays one PRAGMA and nothing else.

    It deliberately does NOT drop or rewrite anything. Obsolete columns and
    tables are left in place: the current schema does not read them, and
    removing them on every open would keep a migration patch alive forever.
    `table`, `col` and `decl` come from module constants, never from input.
    """
    try:
        have = {row[1] for row in conn.execute(
            "PRAGMA table_info(" + table + ")").fetchall()}
    except sqlite3.Error:
        return []
    if not have:
        return []          # table is created later; nothing to upgrade yet
    added: list[str] = []
    for col, decl in spec:
        if col in have:
            continue
        try:
            conn.execute("ALTER TABLE " + table + " ADD COLUMN "
                         + col + " " + decl)
            added.append(col)
        except sqlite3.Error as exc:
            print("[hippocampus] schema upgrade skipped: " + table
                  + "." + col + ": " + repr(exc))
    return added