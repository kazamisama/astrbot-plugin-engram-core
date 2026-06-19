from __future__ import annotations
import json, re, sqlite3, threading, time
from datetime import datetime
from typing import Callable
from .types import Trigger, Engram
from .config import MemoryConfig

class ProspectiveStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS triggers (
              id TEXT PRIMARY KEY,
              kind TEXT, payload TEXT, fire_at REAL,
              status TEXT, created_engram_id TEXT, created_at REAL,
              fired_at REAL, actor_id TEXT, channel_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trig_status ON triggers(status);
            CREATE INDEX IF NOT EXISTS idx_trig_fire ON triggers(fire_at);
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(self, t: Trigger) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO triggers(id,kind,payload,fire_at,status,created_engram_id,created_at,fired_at,actor_id,channel_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (t.id, t.kind, json.dumps(t.payload, ensure_ascii=False),
                 t.fire_at, t.status, t.created_engram_id, t.created_at,
                 t.fired_at, t.actor_id, t.channel_id))

    def list(self, status: str | None = "pending") -> list[Trigger]:
        with self._lock, self._conn:
            if status is None:
                cur = self._conn.execute("SELECT * FROM triggers")
            else:
                cur = self._conn.execute("SELECT * FROM triggers WHERE status=?", (status,))
            return [Trigger.from_row(dict(r)) for r in cur.fetchall()]

    def get(self, tid: str) -> Trigger | None:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM triggers WHERE id=?", (tid,))
            row = cur.fetchone()
        return Trigger.from_row(dict(row)) if row else None

    def mark_fired(self, tid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE triggers SET status='fired', fired_at=? WHERE id=?",
                (time.time(), tid))

    def cancel(self, tid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE triggers SET status='cancelled' WHERE id=?", (tid,))

    def due(self, now: float | None = None) -> list[Trigger]:
        now = now or time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM triggers WHERE status='pending' AND fire_at<=? ORDER BY fire_at",
                (now,))
            return [Trigger.from_row(dict(r)) for r in cur.fetchall()]


def _today(now: float, hour: int) -> float:
    d = datetime.fromtimestamp(now)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()

def _tomorrow(now: float, hour: int) -> float:
    return _today(now, hour) + 86400

def _plus_days(now: float, days: int, hour: int) -> float:
    return _today(now, hour) + days * 86400

def _english_hour(s: str | None) -> int:
    if not s: return 9
    s = s.strip().lower()
    if "morning" in s: return 8
    if "afternoon" in s: return 14
    if "evening" in s: return 19
    return 9


class TimeParser:
    """规则版时间解析。LLM 版可换。"""
    _PATTERNS: list[tuple[str, Callable]] = [
        (r"明早",               lambda m, now: _tomorrow(now, 8)),
        (r"明天下午",            lambda m, now: _tomorrow(now, 14)),
        (r"明天(上午|早上|早晨)?", lambda m, now: _tomorrow(now, 9)),
        (r"今晚",               lambda m, now: _today(now, 20)),
        (r"今天下午",            lambda m, now: _today(now, 14)),
        (r"后天(上午|早上|下午)?", lambda m, now: _plus_days(now, 2, 9)),
        (r"下周",                lambda m, now: _plus_days(now, 7, 9)),
        (r"tomorrow( morning| afternoon| evening)?",
                                  lambda m, now: _tomorrow(now, _english_hour(m.group(1)))),
        (r"tonight",             lambda m, now: _today(now, 20)),
        (r"next week",           lambda m, now: _plus_days(now, 7, 9)),
        (r"in (\d+) hours?",     lambda m, now: now + int(m.group(1)) * 3600),
        (r"in (\d+) days?",      lambda m, now: now + int(m.group(1)) * 86400),
    ]

    @classmethod
    def parse(cls, text: str, now: float | None = None) -> float | None:
        now = now or time.time()
        t = text.lower()
        for pat, fn in cls._PATTERNS:
            m = re.search(pat, t)
            if m: return fn(m, now)
        return None


class ProspectiveScheduler:
    def __init__(self, store: ProspectiveStore, cfg: MemoryConfig,
                 on_fire: Callable[[Trigger], None] | None = None) -> None:
        self._store = store
        self._cfg = cfg
        self._on_fire = on_fire or (lambda t: None)

    def step(self) -> list[Trigger]:
        fired: list[Trigger] = []
        for t in self._store.due():
            try: self._on_fire(t)
            except Exception as e:
                print(f"[hippocampus] trigger on_fire error: {e!r}")
            self._store.mark_fired(t.id)
            fired.append(t)
        return fired

    def create_from_engram(self, engram: Engram) -> Trigger | None:
        if "plan" not in engram.topics:
            return None
        fire_at = TimeParser.parse(engram.content, now=engram.created_at)
        if fire_at is None or fire_at <= engram.created_at:
            return None
        t = Trigger(
            kind="at_time",
            payload={"reminder": engram.summary, "raw": engram.content},
            fire_at=fire_at, status="pending",
            created_engram_id=engram.id,
            created_at=engram.created_at,
            actor_id=engram.actor_id,
            channel_id=engram.channel_id,
        )
        self._store.add(t)
        return t