"""Smoke v1.75: cross-plugin public API (diary line + recall + leases).

Covers:
  - MemoryService.store_diary_line writes a persona-scoped diary engram
    with source/day/mood/signature/ref tags and returns its id
  - query_recent_memory returns persona-scoped newest-first dicts and
    honors the `since` floor
  - TaskLeaseStore claim/renew/release exclusivity, expired reclaim and
    cleanup_expired
"""
import os
import sys
import tempfile
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


def _build_service(tmp):
    from hippocampus.config import MemoryConfig
    from hippocampus.service import MemoryService

    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "hippo.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    return MemoryService(cfg=cfg), cfg


def test_store_diary_line_roundtrip():
    tmp = tempfile.mkdtemp()
    svc, _cfg = _build_service(tmp)
    eid = svc.store_diary_line(
        "shelly", "2026-08-12", "今天看了一篇关于 RAG 的文章。",
        mood="curious", signature="今天的风",
        source_refs=["https://example.com/1"], source="your_own_life",
    )
    assert eid, "store_diary_line must return an engram id"
    e = svc.store.get(eid)
    assert e is not None
    assert e.persona_id == "shelly"
    assert e.memory_type == "diary"
    tags = set(e.tags)
    assert "source:your_own_life" in tags
    assert "day:2026-08-12" in tags
    assert "mood:curious" in tags
    assert "signature:今天的风" in tags
    assert "ref:https://example.com/1" in tags
    assert svc.store_diary_line("shelly", "2026-08-12", "  ") == ""
    assert svc.store_diary_line("", "2026-08-12", "x") == ""
    svc.close()


def test_query_recent_memory_persona_scoped():
    tmp = tempfile.mkdtemp()
    svc, _cfg = _build_service(tmp)
    svc.store_diary_line("shelly", "2026-08-10", "较早的日记。", source="your_own_life")
    svc.store_diary_line("shelly", "2026-08-12", "较新的日记。", source="your_own_life")
    svc.store_diary_line("other", "2026-08-12", "别人的日记。", source="your_own_life")
    rows = svc.query_recent_memory("shelly", k=10)
    assert len(rows) == 2
    assert all(r["persona_id"] == "shelly" for r in rows)
    assert rows[0]["summary"] == "较新的日记。"
    assert rows[1]["summary"] == "较早的日记。"
    assert {"id", "memory_type", "content", "tags", "created_at",
            "importance", "confidence"} <= set(rows[0].keys())
    # A since floor far in the future excludes everything; 0 keeps all.
    future = time.time() + 3600.0
    assert svc.query_recent_memory("shelly", k=10, since=future) == []
    assert len(svc.query_recent_memory("shelly", k=10, since=0.0)) == 2
    assert isinstance(svc.query_recent_memory("shelly", query="RAG", k=3), list)
    # Empty-persona rows must not leak into a named persona partition.
    svc.observe(session_id="leak", actor_id="shelly", platform="test",
                channel_id="c", content="no persona")
    assert all((r["persona_id"] or "") == "shelly"
               for r in svc.query_recent_memory("shelly", k=10))
    # since applies on the query path too.
    assert svc.query_recent_memory("shelly", query="RAG", k=3, since=future) == []
    svc.close()


def test_task_lease_exclusivity_and_ttl():
    tmp = tempfile.mkdtemp()
    svc, _cfg = _build_service(tmp)
    assert svc.claim_task("shelly", "diary", holder="instance-a", ttl_seconds=60)
    assert not svc.claim_task("shelly", "diary", holder="instance-b", ttl_seconds=60)
    assert svc.task_lease_owner("shelly", "diary") == "instance-a"
    assert svc.renew_task("shelly", "diary", holder="instance-a", ttl_seconds=120)
    assert not svc.renew_task("shelly", "diary", holder="instance-b", ttl_seconds=60)
    assert not svc.release_task("shelly", "diary", holder="instance-b")
    assert svc.release_task("shelly", "diary", holder="instance-a")
    assert svc.task_lease_owner("shelly", "diary") == ""
    assert svc.claim_task("shelly", "diary", holder="instance-b", ttl_seconds=60)
    # expire the lease directly, then a new holder can reclaim
    svc.lease_store._conn.execute(
        "UPDATE task_leases SET expires_at = ? "
        "WHERE persona_id = ? AND task_kind = ?",
        (time.time() - 10.0, "shelly", "diary"),
    )
    svc.lease_store._conn.commit()
    assert svc.task_lease_owner("shelly", "diary") == ""
    assert not svc.renew_task("shelly", "diary", holder="instance-b", ttl_seconds=60)
    assert svc.claim_task("shelly", "diary", holder="instance-c", ttl_seconds=60)
    assert svc.task_lease_owner("shelly", "diary") == "instance-c"
    assert svc.lease_store.cleanup_expired() == 0
    svc.lease_store._conn.execute(
        "UPDATE task_leases SET expires_at = ? "
        "WHERE persona_id = ? AND task_kind = ?",
        (time.time() - 10.0, "shelly", "diary"),
    )
    svc.lease_store._conn.commit()
    assert svc.lease_store.cleanup_expired() == 1
    assert svc.task_lease_owner("shelly", "diary") == ""
    svc.close()


def main():
    test_store_diary_line_roundtrip()
    test_query_recent_memory_persona_scoped()
    test_task_lease_exclusivity_and_ttl()
    print("\nv1.75 smoke: ALL PASS")


if __name__ == "__main__":
    main()
