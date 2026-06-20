"""Smoke v1.21 (B-4 WebUI edit): MemoryHandler.update_memory edits fields
and re-embeds only when content (text) changes. No astrbot host needed -
the page handler is pure-python over MemoryService.
"""
import sys, os, tempfile, types


def _install_stub():
    a = types.ModuleType("astrbot"); ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star"); em = types.ModuleType("astrbot.api.event")
    class Star: ...
    def register(*a, **k):
        def deco(cls): return cls
        return deco
    class Context: ...
    class AstrMessageEvent: ...
    class _MT: ALL = "all"
    class _F:
        EventMessageType = _MT
        def event_message_type(self, *a, **k):
            def deco(fn): return fn
            return deco
        def command(self, *a, **k):
            def deco(fn): return fn
            return deco
        @staticmethod
        def on_llm_request(*a, **k):
            def deco(fn): return fn
            return deco
        @staticmethod
        def on_llm_response(*a, **k):
            def deco(fn): return fn
            return deco
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig
from page_api_modules import PageApiUtils, MemoryHandler


def banner(m):
    print(chr(10) + "=== " + m + " ===")


def _svc(tmp):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.tiering_enabled = False
    return MemoryService(cfg=cfg)


def _store_one(svc, content="hello world", summary="s"):
    from hippocampus.types import Engram
    e = Engram(actor_id="A", content=content, summary=summary,
               memory_type="episodic", importance=0.5, strength=1.0)
    e.embedding = svc.embedder.embed(content)
    svc.store.upsert(e)
    return e


def test_update_text_reembeds():
    banner("update content -> re-embed; metadata-only -> no re-embed")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    h = MemoryHandler(PageApiUtils())
    e = _store_one(svc, content="hello world")
    old_emb = list(e.embedding)

    # 1) change content -> reembedded True
    r = h.update_memory(svc, e.id, {"content": "a totally different sentence"})
    assert r["status"] == "ok", r
    assert r["data"]["reembedded"] is True, r
    assert "content" in r["data"]["changed"], r
    got = svc.store.get(e.id)
    assert got.content == "a totally different sentence"
    assert got.embedding != old_emb, "embedding should have changed"

    # 2) change only importance/strength/memory_type -> no reembed
    r2 = h.update_memory(svc, e.id, {"importance": 0.9, "strength": 0.3,
                                     "memory_type": "semantic"})
    assert r2["data"]["reembedded"] is False, r2
    g2 = svc.store.get(e.id)
    assert abs(g2.importance - 0.9) < 1e-9 and abs(g2.strength - 0.3) < 1e-9
    assert g2.memory_type == "semantic"

    # 3) topics/tags coercion from comma string
    r3 = h.update_memory(svc, e.id, {"topics": "a, b, c", "tags": "x\u3001y"})
    g3 = svc.store.get(e.id)
    assert g3.topics == ["a", "b", "c"], g3.topics
    assert g3.tags == ["x", "y"], g3.tags

    # 4) clamping
    r4 = h.update_memory(svc, e.id, {"importance": 5.0})
    g4 = svc.store.get(e.id)
    assert abs(g4.importance - 1.0) < 1e-9, g4.importance

    # 5) no-op when nothing changes
    r5 = h.update_memory(svc, e.id, {"memory_type": "semantic"})
    assert r5["data"]["changed"] == [], r5

    # 6) bad id
    r6 = h.update_memory(svc, "nope", {"summary": "z"})
    assert r6["status"] == "error", r6

    svc.close()
    print("PASS update_memory")


if __name__ == "__main__":
    test_update_text_reembeds()
    print(chr(10) + "ALL v44 PASS")
