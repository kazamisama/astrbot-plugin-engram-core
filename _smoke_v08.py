"""Smoke v0.8: render_stats / find_and_forget / emb_bridge / search / export / import / graph / banner."""
import os, tempfile, sys, types, asyncio, json

def _install_astrbot_stub():
    astrbot = types.ModuleType("astrbot"); astrbot_api = types.ModuleType("astrbot.api")
    star_mod = types.ModuleType("astrbot.api.star")
    event_mod = types.ModuleType("astrbot.api.event")
    class Star: ...
    def register(*a, **k):
        def deco(cls): return cls
        return deco
    class Context: ...
    class AstrMessageEvent: ...
    class _EventMessageType: ALL = "all"
    class _Filter:
        EventMessageType = _EventMessageType
        def event_message_type(self, *a, **k):
            def deco(fn): return fn
            return deco
        def command(self, *a, **k):
            def deco(fn): return fn
            return deco
    star_mod.Star = Star; star_mod.register = register; star_mod.Context = Context
    event_mod.filter = _Filter
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.EventMessageType = _EventMessageType
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.event"] = event_mod

_install_astrbot_stub()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main
from hippocampus import MemoryService, MemoryConfig, Cue
from main import (HippocampusStar, render_stats, find_and_forget,
                  emb_bridge_for_context, parse_search_args, format_graph,
                  export_engrams, import_engrams, banner_text)


def banner(t): print(chr(10) + "=== " + t + " ===")


def main_test():
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "chat-1"

        banner("parse_search_args")
        q, m = parse_search_args("Americano coffee --mode=fts")
        print("  query=" + repr(q) + " mode=" + m)
        assert m == "fts" and q == "Americano coffee"
        q, m = parse_search_args("plain text")
        assert m == "hybrid" and q == "plain text"
        q, m = parse_search_args("foo --mode=garbage")
        assert m == "hybrid"

        banner("ingest")
        for m in ["I am Alice, I love Americano coffee",
                  "I am Bob, I dislike cilantro",
                  "Tomorrow I plan to fly to Tokyo"]:
            svc.observe(session_id=sid, actor_id="u1", platform="qq",
                        channel_id="g1", content=m)
        print("  ingested 3 messages, stats:")
        print(render_stats(svc))

        banner("export + import roundtrip")
        out = os.path.join(td, "out.json")
        msg = export_engrams(svc, out)
        print("  " + msg)
        assert "exported" in msg
        assert os.path.exists(out)
        with open(out, "r", encoding="utf-8") as f:
            payload = json.load(f)
        assert "engrams" in payload and len(payload["engrams"]) == 3
        assert "entities" in payload and len(payload["entities"]) >= 3
        # Import into a fresh service so the round-trip is observable
        svc2 = MemoryService(MemoryConfig(sqlite_path=os.path.join(td, "h2.db"),
                                          embedding_dim=64))
        msg = import_engrams(svc2, out)
        print("  " + msg)
        assert "imported: engrams=3" in msg
        print("  fresh service after import, stats:")
        print(render_stats(svc2))
        try: asyncio.run(svc2.stop())
        except Exception: pass
        try: svc2.close()
        except Exception: pass

        banner("graph")
        g = format_graph(svc, "Alice")
        print(g)
        assert "graph for: Alice" in g
        assert "entities" in g
        assert "likes" in g or "resides_in" in g  # we ingested identity but not in this round; likes maybe missing
        g2 = format_graph(svc, "")
        print("  empty query:", g2)
        assert "usage" in g2
        g3 = format_graph(svc, "zzz_nothing_matches_zzz")
        print("  no hit:", g3)
        assert "no entities" in g3

        banner("banner_text")
        print(banner_text(svc))
        assert "[hippocampus] loaded" in banner_text(svc)

        banner("forget: exact + prefix + not found + empty")
        all_e = svc.store.all(limit=10)
        assert all_e
        m = find_and_forget(svc, all_e[0].id)
        assert "forgot" in m
        all_e = svc.store.all(limit=10)
        prefix = all_e[0].id[:4]
        m = find_and_forget(svc, prefix)
        assert "forgot" in m
        m = find_and_forget(svc, "deadbeef")
        assert "not found" in m
        m = find_and_forget(svc, "")
        assert "usage" in m

        banner("emb_bridge: emb provider sync")
        class MockEmbProv:
            def get_embedding(self, text): return [0.1 * len(text), 0.2, 0.3]
        class MockCtx1:
            async def get_using_embedding_provider(self): return MockEmbProv()
        r = asyncio.run(emb_bridge_for_context(MockCtx1(), "hi"))
        assert r == [0.2, 0.2, 0.3]

        banner("emb_bridge: fallback LLM provider")
        class L:
            def embed(self, text): return [1.0, 2.0]
        class C2:
            async def get_using_provider(self): return L()
        r = asyncio.run(emb_bridge_for_context(C2(), "x"))
        assert r == [1.0, 2.0]

        banner("emb_bridge: nothing -> []")
        class E: pass
        r = asyncio.run(emb_bridge_for_context(E(), "x"))
        assert r == []

        banner("emb_bridge: async method")
        class A:
            async def get_embedding(self, text): return [9.0]
        class C3:
            async def get_using_embedding_provider(self): return A()
        r = asyncio.run(emb_bridge_for_context(C3(), "x"))
        assert r == [9.0]

        try: svc.close()

        except Exception: pass

        del svc

        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main_test()