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
    def __init__(self, db_path: str, embedder: EmbeddingProvider,
                 tokenizer_mode: str = "char") -> None:
        self._db_path = db_path
        self._embedder = embedder
        from .tokenizer import normalize_mode
        self._tokenizer_mode = normalize_mode(tokenizer_mode)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        from .sqlite_util import apply_pragmas
        apply_pragmas(self._conn)
        self._init_schema()
        self._sync_tokenizer_mode()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS engrams (
              id TEXT PRIMARY KEY,
              created_at REAL, session_id TEXT, actor_id TEXT,
              platform TEXT, channel_id TEXT,
              persona_id TEXT DEFAULT '',
              scope_id TEXT DEFAULT '',
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
              ,tier TEXT DEFAULT 'hot'
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

            -- v1.10: small key/value meta (e.g. active tokenizer mode)
            CREATE TABLE IF NOT EXISTS hippo_meta (
              key TEXT PRIMARY KEY,
              value TEXT
            );

            -- v1.76.5: retained source transcript for audit + re-summarize
            CREATE TABLE IF NOT EXISTS memory_sources (
              memory_id TEXT PRIMARY KEY,
              source_json TEXT NOT NULL,
              source_count INTEGER DEFAULT 0,
              created_at REAL DEFAULT 0.0
            );
            """)
        # B10: column-append migrations extracted to hippocampus.db_migration
        ran = run_migrations(self._conn, self._lock)
        for v in ran:
            print("[hippocampus] applied compat migration: " + v)
        # v1.36: index on persona_id, created after the column-append
        # migration guarantees the column exists (old DBs included).
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_persona ON engrams(persona_id)")
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_scope ON engrams(scope_id)")
        except Exception as _ix:
            print("[hippocampus] idx_persona create skipped: " + repr(_ix))
        # P1 (2026-08-11): resumable write-operation log. The post-ingest
        # pipeline fans out across semantic / atom / graph stores, which
        # cannot be covered by one SQLite transaction (separate
        # connections). A crash mid-pipeline leaves derived indexes
        # incomplete; this table lets startup detect and replay them.
        with self._lock, self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_write_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type TEXT NOT NULL,
                memory_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                step TEXT NOT NULL DEFAULT 'started',
                payload TEXT DEFAULT '{}',
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """)
            self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_write_ops_status
            ON memory_write_ops(status, updated_at)
            """)

    def _meta_get(self, key: str):
        try:
            cur = self._conn.execute(
                "SELECT value FROM hippo_meta WHERE key=?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None
        except sqlite3.OperationalError:
            return None

    def _meta_set(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO hippo_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))

    def prompt_override_keys(self) -> list[str]:
        """Names of persisted prompt overrides (keys exclude the prefix)."""
        try:
            with self._lock, self._conn:
                rows = self._conn.execute(
                    "SELECT key FROM hippo_meta WHERE key LIKE 'prompt_override:%'").fetchall()
        except sqlite3.OperationalError:
            return []
        return [str(r["key"]).split(":", 1)[1] for r in rows]

    def get_prompt_override(self, name: str) -> str | None:
        return self._meta_get("prompt_override:" + name)

    def set_prompt_override(self, name: str, content: str | None) -> None:
        if content is None:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM hippo_meta WHERE key=?", ("prompt_override:" + name,))
        else:
            self._meta_set("prompt_override:" + name, content)

    def _sync_tokenizer_mode(self) -> None:
        """If the persisted tokenizer mode differs from the requested one,
        re-tokenize every row''s fts_text so the index matches the new mode,
        then persist it. No-op when unchanged (cheap startup path)."""
        prev = self._meta_get("tokenizer_mode")
        if prev == self._tokenizer_mode:
            return
        if prev is not None:
            self.reindex_fts()
        self._meta_set("tokenizer_mode", self._tokenizer_mode)

    def reindex_fts(self) -> int:
        """Rebuild fts_text for all engrams under the current tokenizer
        mode. The UPDATE triggers keep engrams_fts in sync. Returns the
        number of rows reindexed."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT rowid, content, summary, topics, entities, tags "
                "FROM engrams").fetchall()
            n = 0
            for r in rows:
                parts = [r["content"] or "", r["summary"] or ""]
                for col in ("topics", "entities", "tags"):
                    raw = r[col]
                    if raw:
                        try:
                            vals = json.loads(raw)
                            if isinstance(vals, list):
                                parts.append(" ".join(str(v) for v in vals))
                        except Exception:
                            pass
                from .tokenizer import tokenize
                fts = tokenize(" ".join(p for p in parts if p),
                               self._tokenizer_mode)
                self._conn.execute(
                    "UPDATE engrams SET fts_text=? WHERE rowid=?",
                    (fts, r["rowid"]))
                n += 1
        return n




    def _build_fts_text(self, e: Engram) -> str:
        """Combine content + summary + topics + entities, CJK-split, for FTS5 index."""
        parts = [e.content, e.summary,
                 " ".join(e.topics or []),
                 " ".join(e.entities or []),
                 " ".join(e.tags or [])]
        from .tokenizer import tokenize
        return tokenize(" ".join(p for p in parts if p), self._tokenizer_mode)

    def upsert(self, e: Engram) -> None:
        e.fts_text = self._build_fts_text(e)
        row = (
            e.id, e.created_at, e.session_id, e.actor_id, e.platform, e.channel_id,
            e.persona_id, e.scope_id,
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
            e.tier,
        )
        with self._lock, self._conn:
            self._conn.execute("""
            INSERT INTO engrams(id,created_at,session_id,actor_id,platform,channel_id,
              persona_id,scope_id,
              content,summary,topics,entities,entity_refs,tags,similar_to,
              importance,strength,access_count,last_accessed,
              reconsolidation_lock_until,supersedes,embedding_json,
              memory_type,promoted_at,embedding_model,fts_text,
              valence,intensity,temporal_bucket,stream,forgotten_at,
              cluster_id,profile_fact_id,confidence,tier)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              content=excluded.content,
              persona_id=excluded.persona_id,
              scope_id=excluded.scope_id,
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
              confidence=excluded.confidence,
              tier=excluded.tier
            """, row)

    def check_index_consistency(self) -> dict:
        """P1b (2026-08-11): verify the FTS sync triggers exist.

        engrams_fts is an external-content FTS5 table (content='engrams'),
        so COUNT()/ integrity-check cannot detect missing index rows. The
        root cause of drift is a missing AFTER INSERT/UPDATE/DELETE
        trigger, which is what we check here.
        """
        with self._lock, self._conn:
            names = {r[0] for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN "
                "('engrams_ai','engrams_au','engrams_ad')")}
        expected = {"engrams_ai", "engrams_au", "engrams_ad"}
        missing = sorted(expected - names)
        return {"triggers_ok": len(missing) == 0,
                "missing_triggers": missing,
                "repairable": True}

    def get(self, eid: str) -> Engram | None:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM engrams WHERE id=?", (eid,))
            row = cur.fetchone()
        return Engram.from_row(dict(row)) if row else None

    def all(self, limit: int = 1000) -> list[Engram]:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM engrams ORDER BY created_at DESC LIMIT ?", (limit,))
            return [Engram.from_row(dict(r)) for r in cur.fetchall()]

    def delete(self, eid: str) -> bool:
        """Delete an engram by id. Returns True if a row was removed.
        (FTS rows are dropped by the AFTER DELETE trigger.)"""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM engrams WHERE id=?", (eid,))
            ok = cur.rowcount > 0
            if ok:
                self.delete_memory_source(eid)
            return ok

    def all_after(self, after_id: str, limit: int = 100) -> list:
        """Return engrams with id > `after_id`, ordered by id ASC. Used by v1.3 rebuild checkpoint."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM engrams WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, int(limit)))
            return [Engram.from_row(dict(r)) for r in cur.fetchall()]

    def recent_for_session(self, session_id: str, k: int = 5,
                          include_forgotten: bool = False) -> list:
        """Return up to k engrams for a session, newest-first.
        Used as context seeds in SpreadingActivation to pre-excite
        engrams from the same conversation."""
        if not session_id:
            return []
        params = [session_id]
        fc = "" if include_forgotten else " AND forgotten_at = 0 "
        sql = "SELECT * FROM engrams WHERE session_id = ?" + fc
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(k))
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
            return [Engram.from_row(dict(r)) for r in cur.fetchall()]

    def recent_for_actor(self, actor_id: str, k: int = 5,
                          min_strength: float = 0.0,
                          include_forgotten: bool = False) -> list:
        """Return up to k engrams for an actor, newest-first by last_accessed.

        Filters out soft-forgotten engrams by default. Used as context
        seeds for SpreadingActivation so the user's recently touched
        high-strength items pre-excite the recall graph (analogous to
        hippocampal pre-excitation bias on engram cell allocation).
        """
        if not actor_id:
            return []
        params = [actor_id]
        forgotten_clause = "" if include_forgotten else " AND forgotten_at = 0 "
        sql = "SELECT * FROM engrams WHERE actor_id = ?" + forgotten_clause
        if min_strength > 0:
            sql += " AND strength >= ?"
            params.append(float(min_strength))
        sql += " ORDER BY last_accessed DESC LIMIT ?"
        params.append(int(k))
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
            return [Engram.from_row(dict(r)) for r in cur.fetchall()]

    def top_by_importance(self, min_importance: float = 0.5,
                          k: int = 5,
                          actor_id=None,
                          include_forgotten: bool = False) -> list:
        """Return up to k high-importance active engrams. Used as pre-
        excitation seeds in SpreadingActivation (engram cell allocation
        bias). If actor_id is given, restricts to that actor; otherwise
        returns the global top-k. Soft-forgotten engrams are excluded
        by default.
        """
        params = []
        clauses = ["importance >= ?"]
        params.append(float(min_importance))
        if not include_forgotten:
            clauses.append("forgotten_at = 0")
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM engrams WHERE {where} "
            f"ORDER BY importance DESC, strength DESC LIMIT ?"
        )
        params.append(int(k))
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
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
        """Bulk Ebbinghaus decay. Returns count that fell below floor.

        v1.72: batch UPDATE via executemany instead of per-engram upsert.
        A single pass with N engrams now produces 1 fsync instead of N,
        preventing WAL explosion from N independent write transactions."""
        import math, time
        now = time.time()
        below = 0
        updates = []
        for e in self.all(limit=10_000_000):
            if e.forgotten_at > 0:
                continue
            tau = tau_base * (1.0 + importance_modulator * (e.importance or 0.0))
            anchor = max(e.last_accessed or 0.0, e.created_at or now)
            dt = max(0.0, now - anchor)
            new_strength = e.strength * math.exp(-dt / max(tau, 1.0))
            if new_strength < floor:
                below += 1
            updates.append((max(0.0, new_strength), e.id))
        if updates:
            with self._lock, self._conn:
                self._conn.executemany(
                    "UPDATE engrams SET strength=? WHERE id=?", updates)
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

    def restore(self, eid: str, *, strength: float = 0.1) -> bool:
        """Un-forget an engram so it re-enters active recall paths.

        v1.76.5: WebUI archive/restore support. The engram keeps its
        previous embedding/model unless the caller re-embeds it.
        """
        e = self.get(eid)
        if e is None:
            return False
        if not (getattr(e, "forgotten_at", 0.0) or 0.0):
            return False
        e.forgotten_at = 0.0
        e.strength = max(float(getattr(e, "strength", 0.0) or 0.0),
                         float(strength))
        self.upsert(e)
        return True

    def list_active(self, limit: int = 10_000, *,
                    memory_type: str | None = None,
                    actor_id: str | None = None,
                    scope_id: str | None = None) -> list:
        """Active (not soft-forgotten) engrams, newest first.

        P2b (2026-08-11): filters are pushed into the WHERE clause instead
        of post-filtering a Python list. Old callers passing only `limit`
        keep working unchanged.
        """
        where: list[str] = ["COALESCE(forgotten_at, 0) = 0"]
        params: list[object] = []
        if actor_id:
            where.append("actor_id = ?")
            params.append(actor_id)
        if memory_type:
            where.append("memory_type = ?")
            params.append(str(memory_type))
        if scope_id is not None:
            where.append("COALESCE(scope_id, '') = ?")
            params.append(scope_id or "")
        sql = "SELECT * FROM engrams"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._conn:
            rows = self._conn.execute(sql, params).fetchall()
        return [Engram.from_row(dict(r)) for r in rows]

    def engram_ids_for_scope(self, scope_id: str, limit: int = 100000) -> set:
        """Return active engram ids for one memory-scope partition."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id FROM engrams "
                "WHERE COALESCE(scope_id, '') = ? AND forgotten_at = 0 "
                "LIMIT ?",
                (scope_id or "", int(limit)),
            ).fetchall()
        return {row["id"] for row in rows}

    def engram_ids_for_persona(self, persona_id: str, limit: int = 100000) -> set:
        """Return active engram ids for a persona partition."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id FROM engrams "
                "WHERE COALESCE(persona_id, '') = ? AND forgotten_at = 0 "
                "LIMIT ?",
                (persona_id or "", int(limit)),
            ).fetchall()
        return {row["id"] for row in rows}

    def list_active_by_entity_ref(self, entity_id: str, limit: int = 200) -> list:
        """Active engrams referencing an entity id, newest first.

        P2b (2026-08-11): SQL-level filter via json_each over the
        entity_refs JSON column instead of Python-list filtering.
        """
        sql = """SELECT * FROM engrams
                 WHERE EXISTS (
                   SELECT 1 FROM json_each(engrams.entity_refs)
                   WHERE json_each.value = ?
                 ) AND forgotten_at = 0
                 ORDER BY created_at DESC LIMIT ?"""
        try:
            with self._lock, self._conn:
                rows = self._conn.execute(sql, (entity_id, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [Engram.from_row(dict(r)) for r in rows]

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
                      persona_id: str | None = None,
                      scope_id: str | None = None,
                      memory_types: list[str] | None = None,
                      embedding_model: str | None = None):
        """Vector similarity search with SQL-side filter pushdown.

        Previously this loaded the entire engrams table (SELECT *) and
        filtered in Python on every recall. Now the filters are pushed
        into the WHERE clause and only lightweight columns (id +
        embedding_json) are loaded; the top-k ids are re-fetched as full
        rows. Semantics preserved except: rows without an embedding are
        skipped instead of scoring 0.0 (they were meaningless in vector
        recall and pollute RRF fusion).
        """
        where: list[str] = []
        params: list[object] = []
        # v1.76.4: soft-forgotten engrams must never re-enter vector recall.
        # soft_forget() keeps embedding_json for audit/recovery, so without
        # this clause the vector route can surface "forgotten" memories.
        where.append("COALESCE(forgotten_at, 0) = 0")
        if actor_id:
            where.append("actor_id = ?")
            params.append(actor_id)
        if channel_id:
            where.append("channel_id = ?")
            params.append(channel_id)
        if persona_id is not None:
            # Original Python filter normalised NULL/empty to ''; keep that.
            where.append("(COALESCE(persona_id, '') = ?)")
            params.append(persona_id)
        if scope_id is not None:
            where.append("(COALESCE(scope_id, '') = ?)")
            params.append(scope_id or "")
        if memory_types:
            placeholders = ",".join("?" for _ in memory_types)
            where.append(f"memory_type IN ({placeholders})")
            params.extend(memory_types)
        if embedding_model:
            where.append("embedding_model = ?")
            params.append(embedding_model)
        sql = "SELECT id, embedding_json FROM engrams"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock, self._conn:
            rows = self._conn.execute(sql, params).fetchall()
        scored: list[tuple[str, float]] = []
        for r in rows:
            try:
                emb = json.loads(r["embedding_json"]) if r["embedding_json"] else None
            except (json.JSONDecodeError, TypeError):
                emb = None
            if not emb:
                continue
            scored.append((r["id"], _cos(query_vec, emb)))
        scored.sort(key=lambda x: x[1], reverse=True)
        top_ids = [rid for rid, _s in scored[:k]]
        if not top_ids:
            return []
        placeholders = ",".join("?" for _ in top_ids)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"SELECT * FROM engrams WHERE id IN ({placeholders})", top_ids)
            by_id = {row["id"]: Engram.from_row(dict(row)) for row in cur.fetchall()}
        out: list[tuple[Engram, float]] = []
        for rid, score in scored[:k]:
            e = by_id.get(rid)
            if e is not None:
                out.append((e, score))
        return out

    def fts_search(self, query: str, k: int = 50, *,
                   actor_id: str | None = None, channel_id: str | None = None,
                   persona_id: str | None = None,
                   scope_id: str | None = None,
                   memory_types: list[str] | None = None,
                   embedding_model: str | None = None) -> list[tuple[Engram, float]]:
        """BM25 keyword search via FTS5. Returns (engram, similarity) where
        similarity is roughly in (0, 1] derived from -bm25/10.

        v1.76.4: filters are pushed into the JOIN so persona/actor/channel
        filtering happens BEFORE the LIMIT. Soft-forgotten rows are always
        excluded. ``embedding_model`` is accepted for API compatibility but
        intentionally not applied to keyword search: FTS text is model
        independent and old memories must stay reachable after a provider
        switch without a full vector rebuild.
        """
        if k <= 0:
            return []
        safe_q = self._sanitize_fts_query(query)
        if not safe_q:
            return []
        where = ["COALESCE(e.forgotten_at, 0) = 0"]
        params: list[object] = [safe_q]
        if actor_id:
            where.append("e.actor_id = ?")
            params.append(actor_id)
        if channel_id:
            where.append("e.channel_id = ?")
            params.append(channel_id)
        if persona_id is not None:
            where.append("(COALESCE(e.persona_id, '') = ?)")
            params.append(persona_id)
        if scope_id is not None:
            where.append("(COALESCE(e.scope_id, '') = ?)")
            params.append(scope_id or "")
        if memory_types:
            placeholders = ",".join("?" for _ in memory_types)
            where.append(f"e.memory_type IN ({placeholders})")
            params.extend(memory_types)
        sql = (
            "SELECT e.rowid AS _rid, e.*, bm25(engrams_fts) AS score "
            "FROM engrams_fts "
            "JOIN engrams e ON e.rowid = engrams_fts.rowid "
            "WHERE engrams_fts MATCH ? AND " + " AND ".join(where)
        )
        sql += " ORDER BY score LIMIT ?"
        params.append(int(k))
        with self._lock, self._conn:
            try:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[tuple[Engram, float]] = []
        for r in rows:
            try:
                bm = float(r["score"])
            except (TypeError, ValueError, KeyError):
                bm = 0.0
            e = Engram.from_row(dict(r))
            sim = max(0.0, min(1.0, -bm / 10.0))
            out.append((e, sim))
        return out

    def _sanitize_fts_query(self, q: str) -> str:
        """Drop FTS5 operators/special chars, tokenize per mode, join tokens.

        char mode AND-joins (each single char must match). bigram/jieba
        modes OR-join, because requiring every bigram/word to co-occur is
        too strict and tanks recall; OR keeps BM25 ranking meaningful."""
        if not q: return ""
        for ch in (chr(34), "(", ")", ":", "*", "+", "-", "^", "."):
            q = q.replace(ch, " ")
        from .tokenizer import tokenize
        mode = getattr(self, "_tokenizer_mode", "char")
        q = tokenize(q, mode)
        toks = [t for t in q.split() if t]
        if not toks: return ""
        joiner = " AND " if mode == "char" else " OR "
        return joiner.join(toks)

    def fts_count(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT COUNT(*) AS c FROM engrams_fts")
            return int(cur.fetchone()["c"])

    def import_fingerprints(self, limit: int = 200000) -> list[tuple[str, str, str, str]]:
        """Lightweight (content, session, persona, id) tuples for import dedup."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, content, COALESCE(session_id,'') AS sid, "
                "COALESCE(persona_id,'') AS pid FROM engrams "
                "WHERE COALESCE(forgotten_at,0)=0 LIMIT ?",
                (int(limit),)).fetchall()
        return [(r["content"] or "", r["sid"], r["pid"], r["id"]) for r in rows]

    def count_by_embedding_model(self, model: str, *,
                                 include_forgotten: bool = False) -> int:
        """Count engrams carrying `model` (empty model normalised to '')."""
        sql = ("SELECT COUNT(*) AS c FROM engrams "
               "WHERE COALESCE(embedding_model, '') = ?")
        if not include_forgotten:
            sql += " AND COALESCE(forgotten_at, 0) = 0"
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (model or "",))
            return int(cur.fetchone()["c"])

    def save_memory_source(self, memory_id: str, lines: list[dict]) -> None:
        """Persist the source transcript for one memory (audit/re-summarize)."""
        import time as _t
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO memory_sources(memory_id, source_json, source_count, created_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(memory_id) DO UPDATE SET "
                "source_json=excluded.source_json, source_count=excluded.source_count, "
                "created_at=excluded.created_at",
                (memory_id, json.dumps(lines or [], ensure_ascii=False),
                 len(lines or []), _t.time()))

    def get_memory_source(self, memory_id: str) -> list[dict]:
        try:
            with self._lock, self._conn:
                cur = self._conn.execute(
                    "SELECT source_json FROM memory_sources WHERE memory_id=? LIMIT 1",
                    (memory_id,))
                row = cur.fetchone()
        except sqlite3.OperationalError:
            return []
        if not row:
            return []
        try:
            data = json.loads(row["source_json"] or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def delete_memory_source(self, memory_id: str) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM memory_sources WHERE memory_id=?", (memory_id,))
        except sqlite3.OperationalError:
            pass

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

    def start_write_op(self, op_type: str, payload: dict | None = None,
                        memory_id: str | None = None) -> int | None:
        """Record the beginning of a multi-store write operation."""
        import time as _t
        now = _t.time()
        try:
            with self._lock, self._conn:
                cur = self._conn.execute(
                    "INSERT INTO memory_write_ops("
                    "op_type, memory_id, status, step, payload, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (op_type, memory_id, "pending", "started",
                     json.dumps(payload or {}, ensure_ascii=False), now, now))
                return int(cur.lastrowid)
        except Exception as _wex:
            print("[hippocampus] write_op start failed: " + repr(_wex))
            return None

    def advance_write_op(self, op_id: int | None, step: str, *,
                         status: str = "pending",
                         error: str | None = None) -> None:
        """Advance a write-operation log entry."""
        if op_id is None:
            return
        import time as _t
        try:
            with self._lock, self._conn:
                if status == "completed":
                    self._conn.execute(
                        "UPDATE memory_write_ops SET status=?, step=?,"
                        " updated_at=?, error=NULL WHERE id=?",
                        (status, step, _t.time(), op_id))
                else:
                    self._conn.execute(
                        "UPDATE memory_write_ops SET status=?, step=?,"
                        " updated_at=?, error=? WHERE id=?",
                        (status, step, _t.time(), (error or "")[:1000], op_id))
        except Exception as _wex:
            print("[hippocampus] write_op advance failed: " + repr(_wex))

    def list_incomplete_write_ops(self, limit: int = 200) -> list[dict]:
        """Return write ops that never reached 'completed'."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM memory_write_ops"
                " WHERE status != 'completed'"
                " ORDER BY id LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
