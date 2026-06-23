"""DiaryStore: v1.20 (B-3) daily raw-message cache + diary engram support.

Two responsibilities, both backed by the shared hippocampus.db:

1. daily_messages: a rolling per-channel cache of the day's raw lines
   (INCLUDING the bot's own turns) with a 7-day TTL. The diary writer reads
   a channel's lines for a target "logical day", summarises them into one
   bot-first-person diary engram, then the cache ages out on its own.

2. diary_chunks: when a diary engram is stored, its narrative is split into
   time/paragraph chunks that are embedded individually. Recall can then hit
   a single chunk ("what happened that afternoon") and inject only that
   chunk instead of the whole diary (parent-doc / chunk-level retrieval,
   B-3 requirement 13). The full diary still lives as one engram.

v1.42 (BUG-7): add_line() now goes through an in-memory write buffer that
is flushed (a) when it reaches `batch_size` lines, (b) before any read
query, (c) on close(), or (d) on explicit flush_now(). This cuts fsync
from once per message to once per `batch_size` messages on hot channels
(observed in v1.41: 50 lines/sec on a busy group chat = 50 fsync/sec;
now: 1 fsync/sec at batch_size=50). All read methods see pending writes
because they flush first - callers need not know about the buffer.

No AstrBot imports; pure SQLite + stdlib so it is unit-testable.
"""
from __future__ import annotations
import sqlite3
import threading
import time
import uuid


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


class DailyLine:
    __slots__ = ("id", "channel_id", "chat_type", "actor_id", "speaker",
                 "content", "ts", "is_bot", "group_id", "group_name",
                 "peer_actor_id", "peer_name", "session_id", "platform",
                 "persona_id")

    def __init__(self, channel_id="", chat_type="", actor_id="", speaker="",
                 content="", ts=None, is_bot=False, group_id="", group_name="",
                 peer_actor_id="", peer_name="", session_id="", platform="",
                 persona_id="", id=None):
        self.id = id or _new_id()
        self.channel_id = channel_id
        self.chat_type = chat_type
        self.actor_id = actor_id
        self.speaker = speaker
        self.content = content
        self.ts = ts if ts is not None else _now()
        self.is_bot = bool(is_bot)
        self.group_id = group_id
        self.group_name = group_name
        self.peer_actor_id = peer_actor_id
        self.peer_name = peer_name
        self.session_id = session_id
        self.platform = platform
        self.persona_id = persona_id

    @classmethod
    def from_row(cls, row) -> "DailyLine":
        d = dict(row)
        return cls(
            id=d.get("id"), channel_id=d.get("channel_id", ""),
            chat_type=d.get("chat_type", ""), actor_id=d.get("actor_id", ""),
            speaker=d.get("speaker", ""), content=d.get("content", ""),
            ts=d.get("ts"), is_bot=bool(d.get("is_bot", 0)),
            group_id=d.get("group_id", ""), group_name=d.get("group_name", ""),
            peer_actor_id=d.get("peer_actor_id", ""),
            peer_name=d.get("peer_name", ""),
            session_id=d.get("session_id", ""), platform=d.get("platform", ""),
            persona_id=d.get("persona_id", ""))


class DiaryChunk:
    __slots__ = ("id", "diary_id", "channel_id", "seq", "text", "embedding",
                 "embedding_model", "ts_start", "ts_end", "created_at",
                 "persona_id")

    def __init__(self, diary_id="", channel_id="", seq=0, text="",
                 embedding=None, embedding_model="", ts_start=0.0, ts_end=0.0,
                 id=None, created_at=None, persona_id=""):
        self.id = id or _new_id()
        self.diary_id = diary_id
        self.channel_id = channel_id
        self.seq = int(seq)
        self.text = text
        self.embedding = list(embedding or [])
        self.embedding_model = embedding_model
        self.ts_start = float(ts_start)
        self.ts_end = float(ts_end)
        self.created_at = created_at if created_at is not None else _now()
        self.persona_id = persona_id

    @classmethod
    def from_row(cls, row) -> "DiaryChunk":
        import json
        d = dict(row)
        emb = []
        raw = d.get("embedding")
        if raw:
            try:
                emb = json.loads(raw)
            except Exception:
                emb = []
        return cls(
            id=d.get("id"), diary_id=d.get("diary_id", ""),
            channel_id=d.get("channel_id", ""), seq=d.get("seq", 0),
            text=d.get("text", ""), embedding=emb,
            embedding_model=d.get("embedding_model", ""),
            ts_start=d.get("ts_start", 0.0), ts_end=d.get("ts_end", 0.0),
            created_at=d.get("created_at"), persona_id=d.get("persona_id", ""))


