"""Smoke v1.76: full L2-01 memory surface + entity graph primitives.

Covers:
  - store_event / add_note persist persona-scoped engrams with tags
  - query_memory / search return stable dict lists
  - upsert_entity / link_entities / list_entities / list_links roundtrip
    with dimension model and seen_count accumulation
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


def test_event_and_note_write():
    tmp = tempfile.mkdtemp()
    svc, _cfg = _build_service(tmp)
    eid = svc.store_event(
        "shelly", "internet-life", "s1", time.time(), "observe",
        {"entity": "browse", "session_id": 7},
        source="your_own_life",
    )
    assert eid
    e = svc.store.get(eid)
    assert e.persona_id == "shelly"
    assert e.memory_type == "event"
    assert "kind:observe" in set(e.tags)
    assert "source:your_own_life" in set(e.tags)
    nid = svc.add_note(
        "shelly",
        {"summary": "RAG 短记", "opinion": "值得再看", "url": "https://example.com/1",
         "url_hash": "h1", "category": "opinion", "tags": ["ai"],
         "entities": ["rag"]},
        source="your_own_life",
    )
    assert nid
    n = svc.store.get(nid)
    assert n.memory_type == "note"
    tags = set(n.tags)
    assert "url:https://example.com/1" in tags
    assert "category:opinion" in tags
    assert "hash:h1" in tags
    assert svc.store_event("", "x", "", 0.0, "observe") == ""
    assert svc.add_note("shelly", {"summary": "  "}) == ""
    svc.close()


def test_query_memory_and_search_are_lists():
    tmp = tempfile.mkdtemp()
    svc, _cfg = _build_service(tmp)
    svc.add_note("shelly", {"summary": "RAG 与向量检索", "tags": ["ai"]},
                 source="your_own_life")
    rows = svc.query_memory("shelly", "RAG", k=3)
    assert isinstance(rows, list)
    assert {"id", "persona_id", "memory_type", "content", "summary",
            "tags", "created_at", "importance", "confidence"} <= set(rows[0].keys())
    assert isinstance(svc.search("shelly", "RAG", k=3), list)
    filtered = svc.query_memory("shelly", "RAG", k=3, memory_types=["note"])
    assert all(r["memory_type"] == "note" for r in filtered)
    svc.close()


def test_entity_graph_roundtrip():
    tmp = tempfile.mkdtemp()
    svc, _cfg = _build_service(tmp)
    hn = svc.upsert_entity(
        "shelly", {"dimension": "platform", "entity_id": "hacker-news",
                   "name": "Hacker News", "canonical_url": "https://news.ycombinator.com"})
    tokio = svc.upsert_entity(
        "shelly", {"dimension": "project", "entity_id": "tokio-rs/tokio",
                   "name": "Tokio", "canonical_url": "https://github.com/tokio-rs/tokio"})
    assert hn and tokio and hn != tokio
    assert svc.link_entities("shelly", tokio, "appears_on", hn, weight=1.0) is True
    assert svc.link_entities("shelly", tokio, "appears_on", hn, weight=0.9) is False
    entities = {e["entity_id"]: e for e in svc.list_entities("shelly")}
    assert set(entities) == {"hacker-news", "tokio-rs/tokio"}
    assert entities["tokio-rs/tokio"]["seen_count"] == 1
    links = svc.list_links("shelly")
    assert len(links) == 1
    assert links[0]["relation"] == "appears_on"
    assert links[0]["seen_count"] == 2
    other = svc.list_entities("nobody")
    assert other == []
    assert svc.upsert_entity("shelly", {}) == ""
    assert svc.link_entities("shelly", "", "appears_on", hn) is False
    svc.close()


def main():
    test_event_and_note_write()
    test_query_memory_and_search_are_lists()
    test_entity_graph_roundtrip()
    print("\nv1.76 smoke: ALL PASS")


if __name__ == "__main__":
    main()
