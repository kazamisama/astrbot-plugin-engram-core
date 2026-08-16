"""Smoke v1.76.4: recall/backup/initializer hardening batch.

Covers fixes for the 2026-08 review:
  H1  soft-forgotten engrams are excluded from vector / fts / hybrid recall
  H2  backup uses the online SQLite backup API (WAL-consistent while open)
      and PluginInitializer starts exactly one backup scheduler thread
  H3  astrmock is only auto-activated when the host embedding probe works;
      FTS stays reachable for older embedding_model rows after a switch
  M1  recall cache keys distinguish valence / activation / config changes
  M2  FTS persona filtering happens in SQL (verified end-to-end)
  M3  list_active filters forgotten_at before LIMIT
  M4  working-memory cells resolve by both session_id and channel_id
"""
import os
import sys
import tempfile
import threading
import time
import types


def _install_stub():
    a = types.ModuleType("astrbot")
    ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star")
    em = types.ModuleType("astrbot.api.event")

    class Star:
        pass

    def register(*_a, **_k):
        def deco(cls):
            return cls
        return deco

    class Context:
        pass

    class AstrMessageEvent:
        pass

    class _MT:
        ALL = "all"

    class _F:
        EventMessageType = _MT

        def event_message_type(self, *_a, **_k):
            def deco(fn):
                return fn
            return deco

        def command(self, *_a, **_k):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def on_llm_request(*_a, **_k):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def on_llm_response(*_a, **_k):
            def deco(fn):
                return fn
            return deco

    sm.Star = Star
    sm.register = register
    sm.Context = Context
    em.filter = _F
    em.AstrMessageEvent = AstrMessageEvent
    em.EventMessageType = _MT
    sys.modules["astrbot"] = a
    sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm
    sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import sqlite3

from hippocampus.config import MemoryConfig
from hippocampus.service import MemoryService
from hippocampus.types import Cue, Engram
from hippocampus.working_memory import WorkingMemory
from hippocampus.providers import ProxyEmbeddingProvider


def _cfg(path):
    cfg = MemoryConfig(sqlite_path=path)
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.enable_atom_extraction = False
    cfg.enable_graph_indexing = False
    cfg.memory_decay_enabled = False
    cfg.dedup_enabled = False
    return cfg


def test_forgotten_and_list_active_and_m4():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "h.db")
    svc = MemoryService(_cfg(path))

    now = time.time()
    forgotten = Engram(id="forgotten", actor_id="u1", channel_id="g1",
                       content="soft forgotten secret", summary="soft forgotten secret",
                       created_at=now, embedding_model=svc._current_embedding_name)
    forgotten.embedding = svc.embedder.embed(forgotten.content)
    svc.store.upsert(forgotten)
    svc.store.soft_forget("forgotten")

    for mode in ("vector", "fts", "hybrid"):
        res = svc.recall(Cue(text="forgotten secret", actor_id="u1", k=5, mode=mode))
        assert not res.engrams, (mode, [e.id for e in res.engrams])

    active = Engram(id="active", actor_id="u1", channel_id="g1",
                    content="active memory", summary="active memory",
                    created_at=now - 100, embedding_model=svc._current_embedding_name)
    svc.store.upsert(active)
    got = svc.store.list_active(limit=1)
    assert [e.id for e in got] == ["active"], [e.id for e in got]

    # A cached recall result must be invalidated by the user-facing forget
    # path (previously /mem forget could leave the engram in cache for TTL).
    assert svc.recall(Cue(text="active memory", actor_id="u1", k=5)).engrams
    from handlers.format import find_and_forget
    out = find_and_forget(svc, "active")
    assert "forgot engram" in out, out
    assert not svc.recall(Cue(text="active memory", actor_id="u1", k=5)).engrams
    svc.close()

    wm = WorkingMemory(MemoryConfig())
    g = Engram(id="g", session_id="session-x", channel_id="group-x")
    wm.add(g)
    assert wm.snapshot("session-x") and wm.snapshot("group-x")
    assert wm.snapshot("session-x")[0].id == "g"
    assert not wm.drain("session-x") or not wm.snapshot("group-x")
    print("H1 + M3 + M4: OK")


def test_fts_model_independence_and_cache_keys():
    tmp = tempfile.mkdtemp()
    svc = MemoryService(_cfg(os.path.join(tmp, "h.db")))

    old = Engram(id="old", actor_id="u1", channel_id="g1",
                 content="old password 1234", summary="old password 1234",
                 embedding_model="hash")
    old.embedding = svc.embedder.embed(old.content)
    svc.store.upsert(old)
    svc.register_embedding("astrmock",
                           ProxyEmbeddingProvider("astrmock", lambda t: [0.1] * 64))
    svc.cfg.auto_rebuild_on_switch = False
    svc.set_embedding("astrmock")

    res = svc.recall(Cue(text="old password", actor_id="u1", k=5, mode="fts"))
    assert [e.id for e in res.engrams] == ["old"], [e.id for e in res.engrams]

    cue_a = Cue(text="q", actor_id="u1", k=1, valence_hint=1.0)
    cue_b = Cue(text="q", actor_id="u1", k=1, valence_hint=-1.0)
    assert svc._recall_cache_key(cue_a) != svc._recall_cache_key(cue_b)
    cue_c = Cue(text="q", actor_id="u1", k=1, activation={"old": 0.5})
    assert svc._recall_cache_key(cue_a) != svc._recall_cache_key(cue_c)
    old_fp = svc._recall_config_fingerprint()
    svc.cfg.tier_cold_fallback_min_hits = 0
    assert svc._recall_config_fingerprint() != old_fp
    svc.close()
    print("H3-fts + M1: OK")


