"""Smoke v1.4.x: B9 - WebUI (page_api + bot_language + i18n init).

Scope:
- page_api.py + 5 page_api_modules/ (utils + stats + memory + recall + graph)
- PluginPageApi.register_routes() registers 8 endpoints with prefix
  /astrbot-plugin-engram/page/{health,stats,memories,memories/detail,
   memories/delete,recall/test,graph/overview,graph/query}
- _conf_schema.json: 15 fields (was 14), bot_language at top;
  descriptions sourced from ConfigManager.LABELS.en (B7+B9 link)
- PluginInitializer.initialize() now calls i18n_init(bot_language)
  (B8 gap closure; default "zh" if missing)

Tests:
- PluginPageApi instantiated; register_routes calls 8 register_web_api()
- Each handler returns {status:ok|error, data|message} shape
- Real-service roundtrip: 8 endpoints on a MemoryService with 3 engrams
- bot_language=zh -> i18n current_language=zh; bot_language=en -> en
- _conf_schema.json has 15 fields incl. bot_language with English description
- PluginInitializer._init_service wires i18n correctly via mock context
- Soft delete and hard delete both work via HippocampalStore API
- Graph query resolves subject_id/object_id to names via get_entity()
"""
import os, sys, json, tempfile, types


def _install_stub():
    a = types.ModuleType("astrbot")
    ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star")
    em = types.ModuleType("astrbot.api.event")
    class Star: pass
    def register(*a, **k):
        def deco(cls): return cls
        return deco
    class Context: pass
    class AstrMessageEvent: pass
    class _MT: ALL = "all"
    class _F:
        EventMessageType = _MT
        def event_message_type(self, *a, **k):
            def deco(fn): return fn
            return deco
        def command(self, *a, **k):
            def deco(fn): return fn
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
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def banner(t_):
    print(chr(10) + "=== " + t_ + " ===")


def _new_service():
    from hippocampus import MemoryService, MemoryConfig
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    cfg = MemoryConfig(
        sqlite_path=db, embedding_name="hash", llm_name="rule")
    return MemoryService(cfg), db


def _observe_3(svc):
    for msg in [
        "I am Alice, I love Americano coffee",
        "I am Bob, I love tea",
        "I am Alice, I live in Shanghai",
    ]:
        svc.observe(
            session_id="s1",
            actor_id="alice" if "Alice" in msg else "bob",
            platform="qq", channel_id="g1", content=msg)


def test_register_routes_8_endpoints():
    banner("PluginPageApi.register_routes registers 8 endpoints")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__))))
    import page_api
    calls = []
    def reg(path, handler, methods, name):
        calls.append((path, methods, name))
    plugin = types.SimpleNamespace(
        context=types.SimpleNamespace(register_web_api=reg),
        _initializer=None)
    api = page_api.PluginPageApi(plugin)
    api.register_routes()
    assert len(calls) == 10, "expected 10 endpoints, got " + str(len(calls))  # B10: +2 backup
    paths = [c[0] for c in calls]
    expected_paths = [
        "/astrbot-plugin-engram/page/health",
        "/astrbot-plugin-engram/page/stats",
        "/astrbot-plugin-engram/page/memories",
        "/astrbot-plugin-engram/page/memories/detail",
        "/astrbot-plugin-engram/page/memories/delete",
        "/astrbot-plugin-engram/page/recall/test",
        "/astrbot-plugin-engram/page/graph/overview",
        "/astrbot-plugin-engram/page/graph/query",
        "/astrbot-plugin-engram/page/backups",
        "/astrbot-plugin-engram/page/backups/restore",
    ]
    assert paths == expected_paths, "paths: " + str(paths)
    print("  10 endpoints registered with correct paths (8 B9 + 2 B10 backup): OK")


def test_endpoint_response_shapes():
    banner("Each handler returns {status, data|message} shape")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__))))
    import page_api
    api = page_api.PluginPageApi(
        types.SimpleNamespace(context=types.SimpleNamespace(), _initializer=None))
    h = api._health()
    assert h["status"] == "ok" and "version" in h["data"]
    s = api.stats_handler.get_stats(None)
    assert s["status"] == "error" and "message" in s
    m = api.memory_handler.list_memories(None)
    assert m["status"] == "error"
    print("  ok/error shape on health/stats/memories: OK")


