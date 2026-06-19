from __future__ import annotations
import json, sqlite3, threading, math
from typing import Iterable
from .types import Engram
from .embeddings import EmbeddingProvider
from .db_migration import run_migrations

def _cos(a, b) -> float:
    if not a or not b: return 0.0
    n = min(len(a), len(b))
    da = math.sqrt(sum(x*x for x in a[:n])) or 1.0
    db = math.sqrt(sum(x*x for x in b[:n])) or 1.0
    return sum(a[i]*b[i] for i in range(n)) / (da * db)


_CJK_RANGES = (
    (0x3040, 0x30FF),   # Hiragana / Katakana
    (0x3400, 0x4DBF),   # CJK Ext A
    (0x4E00, 0x9FFF),   # CJK Unified
    (0x3000, 0x303F),   # CJK punctuation
    (0xFF00, 0xFFEF),   # fullwidth
)

def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def cjk_split(text: str) -> str:
    """Insert spaces around CJK chars so FTS5 unicode61 can tokenize them
    (one char per token). Also normalizes whitespace."""
    if not text: return ""
    out = []
    for ch in text:
        if _is_cjk(ch):
            out.append(" ")
            out.append(ch)
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())


class HippocampalStore:
    """Index + content + vectors + FTS5 in one SQLite file. Replace with sqlite-vec/faiss at scale."""
    def __init__(self, db_path: str, embedder: EmbeddingProvider) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        from .sqlite_util import apply_pragmas
        apply_pragmas(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS engrams (
              id TEXT PRIMARY KEY,
              created_at REAL, session_id TEXT, actor_id TEXT,
              platform TEXT, channel_id TEXT,
              content TEXT, summary TEXT,
              topics TEXT, entities TEXT, entity_refs TEXT, tags TEXT, similar_to TEXT,
              importance REAL, strength REAL,
              access_count INTEGER, last_accessed REAL,
              reconsolidation_lock_until REAL,
              supersedes TEXT, embedding_json TEXT,
              memory_type TEXT, promoted_at REAL,
              embedding_model TEXT,
              fts_text TEXT,
              cluster_id TEXT DEFAULT '',
              profile_fact_id TEXT DEFAULT ''
              ,confidence REAL DEFAULT 0.5
            );
            CREATE INDEX IF NOT EXISTS idx_session ON engrams(session_id);
            CREATE INDEX IF NOT EXISTS idx_actor ON engrams(actor_id);
            CREATE INDEX IF NOT EXISTS idx_channel ON engrams(channel_id);
            CREATE INDEX IF NOT EXISTS idx_time ON engrams(created_at);
            CREATE INDEX IF NOT EXISTS idx_type ON engrams(memory_type);
            CREATE INDEX IF NOT EXISTS idx_embmodel ON engrams(embedding_model);

            CREATE VIRTUAL TABLE IF NOT EXISTS engrams_fts USING fts5(
              fts_text,
              content='engrams', content_rowid='rowid',
              tokenize='unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS engrams_ai AFTER INSERT ON engrams BEGIN
              INSERT INTO engrams_fts(rowid, fts_text)
              VALUES (new.rowid, COALESCE(new.fts_text, ''));
            END;
            CREATE TRIGGER IF NOT EXISTS engrams_ad AFTER DELETE ON engrams BEGIN
              INSERT INTO engrams_fts(engrams_fts, rowid, fts_text)
              VALUES ('delete', old.rowid, COALESCE(old.fts_text, ''));
            END;
            CREATE TRIGGER IF NOT EXISTS engrams_au AFTER UPDATE ON engrams BEGIN
              INSERT INTO engrams_fts(engrams_fts, rowid, fts_text)
              VALUES ('delete', old.rowid, COALESCE(old.fts_text, ''));
              INSERT INTO engrams_fts(rowid, fts_text)
              VALUES (new.rowid, COALESCE(new.fts_text, ''));
            END;

            -- v1.1: user self-model (neocortex analog) + cluster gists
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

            CREATE TABLE IF NOT EXISTS cluster_summaries (
              cluster_id TEXT PRIMARY KEY,
              gist TEXT,
              member_count INTEGER,
              last_refreshed REAL,
              source TEXT
            );

            -- v1.3: rebuild_embeddings checkpoint (idempotent, no-op on existing DBs)
            CREATE TABLE IF NOT EXISTS rebuild_state (
              model TEXT PRIMARY KEY,
              last_id TEXT DEFAULT '',
              processed INTEGER DEFAULT 0,
              updated_at REAL DEFAULT 0.0
            );
            """)
        # B10: column-append migrations extracted to hippocampus.db_migration
        ran = run_migrations(self._conn, self._lock)
        for v in ran:
            print("[hippocampus] applied compat migration: " + v)




    def _build_fts_text(self, e: Engram) -> str:
        """Combine content + summary + topics + entities, CJK-split, for FTS5 index."""
        parts = [e.content, e.summary,
                 " ".join(e.topics or []),
                 " ".join(e.entities or []),
                 " ".join(e.tags or [])]
        return cjk_split(" ".join(p for p in parts if p))

    def upsert(self, e: Engram) -> None:
        e.fts_text = self._build_fts_text(e)
        row = (
            e.id, e.created_at, e.session_id, e.actor_id, e.platform, e.channel_id,
            e.content, e.summary,
            json.dumps(e.topics, ensure_ascii=False),
            json.dumps(e.entities, ensure_ascii=False),
            json.dumps(e.entity_refs, ensure_ascii=False),
            json.dumps(e.tags, ensure_ascii=False),
            json.dumps(e.similar_to, ensure_ascii=False),
            e.importance, e.strength, e.access_count, e.last_accessed,
            e.reconsolidation_lock_until,
            json.dumps(e.supersedes, ensure_ascii=False),
            json.dumps(e.embedding, ensure_ascii=False),
            e.memory_type, e.promoted_at,
            e.embedding_model,
            e.fts_text,
            e.valence, e.intensity, e.temporal_bucket, e.stream, e.forgotten_at,
            e.cluster_id, e.profile_fact_id,
            e.confidence,
        )
        with self._lock, self._conn:
            self._conn.execute("""
            INSERT INTO engrams(id,created_at,session_id,actor_id,platform,channel_id,
              content,summary,topics,entities,entity_refs,tags,similar_to,
              importance,strength,access_count,last_accessed,
              reconsolidation_lock_until,supersedes,embedding_json,
              memory_type,promoted_at,embedding_model,fts_text,
              valence,intensity,temporal_bucket,stream,forgotten_at,
              cluster_id,profile_fact_id,confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              summary=excluded.summary, topics=excluded.topics, entities=excluded.entities,
              entity_refs=excluded.entity_refs, tags=excluded.tags, similar_to=excluded.similar_to,
              importance=excluded.importance, strength=excluded.strength,
              access_count=excluded.access_count, last_accessed=excluded.last_accessed,
              reconsolidation_lock_until=excluded.reconsolidation_lock_until,
              cluster_id=excluded.cluster_id, profile_fact_id=excluded.profile_fact_id,
              supersedes=excluded.supersedes, embedding_json=excluded.embedding_json,
              memory_type=excluded.memory_type, promoted_at=excluded.promoted_at,
              embedding_model=excluded.embedding_model,
              fts_text=excluded.fts_text,
              valence=excluded.valence, intensity=excluded.intensity,
              temporal_bucket=excluded.temporal_bucket, stream=excluded.stream,
              forgotten_at=excluded.forgotten_at,
              confidence=excluded.confidence
            """, row)

    def get(self, eid: str) -> Engram | None:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM engrams WHERE id=?", (eid,))
            row = cur.fetchone()
        return Engram.from_row(dict(row)) if row else None

    def all(self, limit: int = 1000) -> list[Engram]:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM engrams ORDER BY created_at DESC LIMIT ?", (limit,))
            return [Engram.from_row(dict(r)) for r in cur.fetchall()]

    def delete(self, eid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM engrams WHERE id=?", (eid,))

    def all_after(self, after_id: str, limit: int = 100) -> list:
        """Return engrams with id > `after_id`, ordered by id ASC. Used by v1.3 rebuild checkpoint."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM engrams WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, int(limit)))
            return [Engram.from_row(dict(r)) for r in cur.fetchall()]

    def update_embedding(self, eid: str, embedding: list[float], model: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE engrams SET embedding_json=?, embedding_model=? WHERE id=?",
                (json.dumps(embedding, ensure_ascii=False), model, eid))


    # ---------- v1.3: rebuild_state checkpoint helpers ----------
    def get_rebuild_state(self, model: str) -> dict:
        """Return {last_id, processed, updated_at} for `model`. Empty dict if unset."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT last_id, processed, updated_at FROM rebuild_state WHERE model=?",
                (model,))
            row = cur.fetchone()
        if row is None:
            return {'last_id': '', 'processed': 0, 'updated_at': 0.0}
        return {"last_id": row[0] or "", "processed": int(row[1] or 0), "updated_at": float(row[2] or 0.0)}

    def set_rebuild_state(self, model: str, last_id: str, processed: int) -> None:
        """Upsert a checkpoint row. Raises on failure (caller decides rollback)."""
        import time as _time
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO rebuild_state(model, last_id, processed, updated_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(model) DO UPDATE SET last_id=excluded.last_id, "
                "processed=excluded.processed, updated_at=excluded.updated_at",
                (model, last_id, int(processed), _time.time()))

    def clear_rebuild_state(self, model: str) -> None:
        """Remove the checkpoint row for `model`. Used when caller wants a full rebuild."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM rebuild_state WHERE model=?", (model,))

    # ---------- v1.0 biology helpers ----------
    def iter_for_replay(self, k: int = 50) -> list:
        """Top-k engrams for SWR replay: strength * (1 + 0.3*access_count)."""
        all_e = self.all(limit=10_000_000)
        scored = []
        for e in all_e:
            if e.forgotten_at > 0:
                continue
            score = e.strength * (1.0 + 0.3 * (e.access_count or 0))
            scored.append((e, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [e for e, _ in scored[:k]]

    def decay_pass(self, tau_base: float, floor: float,
                   importance_modulator: float = 4.0) -> int:
        """Bulk Ebbinghaus decay. Returns count that fell below floor."""
        import math, time
        now = time.time()
        below = 0
        for e in self.all(limit=10_000_000):
            if e.forgotten_at > 0:
                continue
            tau = tau_base * (1.0 + importance_modulator * (e.importance or 0.0))
            anchor = max(e.last_accessed or 0.0, e.created_at or now)
            dt = max(0.0, now - anchor)
            new_strength = e.strength * math.exp(-dt / max(tau, 1.0))
            if new_strength < floor:
                below += 1
            e.strength = max(0.0, new_strength)
            self.upsert(e)
        return below

    def gc_pass(self, floor: float, min_age_seconds: float = 86400.0) -> int:
        """Hard-delete engrams below floor, never recalled, and old enough."""
        import time
        now = time.time()
        killed = 0
        for e in self.all(limit=10_000_000):
            if e.forgotten_at > 0:
                continue
            if (e.strength < floor
                    and e.access_count == 0
                    and (now - e.created_at) >= min_age_seconds):
                self.delete(e.id)
                killed += 1
        return killed

    def soft_forget(self, eid: str) -> bool:
        """Mark an engram forgotten (forgotten_at=now) but keep the row."""
        import time
        e = self.get(eid)
        if e is None:
            return False
        if e.forgotten_at > 0:
            return False
        e.forgotten_at = time.time()
        e.strength = 0.0
        self.upsert(e)
        return True

    def list_active(self, limit: int = 10_000) -> list:
        return [e for e in self.all(limit=limit) if e.forgotten_at == 0.0]

    def valence_histogram(self) -> dict:
        b = {"positive": 0, "neutral": 0, "negative": 0, "unscored": 0}
        for e in self.all(limit=10_000_000):
            if e.forgotten_at > 0:
                continue
            v = e.valence
            if v > 0.2:
                b["positive"] += 1
            elif v < -0.2:
                b["negative"] += 1
            elif v == 0.0 and e.intensity == 0.0:
                b["unscored"] += 1
            else:
                b["neutral"] += 1
        return b

    def stream_breakdown(self) -> dict:
        out = {"what": 0, "where_when": 0, "untyped": 0}
        for e in self.all(limit=10_000_000):
            if e.forgotten_at > 0:
                continue
            if e.stream == "what":
                out["what"] += 1
            elif e.stream == "where_when":
                out["where_when"] += 1
            else:
                out["untyped"] += 1
        return out

    def vector_search(self, query_vec, k: int, *,
                      actor_id: str | None = None, channel_id: str | None = None,
                      memory_types: list[str] | None = None,
                      embedding_model: str | None = None):
        items = self.all(limit=10_000_000)
        if actor_id: items = [e for e in items if e.actor_id == actor_id]
        if channel_id: items = [e for e in items if e.channel_id == channel_id]
        if memory_types: items = [e for e in items if e.memory_type in memory_types]
        if embedding_model: items = [e for e in items if e.embedding_model == embedding_model]
        scored = [(e, _cos(query_vec, e.embedding)) for e in items]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def fts_search(self, query: str, k: int = 50, *,
                   actor_id: str | None = None, channel_id: str | None = None,
                   memory_types: list[str] | None = None,
                   embedding_model: str | None = None) -> list[tuple[Engram, float]]:
        """BM25 keyword search via FTS5. Returns (engram, similarity) where
        similarity is roughly in (0, 1] derived from -bm25/10."""
        safe_q = self._sanitize_fts_query(query)
        if not safe_q:
            return []
        with self._lock, self._conn:
            try:
                cur = self._conn.execute(
                    "SELECT rowid, bm25(engrams_fts) AS score "
                    "FROM engrams_fts WHERE engrams_fts MATCH ? ORDER BY score LIMIT ?",
                    (safe_q, k * 4))
                hits = [(r["rowid"], float(r["score"])) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                return []
        if not hits: return []
        rowids = [h[0] for h in hits]
        placeholders = ",".join("?" for _ in rowids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"SELECT rowid AS _rid, * FROM engrams WHERE rowid IN ({placeholders})", rowids)
            by_rowid = {r["_rid"]: Engram.from_row(dict(r)) for r in cur.fetchall()}
        out: list[tuple[Engram, float]] = []
        for rowid, bm in hits:
            e = by_rowid.get(rowid)
            if e is None: continue
            if actor_id and e.actor_id != actor_id: continue
            if channel_id and e.channel_id != channel_id: continue
            if memory_types and e.memory_type not in memory_types: continue
            if embedding_model and e.embedding_model != embedding_model: continue
            sim = max(0.0, min(1.0, -bm / 10.0))
            out.append((e, sim))
        return out[:k]

    @staticmethod
    def _sanitize_fts_query(q: str) -> str:
        """Drop FTS5 operators/special chars, CJK-split, AND-join tokens."""
        if not q: return ""
        for ch in (chr(34), "(", ")", ":", "*", "+", "-", "^", "."):
            q = q.replace(ch, " ")
        q = cjk_split(q)
        toks = [t for t in q.split() if t]
        if not toks: return ""
        return " AND ".join(toks)

    def fts_count(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT COUNT(*) AS c FROM engrams_fts")
            return int(cur.fetchone()["c"])

    # ---------- v1.1: cluster_summaries CRUD ----------
    def get_cluster_summary(self, cluster_id: str):
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM cluster_summaries WHERE cluster_id=? LIMIT 1",
                (cluster_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return {"cluster_id": row["cluster_id"], "gist": row["gist"],
                "member_count": row["member_count"],
                "last_refreshed": row["last_refreshed"],
                "source": row["source"]}

    def upsert_cluster_summary(self, cluster_id: str, gist: str,
                                member_count: int, source: str = "auto") -> None:
        import time
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO cluster_summaries"
                "(cluster_id, gist, member_count, last_refreshed, source)"
                " VALUES(?,?,?,?,?)",
                (cluster_id, gist, member_count, time.time(), source))

    def list_cluster_summaries(self, limit: int = 200):
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM cluster_summaries ORDER BY last_refreshed DESC LIMIT ?",
                (limit,))
            return [{"cluster_id": r["cluster_id"], "gist": r["gist"],
                     "member_count": r["member_count"],
                     "last_refreshed": r["last_refreshed"],
                     "source": r["source"]} for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
