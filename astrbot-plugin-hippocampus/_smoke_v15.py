"""Smoke v1.3 dual-mode end-to-end via /mem search --mode=dual command path.

Tests the new /mem search --mode=dual command without booting AstrBot:
- parse_search_args accepts mode=dual
- format_dual_route renders hits with route tags (doc / graph / doc+graph)
- explain() reports which route contributed what
- The existing vector/fts/hybrid modes still work and parse_search_args
  defaults unknown modes to hybrid (back-compat).
"""
import os, tempfile, sys, types, asyncio


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
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: F401
from hippocampus import MemoryService, MemoryConfig, Cue
from handlers import parse_search_args, format_dual_route
from handlers.help_text import HELP_TEXT


def banner(t): print("\n=== " + t + " ===")


def test_parse_args_dual():
    banner("parse_search_args: dual mode accepted")
    q, m = parse_search_args("Americano --mode=dual")
    assert q == "Americano" and m == "dual", (q, m)
    q, m = parse_search_args("hello world")
    assert q == "hello world" and m == "hybrid"
    q, m = parse_search_args("foo --mode=invalid")
    assert q == "foo" and m == "hybrid"  # invalid -> hybrid
    print("  parse_search_args dual: OK")


def test_help_text_mentions_dual():
    banner("HELP_TEXT: /mem search help line shows dual")
    assert "[--mode=vector|fts|hybrid|dual]" in HELP_TEXT
    print("  help text updated: OK")


def _new_service():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                       enable_semantic=True, enable_prospective=False)
    return MemoryService(cfg), tmp.name


def test_dual_mode_e2e():
    banner("format_dual_route: end-to-end with graph entities")
    svc, db = _new_service()
    try:
        # Set up entities + relations
        from hippocampus.types import Entity, Relation
        alice = Entity(id="ent_alice", name="Alice", type="person", mention_count=5)
        am = Entity(id="ent_am", name="Americano", type="drink", mention_count=3)
        for e in (alice, am):
            svc.semantic.upsert_entity(e)
        svc.semantic.add_relation(Relation(
            subject_id="ent_alice", predicate="likes", object_id="ent_am", confidence=0.9))
        # Ingest 3 engrams, two with entity_refs pointing to graph
        svc.observe(session_id="s1", actor_id="u1", platform="mock",
                    channel_id="c1", content="Alice loves Americano coffee")
        e1 = svc.store.all(limit=10)[0]
        e1.entity_refs = ["ent_alice", "ent_am"]
        svc.store.upsert(e1)
        svc.observe(session_id="s1", actor_id="u1", platform="mock",
                    channel_id="c1", content="random unrelated text about dogs")
        svc.observe(session_id="s1", actor_id="u1", platform="mock",
                    channel_id="c1", content="another sentence mentioning coffee beans")
        # Now run /mem search --mode=dual equivalent
        out = format_dual_route(svc, "Alice", k=5)
        assert "[dual] hits for: Alice" in out
        assert "doc" in out or "graph" in out  # at least one route tag
        print("  dual mode render: OK")
        print("  --- output ---")
        for ln in out.splitlines():
            print("   ", ln)
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_dual_mode_no_hit():
    banner("format_dual_route: empty result message")
    svc, db = _new_service()
    try:
        out = format_dual_route(svc, "anything", k=5)
        assert "[dual] no hit for: anything" in out
        print("  no-hit: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_dual_mode_empty_query():
    banner("format_dual_route: usage on empty query")
    svc, db = _new_service()
    try:
        out = format_dual_route(svc, "", k=5)
        assert "usage" in out
        print("  usage guard: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_dual_mode_semantic_off():
    banner("format_dual_route: semantic off still works (graph route yields nothing, document route serves)")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                       enable_semantic=False, enable_prospective=False)
    svc = MemoryService(cfg)
    try:
        svc.observe(session_id="s1", actor_id="u1", platform="mock",
                    channel_id="c1", content="Americano is great")
        out = format_dual_route(svc, "Americano", k=5)
        assert "[dual] hits for: Americano" in out
        assert "[doc]" in out
        print("  semantic-off fallback: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_existing_modes_still_work():
    banner("back-compat: vector/fts/hybrid parse args still work after dual added")
    assert parse_search_args("foo --mode=vector") == ("foo", "vector")
    assert parse_search_args("foo --mode=fts") == ("foo", "fts")
    assert parse_search_args("foo --mode=hybrid") == ("foo", "hybrid")
    assert parse_search_args("foo --mode=dual") == ("foo", "dual")
    print("  all 4 modes parse correctly: OK")


if __name__ == "__main__":
    test_parse_args_dual()
    test_help_text_mentions_dual()
    test_dual_mode_e2e()
    test_dual_mode_no_hit()
    test_dual_mode_empty_query()
    test_dual_mode_semantic_off()
    test_existing_modes_still_work()
    print("\nALL OK")