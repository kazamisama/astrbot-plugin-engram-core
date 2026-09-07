"""Smoke v1.76.15: audit fixes for bounded memory + maintenance.

Covers:
- WorkingMemory: hard cell cap (LRU) + idle eviction
- ConversationBuffer: sub-min channel grace drop + hard channel cap
- HippocampalStore: completed write-op purge + memory-source purge
- MemoryService: prospective scheduler is started and stopped
"""
import os, sys, tempfile, time, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig
from hippocampus.working_memory import WorkingMemory
from hippocampus.conversation_buffer import ConversationBuffer
from hippocampus.types import Engram


def _new_svc(**over):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    base = dict(embedding_dim=32,
                enable_prospective=False,
                enable_semantic=False, enable_profile=False,
                enable_atom_extraction=False,
                enable_graph_indexing=False,
                memory_decay_enabled=False,
                diary_enabled=False,
                dedup_enabled=False)
    base.update(over)
    cfg = MemoryConfig(sqlite_path=tmp.name, **base)
    svc = MemoryService(cfg)
    return svc, tmp.name


def test_working_memory_bounded():
    cfg = MemoryConfig(working_memory_capacity=4,
                       working_memory_max_cells=2,
                       working_memory_idle_seconds=3600.0)
    wm = WorkingMemory(cfg)
    for i in range(3):
        wm.add(Engram(id=f"e{i}", session_id=f"s{i}", channel_id=f"c{i}"))
    assert wm.snapshot("s0") == [], "oldest cell should be LRU-evicted"
    assert wm.snapshot("s1"), "middle cell should survive"
    assert wm.snapshot("s2"), "newest cell should survive"
    wm.evict_idle(now=time.time() + 7200.0)
    assert wm.snapshot("s1") == [] and wm.snapshot("s2") == []
    print("working memory cap + idle eviction: OK")


def test_conversation_buffer_grace_and_cap():
    class Clock:
        def __init__(self): self.t = 1000.0
        def __call__(self): return self.t
        def tick(self, dt): self.t += dt

    clk = Clock()
    flushed = []
    cfg = MemoryConfig()
    cfg.summary_idle_seconds_group = 600.0
    cfg.summary_min_messages = 3
    cfg.summary_min_messages_grace_seconds = 1000.0
    cfg.summary_max_channels = 2
    buf = ConversationBuffer(cfg, lambda rec: flushed.append(rec), now_fn=clk)
    buf.feed({"channel_id": "g1", "chat_type": "group", "actor_id": "a", "content": "one"})
    clk.tick(1001)
    buf.flush_idle_now()
    assert buf.buffered_channel_count() == 0, "sub-min channel must drop after grace"
    assert len(flushed) == 0

    clk.tick(1)
    for ch in ("g1", "g2", "g3"):
        buf.feed({"channel_id": ch, "chat_type": "group", "actor_id": "a", "content": "x"})
    assert buf.buffered_channel_count() == 2, "channel cap should evict oldest"
    buf.flush_all()
    assert {r.channel_id for r in flushed} == {"g2", "g3"}
    print("conversation buffer grace + channel cap: OK")


def test_write_op_and_source_purge():
    svc, db = _new_svc()
    try:
        eid = "mem-1"
        op = svc.store.start_write_op("post_ingest", {"engram_id": eid}, memory_id=eid)
        assert op is not None
        svc.store.advance_write_op(op, "complete", status="completed")
        svc.store.save_memory_source(eid, [{"content": "hello"}])
        old = time.time() - 10 * 86400.0
        with svc.store._lock, svc.store._conn:
            svc.store._conn.execute("UPDATE memory_write_ops SET updated_at=?", (old,))
            svc.store._conn.execute("UPDATE memory_sources SET created_at=?", (old,))
            svc.store._conn.commit()
        assert svc.store.purge_completed_write_ops(7 * 86400.0) == 1
        assert svc.store.purge_memory_sources(7 * 86400.0) == 1
        with svc.store._lock:
            n = svc.store._conn.execute("SELECT COUNT(*) c FROM memory_write_ops").fetchone()["c"]
            m = svc.store._conn.execute("SELECT COUNT(*) c FROM memory_sources").fetchone()["c"]
        assert n == 0 and m == 0
        print("write-op + memory-source purge: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_prospective_loop_start_stop():
    svc, db = _new_svc(enable_prospective=True,
                       prospective_check_interval=0.05)

    async def _run():
        await svc.start()
        assert svc._prospective_task is not None and not svc._prospective_task.done()
        await svc.stop()
        assert svc._prospective_task is None

    try:
        asyncio.run(_run())
        print("prospective scheduler start/stop: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def main():
    test_working_memory_bounded()
    test_conversation_buffer_grace_and_cap()
    test_write_op_and_source_purge()
    test_prospective_loop_start_stop()
    print("\nv85 audit-fix smoke: ALL PASS")


if __name__ == "__main__":
    main()