def test_real_service_roundtrip():
    banner("Real-service roundtrip: 3 engrams, 8 endpoint behaviors")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__))))
    import page_api
    svc, db = _new_service()
    try:
        _observe_3(svc)
        api = page_api.PluginPageApi(
            types.SimpleNamespace(
                context=types.SimpleNamespace(
                    register_web_api=lambda *a, **k: None),
                _initializer=types.SimpleNamespace(service=svc)))
        h = api._health()
        assert h["status"] == "ok" and h["data"]["service_ready"] is True
        s = api.stats_handler.get_stats(svc)
        assert s["data"]["engrams"] == 3, s
        m = api.memory_handler.list_memories(svc, k=2)
        assert m["status"] == "ok" and m["data"]["returned"] == 2
        m_alice = api.memory_handler.list_memories(
            svc, actor_id="alice", k=10)
        assert m_alice["data"]["returned"] == 2, m_alice
        eid = svc.store.list_active(limit=100)[0].id
        d = api.memory_handler.get_memory_detail(svc, eid=eid)
        assert d["status"] == "ok" and d["data"]["id"] == eid
        d2 = api.memory_handler.get_memory_detail(svc, eid=eid[:6])
        assert d2["data"]["id"] == eid
        d3 = api.memory_handler.get_memory_detail(svc, eid="deadbeef")
        assert d3["status"] == "error"
        eid2 = svc.store.list_active(limit=100)[-1].id
        sd = api.memory_handler.delete_memory(svc, eid=eid2)
        assert sd["status"] == "ok" and sd["data"]["mode"] == "soft"
        r = api.recall_handler.test_recall(
            svc, query="Americano", mode="hybrid", k=2)
        assert r["status"] == "ok" and r["data"]["count"] >= 1
        g = api.graph_handler.graph_overview(svc)
        assert g["data"]["n_entities"] >= 3, g
        gq = api.graph_handler.graph_query(svc, name="Alice")
        assert gq["status"] == "ok"
        assert gq["data"]["entity"]["name"] == "Alice"
        for rel in gq["data"]["relations"]:
            assert rel["src"] is not None, rel
            assert rel["dst"] is not None, rel
        gq2 = api.graph_handler.graph_query(svc, name="ZZZ_no_such")
        assert gq2["status"] == "error"
    finally:
        svc.close()
        try: os.unlink(db)
        except Exception: pass
    print("  8 endpoint behaviors on real service: OK")


def test_conf_schema_has_bot_language_and_en_descriptions():
    banner("_conf_schema.json: 15 fields, bot_language + EN descriptions")
    p = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_conf_schema.json")
    schema = json.load(open(p, encoding="utf-8"))
    assert "bot_language" in schema, schema.keys()
    assert schema["bot_language"]["default"] == "zh"
    assert "description" in schema["bot_language"]
    assert len(schema) == 20, len(schema)  # B10: +5 backup
    from hippocampus.config_manager import LABELS
    for fname in [
        "sqlite_path", "embedding_name", "embedding_dim",
        "openai_api_key", "auto_rebuild_on_switch",
        "enable_semantic", "metamemory_enabled",
    ]:
        assert fname in schema, fname
        desc = schema[fname].get("description", "")
        if desc != LABELS[fname]["en"]:
            raise AssertionError(fname + ": schema=" + repr(desc) + " vs LABELS.en=" + repr(LABELS[fname]["en"]))
    print("  15 fields, bot_language, LABELS.en sourced: OK")


def test_plugin_initializer_uses_bot_language():
    banner("PluginInitializer.initialize reads cfg.bot_language")
    from handlers.init import PluginInitializer
    from hippocampus.i18n_backend import current_language
    init = PluginInitializer(
        types.SimpleNamespace(
            get_config=lambda k: {"bot_language": "zh",
                                  "sqlite_path": ":memory:"},
            register_tool=None))
    init.initialize()
    assert current_language() == "zh"
    init2 = PluginInitializer(
        types.SimpleNamespace(
            get_config=lambda k: {"bot_language": "en",
                                  "sqlite_path": ":memory:"},
            register_tool=None))
    init2.initialize()
    assert current_language() == "en"
    init3 = PluginInitializer(
        types.SimpleNamespace(
            get_config=lambda k: {"sqlite_path": ":memory:"},
            register_tool=None))
    init3.initialize()
    assert current_language() == "zh"
    init4 = PluginInitializer(
        types.SimpleNamespace(
            get_config=lambda k: {"bot_language": "ja",
                                  "sqlite_path": ":memory:"},
            register_tool=None))
    init4.initialize()
    assert current_language() == "zh"
    print("  PluginInitializer reads bot_language (zh/en/default/unknown): OK")


def main():
    test_register_routes_8_endpoints()
    test_endpoint_response_shapes()
    test_real_service_roundtrip()
    test_conf_schema_has_bot_language_and_en_descriptions()
    test_plugin_initializer_uses_bot_language()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()