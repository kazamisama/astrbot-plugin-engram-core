"""PersonaStore: v1.8 natural-language user persona (narrative profile).

Distinct from ProfileStore: ProfileStore holds structured triples
(actor_id, predicate, value) mined from the semantic relation graph.
PersonaStore holds a free-text *summary* of who a speaker is - their
stable preferences / identity / behaviour - produced by an LLM over the
speaker's recent engrams. It is meant to be injected as stable
background context, complementing (not replacing) ProfileFacts.

One row per (actor_id). Personas are stable background and do NOT
participate in decay / GC.
"""
from __future__ import annotations
import sqlite3
import time
from dataclasses import dataclass, field


def _now() -> float:
    return time.time()


@dataclass
class Persona:
    actor_id: str
    summary: str = ""
    platform: str = ""
    source_count: int = 0
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    @classmethod
    def from_row(cls, row: dict) -> "Persona":
        return cls(
            actor_id=row.get("actor_id", ""),
            summary=row.get("summary", "") or "",
            platform=row.get("platform", "") or "",
            source_count=int(row.get("source_count", 0) or 0),
            created_at=float(row.get("created_at", 0.0) or 0.0),
            updated_at=float(row.get("updated_at", 0.0) or 0.0),
        )


class PersonaStore:
    """CRUD over the personas table. Lazy connection like ProfileStore."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._initialized = False

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            from .sqlite_util import apply_pragmas
            apply_pragmas(self._conn)
        if not self._initialized:
            self._init_schema()
            self._initialized = True
        return self._conn

    def _init_schema(self) -> None:
        conn = self._conn
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS personas (
              actor_id TEXT PRIMARY KEY,
              summary TEXT,
              platform TEXT,
              source_count INTEGER DEFAULT 0,
              created_at REAL,
              updated_at REAL
            );
            """
        )
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

    def upsert(self, persona: Persona) -> Persona:
        conn = self._ensure_conn()
        with conn:
            row = conn.execute(
                "SELECT * FROM personas WHERE actor_id=? LIMIT 1",
                (persona.actor_id,)).fetchone()
            now = _now()
            if row is None:
                conn.execute(
                    "INSERT INTO personas(actor_id,summary,platform,"
                    "source_count,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (persona.actor_id, persona.summary, persona.platform,
                     int(persona.source_count), now, now))
                persona.created_at = now
                persona.updated_at = now
                return persona
            conn.execute(
                "UPDATE personas SET summary=?, platform=?, source_count=?, "
                "updated_at=? WHERE actor_id=?",
                (persona.summary, persona.platform, int(persona.source_count),
                 now, persona.actor_id))
            persona.updated_at = now
            return persona

    def get(self, actor_id: str) -> Persona | None:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT * FROM personas WHERE actor_id=? LIMIT 1",
            (actor_id,)).fetchone()
        return Persona.from_row(dict(row)) if row is not None else None

    def all(self, limit: int = 1000) -> list[Persona]:
        conn = self._ensure_conn()
        cur = conn.execute(
            "SELECT * FROM personas ORDER BY updated_at DESC LIMIT ?",
            (int(limit),))
        return [Persona.from_row(dict(r)) for r in cur.fetchall()]

    def delete(self, actor_id: str) -> bool:
        conn = self._ensure_conn()
        with conn:
            cur = conn.execute(
                "DELETE FROM personas WHERE actor_id=?", (actor_id,))
            return cur.rowcount > 0