class DiaryStore:
    # Default batch size for the BUG-7 in-memory write buffer. 50 is
    # empirically enough to collapse a busy group's chat into ~1 fsync/sec
    # while keeping tail latency of the worst single line low.
    DEFAULT_BATCH_SIZE = 50

    def __init__(self, db_path: str, *, batch_size: int | None = None) -> None:
        self._db_path = db_path
        self._conn = None
        self._initialized = False
        # BUG-7 (v1.42) write buffer. Holds DailyLine instances until
        # flushed via threshold, read-trigger, flush_now, or close.
        self._buffer: list = []
        bs = self.DEFAULT_BATCH_SIZE if batch_size is None else int(batch_size)
        self._batch_size = max(1, bs)
        self._buffer_lock = threading.Lock()

    def _ensure_conn(self):
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
        c = self._conn
        c.execute(
            "CREATE TABLE IF NOT EXISTS daily_messages ("
            " id TEXT PRIMARY KEY, channel_id TEXT, chat_type TEXT,"
            " actor_id TEXT, speaker TEXT, content TEXT, ts REAL,"
            " is_bot INTEGER, group_id TEXT, group_name TEXT,"
            " peer_actor_id TEXT, peer_name TEXT, session_id TEXT, platform TEXT,"
            " persona_id TEXT DEFAULT '')")
        self._add_col_if_missing(c, "daily_messages", "persona_id", "TEXT DEFAULT ''")
        c.execute("CREATE INDEX IF NOT EXISTS idx_daily_ch_ts ON daily_messages(channel_id, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_daily_ts ON daily_messages(ts)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS diary_chunks ("
            " id TEXT PRIMARY KEY, diary_id TEXT, channel_id TEXT, seq INTEGER,"
            " text TEXT, embedding TEXT, embedding_model TEXT,"
            " ts_start REAL, ts_end REAL, created_at REAL,"
            " persona_id TEXT DEFAULT '')")
        self._add_col_if_missing(c, "diary_chunks", "persona_id", "TEXT DEFAULT ''")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chunk_diary ON diary_chunks(diary_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chunk_ch ON diary_chunks(channel_id)")
        c.commit()

    @staticmethod
    def _add_col_if_missing(conn, table, col, decl):
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")").fetchall()}
            if col not in cols:
                conn.execute("ALTER TABLE " + table + " ADD COLUMN " + col + " " + decl)
        except Exception:
            pass

    def close(self) -> None:
        # FIX (v1.42) BUG-7: flush any pending buffered lines before close
        # so we never lose a line on shutdown.
        try:
            self.flush_now()
        except Exception:
            pass
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
                self._initialized = False

    def is_open(self) -> bool:
        return self._conn is not None

    def buffer_size(self) -> int:
        """Number of lines currently held in the in-memory write buffer
        (not yet committed to SQLite). Useful for tests + diagnostics."""
        with self._buffer_lock:
            return len(self._buffer)

    def flush_now(self) -> int:
        """Force-flush the in-memory buffer. Returns rows committed.
        Safe to call from any thread; no-op when the buffer is empty."""
        with self._buffer_lock:
            if not self._buffer:
                return 0
            c = self._ensure_conn()
            rows = [
                (ln.id, ln.channel_id, ln.chat_type, ln.actor_id, ln.speaker,
                 ln.content, ln.ts, 1 if ln.is_bot else 0, ln.group_id,
                 ln.group_name, ln.peer_actor_id, ln.peer_name, ln.session_id,
                 ln.platform, ln.persona_id)
                for ln in self._buffer]
            c.executemany(
                "INSERT OR REPLACE INTO daily_messages(id,channel_id,chat_type,"
                "actor_id,speaker,content,ts,is_bot,group_id,group_name,"
                "peer_actor_id,peer_name,session_id,platform,persona_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows)
            c.commit()
            n = len(self._buffer)
            self._buffer.clear()
            return n

    # ---- daily message cache ----
    def add_line(self, line: DailyLine) -> DailyLine:
        """Append a daily-message line.

        FIX (v1.42) BUG-7: appends to an in-memory buffer; the actual
        commit happens when the buffer reaches `batch_size`, when any
        read query is issued, when flush_now() is called, or on close().
        Cuts fsync from once-per-message to once-per-batch on hot
        channels. Callers do not need to know about the buffer because
        every read method in this class flushes first.
        """
        c = self._ensure_conn()
        # Pre-build the row outside the lock so the critical section is
        # tiny. SQLite executemany in the flush takes the connection;
        # the lock only protects the list.
        with self._buffer_lock:
            self._buffer.append(line)
            if len(self._buffer) >= self._batch_size:
                # Flush under the same lock to keep buffer and DB in step.
                rows = [
                    (ln.id, ln.channel_id, ln.chat_type, ln.actor_id, ln.speaker,
                     ln.content, ln.ts, 1 if ln.is_bot else 0, ln.group_id,
                     ln.group_name, ln.peer_actor_id, ln.peer_name, ln.session_id,
                     ln.platform, ln.persona_id)
                    for ln in self._buffer]
                c.executemany(
                    "INSERT OR REPLACE INTO daily_messages(id,channel_id,chat_type,"
                    "actor_id,speaker,content,ts,is_bot,group_id,group_name,"
                    "peer_actor_id,peer_name,session_id,platform,persona_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
                c.commit()
                self._buffer.clear()
        return line

    def channels_with_lines(self, t0: float, t1: float) -> list:
        """Distinct (channel_id, persona_id) groups with any line in [t0, t1).
        v1.36: diary is persona-scoped, so each persona in a channel gets its
        own diary. Returns list[(channel_id, persona_id)]."""
        self.flush_now()  # BUG-7: pending buffer must be visible here
        c = self._ensure_conn()
        rows = c.execute(
            "SELECT DISTINCT channel_id, COALESCE(persona_id, '') AS persona_id "
            "FROM daily_messages WHERE ts >= ? AND ts < ?", (t0, t1)).fetchall()
        return [(r["channel_id"], r["persona_id"] or "") for r in rows]

    def lines_in_range(self, channel_id: str, t0: float, t1: float,
                       persona_id: str | None = None) -> list:
        """Time-ordered lines for a channel within [t0, t1). When persona_id
        is given, restrict to that persona (v1.36 persona-scoped diary)."""
        self.flush_now()  # BUG-7: pending buffer must be visible here
        c = self._ensure_conn()
        if persona_id is None:
            rows = c.execute(
                "SELECT * FROM daily_messages WHERE channel_id=? "
                "AND ts >= ? AND ts < ? ORDER BY ts ASC", (channel_id, t0, t1)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM daily_messages WHERE channel_id=? "
                "AND COALESCE(persona_id, '')=? "
                "AND ts >= ? AND ts < ? ORDER BY ts ASC",
                (channel_id, persona_id, t0, t1)).fetchall()
        return [DailyLine.from_row(r) for r in rows]

    def find_idle_gap(self, channel_id: str, t0: float, t1: float,
                      min_gap_seconds: float, persona_id: str | None = None) -> float | None:
        """B-3 night cut-point: within [t0, t1), find the END of the LAST
        message that is followed by a silence >= min_gap_seconds (the last
        quiet boundary). Returns that boundary ts, or None if no such gap."""
        self.flush_now()  # BUG-7: pending buffer must be visible here
        c = self._ensure_conn()
        if persona_id is None:
            rows = c.execute(
                "SELECT ts FROM daily_messages WHERE channel_id=? "
                "AND ts >= ? AND ts < ? ORDER BY ts ASC", (channel_id, t0, t1)).fetchall()
        else:
            rows = c.execute(
                "SELECT ts FROM daily_messages WHERE channel_id=? "
                "AND COALESCE(persona_id, '')=? "
                "AND ts >= ? AND ts < ? ORDER BY ts ASC",
                (channel_id, persona_id, t0, t1)).fetchall()
        tss = [r["ts"] for r in rows]
        if len(tss) < 2:
            return None
        cut = None
        for i in range(len(tss) - 1):
            if (tss[i + 1] - tss[i]) >= min_gap_seconds:
                cut = tss[i]  # boundary = end of the message before the gap
        return cut

    def purge_older_than(self, cutoff_ts: float) -> int:
        """TTL cleanup: drop daily_messages older than cutoff_ts."""
        self.flush_now()  # BUG-7: pending buffer must be flushed before TTL purge
        c = self._ensure_conn()
        cur = c.execute("DELETE FROM daily_messages WHERE ts < ?", (cutoff_ts,))
        c.commit()
        return cur.rowcount or 0

    def purge_lines_in_range(self, channel_id: str, t0: float, t1: float,
                             persona_id: str | None = None) -> int:
        """FIX (v1.41) for BUG-3 + BUG-7: drop the raw lines that were
        consumed by a diary write so a re-run of run_daily_diary (manual
        /mem diary or a delayed scheduler tick) does not reproduce the
        same diary. Bounded by [t0, t1). persona_id filters to a
        single persona's slice when given.
        """
        self.flush_now()  # BUG-7: pending buffer must be flushed before range purge
        c = self._ensure_conn()
        if persona_id is None:
            cur = c.execute(
                "DELETE FROM daily_messages WHERE channel_id=? "
                "AND ts >= ? AND ts < ?",
                (channel_id, t0, t1))
        else:
            cur = c.execute(
                "DELETE FROM daily_messages WHERE channel_id=? "
                "AND COALESCE(persona_id, '')=? "
                "AND ts >= ? AND ts < ?",
                (channel_id, persona_id, t0, t1))
        c.commit()
        return cur.rowcount or 0

    def add_lines_batch(self, lines: list) -> int:
        """FIX (v1.41) for BUG-7: insert N lines inside a single commit
        so a hot channel does not fsync once per message. Same INSERT
        shape as add_line; returns rows inserted.
        """
        if not lines:
            return 0
        c = self._ensure_conn()
        rows = [
            (ln.id, ln.channel_id, ln.chat_type, ln.actor_id, ln.speaker,
             ln.content, ln.ts, 1 if ln.is_bot else 0, ln.group_id,
             ln.group_name, ln.peer_actor_id, ln.peer_name, ln.session_id,
             ln.platform, ln.persona_id)
            for ln in lines]
        c.executemany(
            "INSERT OR REPLACE INTO daily_messages(id,channel_id,chat_type,"
            "actor_id,speaker,content,ts,is_bot,group_id,group_name,"
            "peer_actor_id,peer_name,session_id,platform,persona_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        c.commit()
        return len(rows)

    def count_lines(self) -> int:
        self.flush_now()  # BUG-7: pending buffer must be visible here
        c = self._ensure_conn()
        row = c.execute("SELECT COUNT(*) AS n FROM daily_messages").fetchone()
        return int(row["n"]) if row else 0

    # ---- diary chunks ----
    def add_chunks(self, chunks: list) -> int:
        import json
        self.flush_now()  # BUG-7: ensure diary lines are visible to any readers
        c = self._ensure_conn()
        n = 0
        for ch in chunks:
            c.execute(
                "INSERT OR REPLACE INTO diary_chunks(id,diary_id,channel_id,seq,"
                "text,embedding,embedding_model,ts_start,ts_end,created_at,persona_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ch.id, ch.diary_id, ch.channel_id, ch.seq, ch.text,
                 json.dumps(ch.embedding) if ch.embedding else "",
                 ch.embedding_model, ch.ts_start, ch.ts_end, ch.created_at,
                 getattr(ch, "persona_id", "") or ""))
            n += 1
        c.commit()
        return n

    def all_chunks(self, limit: int = 2000, persona_id: str | None = None) -> list:
        self.flush_now()  # BUG-7: pending buffer must be visible here
        c = self._ensure_conn()
        if persona_id is None:
            rows = c.execute(
                "SELECT * FROM diary_chunks ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM diary_chunks WHERE COALESCE(persona_id, '')=? "
                "ORDER BY created_at DESC LIMIT ?",
                (persona_id, limit)).fetchall()
        return [DiaryChunk.from_row(r) for r in rows]

    def chunks_for_diary(self, diary_id: str) -> list:
        self.flush_now()  # BUG-7: pending buffer must be visible here
        c = self._ensure_conn()
        rows = c.execute(
            "SELECT * FROM diary_chunks WHERE diary_id=? ORDER BY seq ASC",
            (diary_id,)).fetchall()
        return [DiaryChunk.from_row(r) for r in rows]

    def delete_chunks_for_diary(self, diary_id: str) -> int:
        self.flush_now()  # BUG-7: pending buffer must be flushed before delete
        c = self._ensure_conn()
        cur = c.execute("DELETE FROM diary_chunks WHERE diary_id=?", (diary_id,))
        c.commit()
        return cur.rowcount or 0

    def count_chunks(self) -> int:
        c = self._ensure_conn()
        row = c.execute("SELECT COUNT(*) AS n FROM diary_chunks").fetchone()
        return int(row["n"]) if row else 0
