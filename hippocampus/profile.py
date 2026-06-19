"""v1.1 user self-model (neocortex analog).

A flat, slowly-changing KV of stable user facts derived from the semantic
relation graph. Lives next to engrams (episodic) and entities (semantic)
in the same SQLite file.

The fact is keyed by (actor_id, predicate, value). When the same triple is
re-observed, evidence_count is incremented and confidence is averaged in.

The SQLite connection is opened lazily on first use so constructing a
ProfileStore is cheap and the file is not locked until something actually
needs it.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict

from .config import MemoryConfig


_PROFILE_PREDICATES = {"likes", "dislikes", "resides_in", "is_a", "has"}


def _now() -> float: return time.time()
def _new_id() -> str: return uuid.uuid4().hex


@dataclass
class ProfileFact:
    id: str = field(default_factory=_new_id)
    actor_id: str = ""
    predicate: str = ""
    value: str = ""
    value_type: str = "string"  # 'string' | 'entity_ref'
    confidence: float = 0.5
    evidence_count: int = 1
    source_relation_ids: list[str] = field(default_factory=list)
    source_engram_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    last_evidence_at: float = field(default_factory=_now)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_row(cls, row: dict) -> "ProfileFact":
        d = dict(row)
        for k in ("source_relation_ids", "source_engram_ids"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = json.loads(v) if v else []
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ProfileStore:
    """CRUD over the profile_facts table. Lazy connection: the SQLite file
    is only opened on the first CRUD call (or by an explicit schema init)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._initialized = False

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        if not self._initialized:
            self._init_schema()
            self._initialized = True
        return self._conn

    def _init_schema(self) -> None:
        assert self._conn is not None
        with self._lock, self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS profile_facts (
              id TEXT PRIMARY KEY,
              actor_id TEXT,
              predicate TEXT,
              value TEXT,
              value_type TEXT DEFAULT 'string',
              confidence REAL,
              evidence_count INTEGER,
              source_relation_ids TEXT,
              source_engram_ids TEXT,
              created_at REAL,
              updated_at REAL,
              last_evidence_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_profile_actor ON profile_facts(actor_id);
            CREATE INDEX IF NOT EXISTS idx_profile_pred ON profile_facts(actor_id, predicate);
            """)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try: self._conn.close()
                except Exception: pass
                self._conn = None
                self._initialized = False

    def is_open(self) -> bool:
        return self._conn is not None

    # ---------- CRUD ----------
    def upsert_fact(self, fact: ProfileFact) -> ProfileFact:
        conn = self._ensure_conn()
        with self._lock, conn:
            cur = conn.execute(
                "SELECT * FROM profile_facts WHERE actor_id=? AND predicate=? AND value=? LIMIT 1",
                (fact.actor_id, fact.predicate, fact.value))
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO profile_facts("
                    "id,actor_id,predicate,value,value_type,confidence,evidence_count,"
                    "source_relation_ids,source_engram_ids,created_at,updated_at,last_evidence_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fact.id, fact.actor_id, fact.predicate, fact.value, fact.value_type,
                     float(fact.confidence), int(fact.evidence_count),
                     json.dumps(fact.source_relation_ids, ensure_ascii=False),
                     json.dumps(fact.source_engram_ids, ensure_ascii=False),
                     fact.created_at, fact.updated_at, fact.last_evidence_at))
                return fact
            existing = ProfileFact.from_row(dict(row))
            old_n = existing.evidence_count
            new_n = max(1, int(fact.evidence_count))
            total = old_n + new_n
            existing.confidence = (existing.confidence * old_n + float(fact.confidence) * new_n) / total
            existing.evidence_count = total
            existing.updated_at = _now()
            existing.last_evidence_at = max(existing.last_evidence_at, fact.last_evidence_at)
            for rid in fact.source_relation_ids:
                if rid and rid not in existing.source_relation_ids:
                    existing.source_relation_ids.append(rid)
            for eid in fact.source_engram_ids:
                if eid and eid not in existing.source_engram_ids:
                    existing.source_engram_ids.append(eid)
            conn.execute(
                "UPDATE profile_facts SET confidence=?, evidence_count=?, updated_at=?,"
                " last_evidence_at=?, source_relation_ids=?, source_engram_ids=? WHERE id=?",
                (existing.confidence, existing.evidence_count, existing.updated_at,
                 existing.last_evidence_at,
                 json.dumps(existing.source_relation_ids, ensure_ascii=False),
                 json.dumps(existing.source_engram_ids, ensure_ascii=False),
                 existing.id))
            return existing

    def facts_for(self, actor_id: str, *, predicate: str | None = None,
                  limit: int = 200) -> list[ProfileFact]:
        conn = self._ensure_conn()
        with self._lock, conn:
            if predicate is not None:
                cur = conn.execute(
                    "SELECT * FROM profile_facts WHERE actor_id=? AND predicate=?"
                    " ORDER BY confidence DESC, evidence_count DESC LIMIT ?",
                    (actor_id, predicate, limit))
            else:
                cur = conn.execute(
                    "SELECT * FROM profile_facts WHERE actor_id=?"
                    " ORDER BY confidence DESC, evidence_count DESC LIMIT ?",
                    (actor_id, limit))
            return [ProfileFact.from_row(dict(r)) for r in cur.fetchall()]

    def get_fact(self, fact_id: str) -> ProfileFact | None:
        conn = self._ensure_conn()
        with self._lock, conn:
            cur = conn.execute(
                "SELECT * FROM profile_facts WHERE id=? LIMIT 1", (fact_id,))
            row = cur.fetchone()
        return ProfileFact.from_row(dict(row)) if row else None

    def delete_fact(self, fact_id: str) -> bool:
        conn = self._ensure_conn()
        with self._lock, conn:
            cur = conn.execute("DELETE FROM profile_facts WHERE id=?", (fact_id,))
            return cur.rowcount > 0

    def all_facts(self, limit: int = 1000) -> list[ProfileFact]:
        conn = self._ensure_conn()
        with self._lock, conn:
            cur = conn.execute(
                "SELECT * FROM profile_facts ORDER BY updated_at DESC LIMIT ?", (limit,))
            return [ProfileFact.from_row(dict(r)) for r in cur.fetchall()]

    # ---------- build from relations ----------
    def build_from_relations(self, actor_id: str, semantic_store,
                             store, cfg: MemoryConfig) -> list[ProfileFact]:
        if not cfg.enable_profile:
            return []
        min_ev = max(1, int(cfg.profile_min_evidence))
        min_conf = float(cfg.profile_min_confidence)
        grouped: dict[tuple[str, str], list[tuple[float, str, str]]] = {}
        engrams = [e for e in store.all(limit=10_000_000) if e.actor_id == actor_id]
        for e in engrams:
            best_per_pair: dict[tuple[str, str], tuple[float, str, str]] = {}
            for ref in (e.entity_refs or []):
                ent = semantic_store.get_entity(ref)
                if ent is None:
                    continue
                for rel in semantic_store.relations_of(ent.id):
                    if rel.predicate not in _PROFILE_PREDICATES:
                        continue
                    if rel.subject_id != ent.id:
                        continue
                    obj = semantic_store.get_entity(rel.object_id)
                    if obj is None:
                        continue
                    key = (rel.predicate, obj.name.lower().strip())
                    cur = best_per_pair.get(key)
                    if cur is None or float(rel.confidence) > cur[0]:
                        best_per_pair[key] = (float(rel.confidence), e.id, rel.id)
            for key, evidence in best_per_pair.items():
                grouped.setdefault(key, []).append(evidence)
        first_display: dict[tuple[str, str], str] = {}
        for ent in semantic_store.all_entities(limit=10_000_000):
            for rel in semantic_store.relations_of(ent.id):
                if rel.predicate not in _PROFILE_PREDICATES:
                    continue
                if rel.subject_id != ent.id:
                    continue
                obj = semantic_store.get_entity(rel.object_id)
                if obj is None:
                    continue
                k = (rel.predicate, obj.name.lower().strip())
                first_display.setdefault(k, obj.name)
        out: list[ProfileFact] = []
        now = _now()
        for (pred, value_lower), evidence in grouped.items():
            if len(evidence) < min_ev:
                continue
            avg_conf = sum(c for c, _, _ in evidence) / len(evidence)
            if avg_conf < min_conf:
                continue
            display = first_display.get((pred, value_lower), value_lower)
            fact = ProfileFact(
                actor_id=actor_id, predicate=pred, value=display,
                confidence=avg_conf, evidence_count=len(evidence),
                source_relation_ids=list({r for _, _, r in evidence}),
                source_engram_ids=list({eid for _, eid, _ in evidence}),
                created_at=now, updated_at=now, last_evidence_at=now,
            )
            out.append(self.upsert_fact(fact))
        return out

    # ---------- decay ----------
    def decay_facts(self, actor_id: str | None, cfg: MemoryConfig) -> int:
        if not cfg.enable_profile:
            return 0
        conn = self._ensure_conn()
        now = _now()
        cutoff = now - float(cfg.profile_fact_decay_days) * 86400.0
        affected = 0
        with self._lock, conn:
            where = "WHERE last_evidence_at < ?"
            params: list = [cutoff]
            if actor_id is not None:
                where += " AND actor_id=?"
                params.append(actor_id)
            cur = conn.execute("SELECT * FROM profile_facts " + where, params)
            rows = [ProfileFact.from_row(dict(r)) for r in cur.fetchall()]
            for f in rows:
                f.confidence *= 0.5
                if f.confidence < 0.1:
                    conn.execute("DELETE FROM profile_facts WHERE id=?", (f.id,))
                else:
                    conn.execute(
                        "UPDATE profile_facts SET confidence=? WHERE id=?",
                        (f.confidence, f.id))
                affected += 1
        return affected

    # ---------- render ----------
    def render(self, actor_id: str) -> str:
        facts = self.facts_for(actor_id)
        if not facts:
            return "(no profile facts for " + actor_id + ")"
        lines = ["## profile: " + actor_id]
        for f in facts:
            lines.append("  " + f.predicate + ": " + f.value
                         + "  (conf=" + str(round(f.confidence, 2))
                         + ", n=" + str(f.evidence_count) + ")")
        return "\n".join(lines)
