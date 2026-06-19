"""Smoke v1.3 Agent tools: recall_long_term_memory / memorize_long_term_memory.

Tests the new Agent-callable tools that the LLM can invoke:
- schema validity (name, description, parameters conform to JSON Schema)
- handler is callable and returns valid JSON
- handler operates on a real MemoryService
- HippocampusStar._tools is populated after __init__
- Tool roundtrip: observe 3 engrams via memorize, then recall -> finds them
"""
import os, tempfile, sys, types, json


def _install_stub():
    a = types.ModuleType("astrbot"); ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star"); em = types.ModuleType("astrbot.api.event")
    class Star: ...
    def register(*a, **k):
        def deco(cls): return cls
        return deco
    class Context:
        def get_config(self, *a, **k): return {}
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
import main
from hippocampus import MemoryService, MemoryConfig
from hippocampus.tools import (
    MemoryTool, build_recall_tool, build_memorize_tool, all_tools,
)


def banner(t): print("\n=== " + t + " ===")


def test_schema_recall():
    banner("schema: recall_long_term_memory has OpenAI-compatible JSON schema")
    t = build_recall_tool()
    assert t.name == "recall_long_term_memory"
    assert t.description and "recall" in t.description.lower()
    spec = t.to_dict()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "recall_long_term_memory"
    params = spec["function"]["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert "query" in params["required"]
    assert params["properties"]["k"]["default"] == 5
    print("  recall schema: OK")


def test_schema_memorize():
    banner("schema: memorize_long_term_memory has OpenAI-compatible JSON schema")
    t = build_memorize_tool()
    assert t.name == "memorize_long_term_memory"
    spec = t.to_dict()
    params = spec["function"]["parameters"]
    assert "content" in params["properties"]
    assert "content" in params["required"]
    assert "importance" in params["properties"]
    print("  memorize schema: OK")


def test_all_tools_returns_five():
    # v1.4.x B5: 3 new tools added (forget_long_term_memory,
    # list_recent_memories, search_by_entity_memory). Schema: stable names
    # in the order [recall, memorize, forget, list_recent, search_by_entity].
    banner("all_tools() returns exactly 5 tools (v1.4.x B5)")
    ts = all_tools()
    names = [t.name for t in ts]
    assert names == [
        "recall_long_term_memory",
        "memorize_long_term_memory",
        "forget_long_term_memory",
        "list_recent_memories",
        "search_by_entity_memory",
    ], names
    print("  all_tools: OK ->", names)


def _new_service():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                       enable_semantic=False, enable_prospective=False)
    return MemoryService(cfg), tmp.name


def test_recall_handler_real_service():
    banner("recall_long_term_memory handler on real service")
    svc, db = _new_service()
    try:
        for txt in ["I love Americano coffee", "Alice lives in Shanghai", "tomorrow meeting 3pm"]:
            svc.observe(session_id="s1", actor_id="alice", platform="mock",
                        channel_id="c1", content=txt)
        tool = build_recall_tool()
        out = tool.handler(svc, query="Americano", k=3)
        payload = json.loads(out)
        assert payload["query"] == "Americano"
        assert payload["k"] == 3
        assert payload["count"] >= 1
        # First hit should be Americano-related
        top = payload["hits"][0]
        assert "Americano" in top["summary"] or "Americano" in top.get("summary", "")
        assert "id" in top and "score" in top
        print("  recall handler: OK ->", top["summary"][:40])
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_memorize_handler_real_service():
    banner("memorize_long_term_memory handler writes a new engram")
    svc, db = _new_service()
    try:
        tool = build_memorize_tool()
        out = tool.handler(svc, content="user prefers dark mode",
                           actor_id="alice", importance=0.8)
        payload = json.loads(out)
        assert payload["ok"] is True
        assert "engram_id" in payload
        assert payload["importance"] == 0.8
        # Now recall - the new engram should be findable
        rtool = build_recall_tool()
        out2 = rtool.handler(svc, query="dark mode", k=3)
        p2 = json.loads(out2)
        ids = [h["id"] for h in p2["hits"]]
        assert payload["engram_id"] in ids
        print("  memorize + recall roundtrip: OK -> wrote", payload["engram_id"][:8])
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_memorize_handler_empty_content():
    banner("memorize handler: empty content -> graceful error JSON")
    svc, db = _new_service()
    try:
        tool = build_memorize_tool()
        out = tool.handler(svc, content="")
        payload = json.loads(out)
        assert payload["ok"] is False
        assert "error" in payload
        print("  empty content error: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_memorize_handler_importance_clamp():
    banner("memorize handler: importance clamped to [0, 1]")
    svc, db = _new_service()
    try:
        tool = build_memorize_tool()
        out = tool.handler(svc, content="x", importance=5.0)
        p = json.loads(out)
        assert 0.0 <= p["importance"] <= 1.0
        assert p["importance"] == 1.0  # clamped from 5.0
        print("  importance clamp: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_star_registers_tools():
    banner("HippocampusStar: _register_agent_tools populates self._tools")
    # Use main.HippocampusStar but mock Context to allow init
    import main
    # Build a star without invoking full init by calling __new__ then _register
    star = main.HippocampusStar.__new__(main.HippocampusStar)
    star.context = types.SimpleNamespace()
    svc, db = _new_service()
    try:
        star.service = svc
        star._register_agent_tools()
        assert hasattr(star, "_tools")
        assert len(star._tools) == 5
        assert [t.name for t in star._tools] == [
            "recall_long_term_memory",
            "memorize_long_term_memory",
            "forget_long_term_memory",
            "list_recent_memories",
            "search_by_entity_memory",
        ]
        # And the context.register_tool (if it existed) was callable-safe
        print("  star._tools populated: OK")
    finally:
        import gc; del star, svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_version_alignment_v12_smoke_still_holds():
    banner("version alignment: metadata.yaml == __version__ == _registered_version")
    from hippocampus import __version__
    import re
    meta = open(os.path.dirname(os.path.abspath(__file__)) + "/metadata.yaml",
                encoding="utf8").read()
    m = re.search(r"^version:\s*(\S+)", meta, re.M)
    assert m, "version not in metadata"
    assert m.group(1) == __version__, (m.group(1), __version__)
    assert __version__ == m.group(1), (__version__, m.group(1))
    assert main.HippocampusStar._registered_version == __version__, (main.HippocampusStar._registered_version, __version__)
    print(f"  all 3 versions align at {__version__}: OK")


if __name__ == "__main__":
    test_schema_recall()
    test_schema_memorize()
    test_all_tools_returns_five()
    test_recall_handler_real_service()
    test_memorize_handler_real_service()
    test_memorize_handler_empty_content()
    test_memorize_handler_importance_clamp()
    test_star_registers_tools()
    test_version_alignment_v12_smoke_still_holds()
    print("\nALL OK")
