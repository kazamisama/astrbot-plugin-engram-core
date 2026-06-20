"""RelationStore: v1.19 (B-2) structured relation triples with per-relation
confidence + conflict-driven supersede ("rewrite" updates).

A relation is (subject, predicate, object) with a confidence in [0,1],
derived by the conversation/diary summarizer. Stored in its own SQLite
table, independent of the main engram store, so relation reasoning does
not pollute the episodic memory table.

Rewrite/supersede semantics (B-2b): when a NEW relation shares the same
(subject, predicate) key as an existing ACTIVE relation but a DIFFERENT
object, they conflict. If the new relation's confidence >= the old one's
(minus a small hysteresis) it SUPERSEDES the old: the old row is marked
superseded_by=new.id and soft-forgotten; otherwise the new relation is
kept as a low-priority candidate and does not override.
"""
from __future__ import annotations
import sqlite3
import time
import uuid


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


def rel_key(subject: str, predicate: str) -> str:
    return (subject or "").strip().lower() + "\u0001" + (predicate or "").strip().lower()


class Relation:
    __slots__ = ("id", "subject", "predicate", "object", "confidence",
                 "actor_id", "channel_id", "source_engram_id",
                 "created_at", "updated_at", "superseded_by", "forgotten_at",
                 "subject_type", "object_type")

    def __init__(self, subject="", predicate="", object="", confidence=0.5,
                 actor_id="", channel_id="", source_engram_id="",
                 id=None, created_at=None, updated_at=None,
                 superseded_by="", forgotten_at=0.0,
                 subject_type="", object_type=""):
        self.id = id or _new_id()
        self.subject = subject
        self.predicate = predicate
        self.object = object
        self.subject_type = subject_type or ""
        self.object_type = object_type or ""
        self.confidence = float(confidence)
        self.actor_id = actor_id
        self.channel_id = channel_id
        self.source_engram_id = source_engram_id
        self.created_at = created_at if created_at is not None else _now()
        self.updated_at = updated_at if updated_at is not None else self.created_at
        self.superseded_by = superseded_by
        self.forgotten_at = forgotten_at

    @classmethod
    def from_row(cls, row) -> "Relation":
        d = dict(row)
        return cls(
            id=d.get("id"), subject=d.get("subject", ""),
            predicate=d.get("predicate", ""), object=d.get("object", ""),
            confidence=d.get("confidence", 0.5), actor_id=d.get("actor_id", ""),
            channel_id=d.get("channel_id", ""),
            source_engram_id=d.get("source_engram_id", ""),
            created_at=d.get("created_at"), updated_at=d.get("updated_at"),
            superseded_by=d.get("superseded_by", "") or "",
            forgotten_at=d.get("forgotten_at", 0.0) or 0.0,
            subject_type=d.get("subject_type", "") or "",
            object_type=d.get("object_type", "") or "")


class RelationStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._initialized = False

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            try:
                from .sqlite_util import apply_pragmas
                apply_pragmas(self._conn)
            except Exception:
                pass
        if not self._initialized:
            self._init_schema()
            self._initialized = True
        return self._conn

    def _init_schema(self) -> None:
        conn = self._conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_relations ("
            " id TEXT PRIMARY KEY,"
            " subject TEXT, predicate TEXT, object TEXT,"
            " confidence REAL, actor_id TEXT, channel_id TEXT,"
            " source_engram_id TEXT,"
            " created_at REAL, updated_at REAL,"
            " superseded_by TEXT, forgotten_at REAL,"
            " rkey TEXT, subject_type TEXT, object_type TEXT)")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(llm_relations)").fetchall()]
        if "rkey" not in cols:
            conn.execute("ALTER TABLE llm_relations ADD COLUMN rkey TEXT")
        if "subject_type" not in cols:
            conn.execute("ALTER TABLE llm_relations ADD COLUMN subject_type TEXT")
        if "object_type" not in cols:
            conn.execute("ALTER TABLE llm_relations ADD COLUMN object_type TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lrel_rkey ON llm_relations(rkey)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lrel_subject ON llm_relations(subject)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lrel_active ON llm_relations(superseded_by, forgotten_at)")
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
                self._initialized = False

    def is_open(self) -> bool:
        return self._conn is not None

    # ---- write ----
    def _insert(self, r: Relation) -> Relation:
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR REPLACE INTO llm_relations(id,subject,predicate,object,"
            "confidence,actor_id,channel_id,source_engram_id,created_at,"
            "updated_at,superseded_by,forgotten_at,rkey,subject_type,object_type) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.id, r.subject, r.predicate, r.object, r.confidence,
             r.actor_id, r.channel_id, r.source_engram_id, r.created_at,
             r.updated_at, r.superseded_by, r.forgotten_at,
             rel_key(r.subject, r.predicate),
             getattr(r, "subject_type", "") or "",
             getattr(r, "object_type", "") or ""))
        conn.commit()
        return r

    def add_with_supersede(self, r: Relation, *, hysteresis: float = 0.0) -> dict:
        """Insert `r`; if it conflicts with an active same-key relation
        (different object), supersede the loser. Returns a small report:
        {action: "insert"|"supersede"|"candidate", superseded: [ids]}.
        """
        conn = self._ensure_conn()
        key = rel_key(r.subject, r.predicate)
        rows = conn.execute(
            "SELECT * FROM llm_relations WHERE rkey=? AND superseded_by='' "
            "AND (forgotten_at IS NULL OR forgotten_at=0)", (key,)).fetchall()
        actives = [Relation.from_row(x) for x in rows]
        conflicts = [a for a in actives
                     if (a.object or "").strip().lower() != (r.object or "").strip().lower()]
        if not conflicts:
            # no conflict: same object reinforces (bump confidence), or brand new
            same = [a for a in actives
                    if (a.object or "").strip().lower() == (r.object or "").strip().lower()]
            if same:
                old = same[0]
                old.confidence = min(1.0, max(old.confidence, r.confidence))
                old.updated_at = _now()
                old.source_engram_id = r.source_engram_id or old.source_engram_id
                self._insert(old)
                return {"action": "reinforce", "id": old.id, "superseded": []}
            self._insert(r)
            return {"action": "insert", "id": r.id, "superseded": []}
        # conflict: decide supersede vs candidate
        strongest_old = max(conflicts, key=lambda a: a.confidence)
        if r.confidence >= (strongest_old.confidence - hysteresis):
            self._insert(r)  # new becomes active
            superseded = []
            now = _now()
            for a in conflicts:
                a.superseded_by = r.id
                a.forgotten_at = now
                a.updated_at = now
                self._insert(a)
                superseded.append(a.id)
            return {"action": "supersede", "id": r.id, "superseded": superseded}
        # new is weaker: keep as candidate (forgotten so it does not surface)
        r.forgotten_at = _now()
        self._insert(r)
        return {"action": "candidate", "id": r.id, "superseded": []}

    # ---- read ----
    def active_for_subjects(self, subjects, limit: int = 50) -> list:
        if not subjects:
            return []
        conn = self._ensure_conn()
        qs = ",".join("?" for _ in subjects)
        rows = conn.execute(
            "SELECT * FROM llm_relations WHERE subject IN (" + qs + ") "
            "AND superseded_by='' AND (forgotten_at IS NULL OR forgotten_at=0) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (*subjects, limit)).fetchall()
        return [Relation.from_row(x) for x in rows]

    def all_active(self, limit: int = 500) -> list:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM llm_relations WHERE superseded_by='' "
            "AND (forgotten_at IS NULL OR forgotten_at=0) "
            "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [Relation.from_row(x) for x in rows]

    def count_active(self) -> int:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM llm_relations WHERE superseded_by='' "
            "AND (forgotten_at IS NULL OR forgotten_at=0)").fetchone()
        return int(row["c"]) if row else 0

    def get_by_id(self, rid: str):
        conn = self._ensure_conn()
        row = conn.execute("SELECT * FROM llm_relations WHERE id=?", (rid,)).fetchone()
        return Relation.from_row(row) if row else None

    def delete_by_id(self, rid: str) -> bool:
        """Hard-delete one relation by id. True if a row was removed."""
        conn = self._ensure_conn()
        cur = conn.execute("DELETE FROM llm_relations WHERE id=?", (rid,))
        conn.commit()
        return cur.rowcount > 0

    def set_confidence(self, rid: str, confidence: float) -> bool:
        """Set one relation's confidence (clamped to [0,1])."""
        c = max(0.0, min(1.0, float(confidence)))
        conn = self._ensure_conn()
        cur = conn.execute(
            "UPDATE llm_relations SET confidence=?, updated_at=? WHERE id=?",
            (c, _now(), rid))
        conn.commit()
        return cur.rowcount > 0

    def delete_entity(self, name: str) -> int:
        """Hard-delete every relation touching `name` (subject OR object).
        Case-insensitive on the trimmed name. Returns rows removed."""
        nm = (name or "").strip().lower()
        if not nm:
            return 0
        conn = self._ensure_conn()
        cur = conn.execute(
            "DELETE FROM llm_relations WHERE lower(trim(subject))=? "
            "OR lower(trim(object))=?", (nm, nm))
        conn.commit()
        return cur.rowcount

    def relations_for(self, name: str, limit: int = 200) -> list:
        """Active relations where `name` is subject OR object."""
        nm = (name or "").strip().lower()
        if not nm:
            return []
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM llm_relations WHERE (lower(trim(subject))=? "
            "OR lower(trim(object))=?) AND superseded_by='' "
            "AND (forgotten_at IS NULL OR forgotten_at=0) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (nm, nm, limit)).fetchall()
        return [Relation.from_row(x) for x in rows]