def test_fts_persona_filter_before_limit():
    tmp = tempfile.mkdtemp()
    svc = MemoryService(_cfg(os.path.join(tmp, "h.db")))
    svc.cfg.recall_candidate_k = 5
    model = svc._current_embedding_name
    # More non-matching personas than the old FTS pre-filter window
    # (k * 4 = 20). The matching persona row is inserted last and would
    # fall outside the old global top-k before the Python-side filter.
    for i in range(25):
        dog = Engram(id=f"dog-{i}", actor_id="u1", channel_id="g1",
                     persona_id="dog", content="shared keyword",
                     summary="shared keyword", embedding_model=model)
        svc.store.upsert(dog)
    cat = Engram(id="cat", actor_id="u1", channel_id="g1",
                 persona_id="cat", content="shared keyword",
                 summary="shared keyword", embedding_model=model)
    svc.store.upsert(cat)
    res = svc.recall(Cue(text="shared keyword", actor_id="u1",
                         persona_id="cat", k=5, mode="fts"))
    pids = {getattr(e, "persona_id", "") for e in res.engrams}
    assert pids == {"cat"}, pids
    assert any(e.id == "cat" for e in res.engrams)
    svc.close()
    print("M2: OK")


async def _test_observe_offloads_blocking_ingest():
    from handlers.event.observe import ObserveHandler

    class _BlockingService:
        cfg = types.SimpleNamespace(summary_mode_enabled=False,
                                    per_message_ingest_debug=False,
                                    diary_enabled=False,
                                    session_aggregate_enabled=False)

        def cache_daily_line(self, meta):
            time.sleep(0.15)

        def observe(self, **kwargs):
            time.sleep(0.15)

    class _BlockingEvent:
        unified_msg_origin = "sess"
        message_str = "hello"

        def get_sender_id(self):
            return "u1"

        def get_platform_name(self):
            return "test"

        def get_group_id(self):
            return ""

        def get_sender_name(self):
            return "u1"

        def get_extra(self, key):
            return None

    h = ObserveHandler(_BlockingService())
    last = [time.monotonic()]
    gaps = []

    async def ticker():
        while True:
            now = time.monotonic()
            gaps.append(now - last[0])
            last[0] = now
            await asyncio.sleep(0.01)

    tick = asyncio.create_task(ticker())
    await h.handle_message(_BlockingEvent())
    tick.cancel()
    try:
        await tick
    except asyncio.CancelledError:
        pass
    # A blocked event loop would show one ~0.3s gap; worker-thread ingest
    # keeps the loop ticking through the whole blocking operation.
    assert max(gaps) < 0.1, gaps


def test_observe_ingest_does_not_block_event_loop():
    asyncio.run(_test_observe_offloads_blocking_ingest())
    print("M5: OK")


def test_backup_api_and_single_scheduler():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "live.db")
    bdir = os.path.join(tmp, "backups")
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE engrams (id TEXT PRIMARY KEY, content TEXT);
        INSERT INTO engrams VALUES ('e1', 'wal-persisted');
    """)
    conn.commit()
    # keep the connection open, like the live service does
    from hippocampus.managers.backup_manager import BackupManager
    bm = BackupManager(src, bdir, version_provider=lambda: "v1.76.4")
    rec = bm.create(reason="manual")
    conn.close()
    check = sqlite3.connect(rec.db_path)
    rows = list(check.execute("SELECT id, content FROM engrams"))
    check.close()
    assert rows == [("e1", "wal-persisted")], rows

    from handlers.init import PluginInitializer

    from astrbot.api.star import Context as _StubContext

    class Ctx(_StubContext):
        def get_config(self, key):
            return {
                "sqlite_path": os.path.join(tmp, "init.db"),
                "enable_backup": True,
                "backup_interval_hours": 24.0,
                "enable_semantic": False,
                "enable_prospective": False,
                "enable_profile": False,
                "enable_persona": False,
                "enable_atom_extraction": False,
                "enable_graph_indexing": False,
                "memory_decay_enabled": False,
                "summary_mode_enabled": False,
                "diary_enabled": False,
                "auto_inject_enabled": False,
                "session_aggregate_enabled": False,
                "dedup_enabled": False,
                "tokenizer_mode": "char",
            }

        def get_using_provider(self):
            raise RuntimeError("no host provider")

        def register_tool(self, *_a, **_k):
            pass

    p = PluginInitializer(Ctx())
    p.initialize()
    names = [t.name for t in threading.enumerate() if "hippocampus-backup" in t.name]
    assert len(names) == 1, names
    # no host embedding provider => keep hash instead of switching to empty astrmock
    assert p.service.current_embedding() == "hash", p.service.current_embedding()
    try:
        p.service.close()
    except Exception:
        pass
    print("H2 + H3-provider gate: OK")


def main():
    test_forgotten_and_list_active_and_m4()
    test_fts_model_independence_and_cache_keys()
    test_fts_persona_filter_before_limit()
    test_observe_ingest_does_not_block_event_loop()
    test_backup_api_and_single_scheduler()
    print("\nv1.76.4 smoke: ALL PASS")


if __name__ == "__main__":
    main()
