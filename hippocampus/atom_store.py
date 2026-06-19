# v1.4 B3: AtomStore -- independent SQLite table for MemoryAtom.
# Single-file layout to avoid colliding with the existing storage.py
# (which still ships the HippocampalStore class).
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Iterable

from .types import MemoryAtom, AtomStatus, AtomType, DecayType


class AtomStore:
    """CRUD for MemoryAtom. One SQLite table `atoms`.

    Schema notes:
      - triple identity: (subject, predicate, object) COLLATE NOCASE -- a
        new observation of the same fact MERGEs into the existing row.
      - source_engram_ids stored as JSON array (SQLite has no native list).
      - tags / attributes as JSON for forward compatibility.
      - All times are unix epoch seconds (float).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        from .sqlite_util import apply_pragmas
        apply_pragmas(self._conn)
        self._init_schema()

    # -- schema --------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS atoms (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    evidence_count INTEGER NOT NULL DEFAULT 1,
                    source_engram_ids TEXT NOT NULL DEFAULT '[]',
                    actor_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    decay_type TEXT NOT NULL DEFAULT 'episodic',
                    importance REAL NOT NULL DEFAULT 0.5,
                    strength REAL NOT NULL DEFAULT 1.0,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    tags TEXT NOT NULL DEFAULT '[]',
                    attributes TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(subject COLLATE NOCASE, predicate COLLATE NOCASE, object COLLATE NOCASE)
                );
                CREATE INDEX IF NOT EXISTS idx_atoms_status
                    ON atoms(status);
                CREATE INDEX IF NOT EXISTS idx_atoms_kind
                    ON atoms(kind);
                CREATE INDEX IF NOT EXISTS idx_atoms_decay
                    ON atoms(decay_type);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- row <-> atom --------------------------------------------------

    @staticmethod
    def _row_to_atom(row: sqlite3.Row) -> MemoryAtom:
        d = dict(row)
        d["source_engram_ids"] = json.loads(d.get("source_engram_ids") or "[]")
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["attributes"] = json.loads(d.get("attributes") or "{}")
        return MemoryAtom.from_dict(d)

    @staticmethod
    def _atom_to_params(a: MemoryAtom) -> dict:
        return {
            "id": a.id,
            "kind": a.kind,
            "subject": a.subject,
            "predicate": a.predicate,
            "object": a.object,
            "confidence": float(a.confidence),
            "evidence_count": int(a.evidence_count),
            "source_engram_ids": json.dumps(list(a.source_engram_ids), ensure_ascii=False),
            "actor_id": a.actor_id,
            "platform": a.platform,
            "channel_id": a.channel_id,
            "created_at": float(a.created_at),
            "last_seen": float(a.last_seen),
            "last_accessed": float(a.last_accessed),
            "status": a.status,
            "decay_type": a.decay_type,
            "importance": float(a.importance),
            "strength": float(a.strength),
            "access_count": int(a.access_count),
            "tags": json.dumps(list(a.tags), ensure_ascii=False),
            "attributes": json.dumps(dict(a.attributes), ensure_ascii=False),
        }

    # -- write ---------------------------------------------------------

    def upsert(self, atom: MemoryAtom) -> MemoryAtom:
        """Insert a new atom, or merge into the existing one with the same
        (subject, predicate, object) triple. Returns the persisted atom.

        Merge rule: copy union into the existing row, take the higher
        confidence, the larger strength, and the larger evidence_count
        sum. The original `id` is preserved so external references stay
        stable; the new atom's `id` is discarded.
        """
        # Defensive normalization: keep the canonical key invariant even
        # when callers skip the factory helpers.
        atom.subject = (atom.subject or "").strip().lower()
        atom.predicate = (atom.predicate or "").strip().lower()
        atom.object = (atom.object or "").strip().lower()
        params = self._atom_to_params(atom)
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT id, confidence, strength, evidence_count,
                       source_engram_ids, last_seen, access_count
                FROM atoms
                WHERE subject = ? COLLATE NOCASE
                  AND predicate = ? COLLATE NOCASE
                  AND object = ? COLLATE NOCASE
                """,
                (atom.subject, atom.predicate, atom.object),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO atoms
                        (id, kind, subject, predicate, object, confidence,
                         evidence_count, source_engram_ids, actor_id, platform,
                         channel_id, created_at, last_seen, last_accessed,
                         status, decay_type, importance, strength,
                         access_count, tags, attributes)
                    VALUES
                        (:id, :kind, :subject, :predicate, :object, :confidence,
                         :evidence_count, :source_engram_ids, :actor_id, :platform,
                         :channel_id, :created_at, :last_seen, :last_accessed,
                         :status, :decay_type, :importance, :strength,
                         :access_count, :tags, :attributes)
                    """,
                    params,
                )
                return atom

            existing_sources = set(json.loads(row["source_engram_ids"] or "[]"))
            new_sources = set(atom.source_engram_ids)
            merged_sources = sorted(existing_sources | new_sources)
            merged_evidence = max(int(row["evidence_count"]), int(atom.evidence_count))
            merged_last_seen = max(float(row["last_seen"]), float(atom.last_seen))
            merged_confidence = max(float(row["confidence"]), float(atom.confidence))
            merged_strength = max(float(row["strength"]), float(atom.strength))
            merged_access = max(int(row["access_count"]), int(atom.access_count))

            self._conn.execute(
                """
                UPDATE atoms SET
                    confidence = ?,
                    evidence_count = ?,
                    source_engram_ids = ?,
                    last_seen = ?,
                    strength = ?,
                    access_count = ?
                WHERE id = ?
                """,
                (
                    merged_confidence,
                    merged_evidence,
                    json.dumps(merged_sources, ensure_ascii=False),
                    merged_last_seen,
                    merged_strength,
                    merged_access,
                    row["id"],
                ),
            )
            # Return the canonical row, with the original id preserved.
            return self.get(row["id"])  # type: ignore[return-value]

    # -- read ----------------------------------------------------------

    def get(self, atom_id: str) -> MemoryAtom | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM atoms WHERE id = ?", (atom_id,)
            ).fetchone()
        return self._row_to_atom(row) if row else None

    def by_triple(self, subject: str, predicate: str, obj: str) -> MemoryAtom | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM atoms
                WHERE subject = ? COLLATE NOCASE
                  AND predicate = ? COLLATE NOCASE
                  AND object = ? COLLATE NOCASE
                """,
                (subject, predicate, obj),
            ).fetchone()
        return self._row_to_atom(row) if row else None

    def all(self, limit: int | None = None, status: str | None = None) -> list[MemoryAtom]:
        sql = "SELECT * FROM atoms"
        args: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY last_seen DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args = args + (limit,)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_atom(r) for r in rows]

    def list_by_source_engram(self, engram_id: str) -> list[MemoryAtom]:
        """Return every atom whose source_engram_ids contains `engram_id`.

        SQLite JSON1 `EXISTS` is used to avoid loading every row into Python.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM atoms
                WHERE EXISTS (
                    SELECT 1 FROM json_each(atoms.source_engram_ids)
                    WHERE value = ?
                )
                ORDER BY last_seen DESC
                """,
                (engram_id,),
            ).fetchall()
        return [self._row_to_atom(r) for r in rows]

    # -- count ---------------------------------------------------------

    def count(self, status: str | None = None) -> int:
        with self._lock:
            if status is None:
                row = self._conn.execute("SELECT COUNT(*) AS c FROM atoms").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM atoms WHERE status = ?", (status,)
                ).fetchone()
        return int(row["c"])

    def count_by_type(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS c FROM atoms GROUP BY kind"
            ).fetchall()
        return {r["kind"]: int(r["c"]) for r in rows}

    # -- mutation ------------------------------------------------------

    def delete(self, atom_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM atoms WHERE id = ?", (atom_id,))
            return cur.rowcount > 0

    def write_strength(self, atom_id: str, strength: float) -> bool:
        """Narrow, explicit overwrite for the strength column. Used by
        AtomLifecycleManager.decay_pass() to apply the decayed value
        without falling into upsert's max()-with-existing rule.

        Returns True iff the row was found and updated.
        """
        v = max(0.0, float(strength))
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE atoms SET strength = ? WHERE id = ?",
                (v, atom_id),
            )
            return cur.rowcount > 0

    def set_status(self, atom_id: str, status: str) -> bool:
        """Narrow overwrite for the status column."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE atoms SET status = ? WHERE id = ?",
                (status, atom_id),
            )
            return cur.rowcount > 0

    def soft_forget(self, atom_id: str) -> bool:
        """Mark an atom as soft_forgotten (recoverable; not deleted)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE atoms SET status = ? WHERE id = ?",
                (AtomStatus.SOFT_FORGOTTEN.value, atom_id),
            )
            return cur.rowcount > 0

    def gc_pass(self, floor: float = 0.05, min_age_seconds: float = 0.0) -> int:
        """Garbage-collect atoms whose strength has decayed below `floor`.

        Only atoms older than `min_age_seconds` are eligible. Returns the
        number of rows actually moved to `gc` status. `floor=0` disables
        the lower bound (still respects min_age).
        """
        threshold = max(0.0, float(floor))
        now = time.time()
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, strength, created_at FROM atoms WHERE status = ?",
                (AtomStatus.ACTIVE.value,),
            ).fetchall()
            victims = [
                r["id"]
                for r in rows
                if float(r["strength"]) < threshold
                and (now - float(r["created_at"])) >= float(min_age_seconds)
            ]
            if not victims:
                return 0
            placeholders = ",".join("?" * len(victims))
            cur = self._conn.execute(
                f"UPDATE atoms SET status = ? WHERE id IN ({placeholders})",
                (AtomStatus.GC.value, *victims),
            )
            return cur.rowcount


__all__ = ["AtomStore"]
