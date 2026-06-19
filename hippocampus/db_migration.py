"""B10 db_migration: compatibility-layer migrations for hippocampus.db.

Extracted from HippocampalStore._migrate_v10/v11/v12 at v1.4.x B10. Owns
the "old column might be missing on a pre-v1.0 db" ALTER path; v1.3 and
v1.4 are CREATE-TABLE-IF-NOT-EXISTS only and live in their own store
files (atom_store.py, graph_store.py), so they are already idempotent
and not migrated here.

Exposes one entry point: `run_migrations(conn, lock) -> list[str]`
returning the version labels that actually ran, for logging.

`lock` is the store's threading.RLock - the migration is a single
short transaction, so the caller passes it to stay consistent with
the rest of HippocampalStore (no other writers see a half-migrated
schema).
"""
from __future__ import annotations
import sqlite3
import threading
from typing import List


# Column-append migrations. Each entry is (version, [(col, decl), ...]).
# Idempotent: skip if the column already exists.
_COMPAT_MIGRATIONS: List[tuple] = [
    ("v1.0", [
        ("valence", "REAL DEFAULT 0.0"),
        ("intensity", "REAL DEFAULT 0.0"),
        ("temporal_bucket", "INTEGER DEFAULT 0"),
        ("stream", "TEXT DEFAULT ''"),
        ("forgotten_at", "REAL DEFAULT 0.0"),
    ]),
    ("v1.1", [
        ("cluster_id", "TEXT DEFAULT ''"),
        ("profile_fact_id", "TEXT DEFAULT ''"),
    ]),
    ("v1.2", [
        ("confidence", "REAL DEFAULT 0.5"),
    ]),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set:
    try:
        cur = conn.execute("PRAGMA table_info(" + table + ")")
        return {r[1] for r in cur.fetchall()}
    except Exception:
        return set()


class _NullCtx:
    """Context manager that does nothing (no lock passed in)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run_migrations(conn: sqlite3.Connection,
                   lock: threading.RLock | None = None) -> List[str]:
    """Run all column-append migrations against the `engrams` table.

    Idempotent: columns that already exist are skipped. Tables missing
    altogether (e.g. fresh DB before _init_schema) return [] quietly.
    """
    cols = _existing_columns(conn, "engrams")
    if not cols:
        return []
    ran: List[str] = []
    for version, alters in _COMPAT_MIGRATIONS:
        added_any = False
        for col, decl in alters:
            if col in cols:
                continue
            try:
                ctx = lock if lock is not None else _NullCtx()
                with ctx, conn:
                    conn.execute(
                        "ALTER TABLE engrams ADD COLUMN " + col + " " + decl)
                added_any = True
            except Exception as e:
                print("[hippocampus] " + version + " migration add "
                      + col + " failed: " + repr(e))
        if added_any:
            ran.append(version)
    return ran