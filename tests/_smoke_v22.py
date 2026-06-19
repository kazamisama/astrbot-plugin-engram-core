"""Smoke v1.4.x: B5 - 3 new agent tools (forget / list_recent / search_by_entity).

Scope:
- all_tools() now returns 5 MemoryTool objects (recall, memorize, forget,
  list_recent, search_by_entity).
- forget_long_term_memory: soft_forget by default; hard=True deletes row.
- list_recent_memories: list_active + actor_id filter, newest first.
- search_by_entity_memory: resolve entity by name (case-insensitive) and
  return engrams whose entity_refs includes the resolved entity id.

Tests:
- All 5 tools are registered with stable names and required-params.
- Handler JSON shape matches documented contract (ok/engram_id/mode).
- forget: missing id -> ok=False; unknown id -> ok=False; soft round-trip.
- forget: hard=True actually removes the row.
- forget: re-forgetting an already-forgotten row is a noop (ok=True, mode=noop).
- list_recent: filters by actor_id; respects k; newest first.
- list_recent: missing actor_id -> ok=False.
- search_by_entity: case-insensitive resolve; returns engrams that reference
  the entity; unknown entity -> ok=False."""
import os, sys, types, tempfile, json


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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from hippocampus import MemoryService, MemoryConfig
from hippocampus import tools as ht


def banner(t): print("\n=== " + t + " ===")


def _new_svc(**over):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32, **over)
    return MemoryService(cfg), tmp.name


def test_tools_registered_with_stable_names():
    banner("all_tools: 5 tools registered with required params")
    names = [t.name for t in ht.all_tools()]
    assert names == [
        "recall_long_term_memory",
        "memorize_long_term_memory",
        "forget_long_term_memory",
        "list_recent_memories",
        "search_by_entity_memory",
    ], names
    for t in ht.all_tools():
        assert t.description, t.name
        assert t.parameters["type"] == "object"
        assert "required" in t.parameters
    forget = ht.build_forget_tool()
    assert forget.parameters["required"] == ["engram_id"]
    list_recent = ht.build_list_recent_tool()
    assert list_recent.parameters["required"] == ["actor_id"]
    sbe = ht.build_search_by_entity_tool()
    assert sbe.parameters["required"] == ["entity_name"]
    print("  registered: OK (" + str(len(names)) + " tools)")


def test_forget_soft_round_trip():
    banner("forget: soft round-trip")
    svc, db = _new_svc()
    try:
        e = svc.observe(session_id="s1", actor_id="u1", platform="qq",
                        channel_id="g1", content="I am Alice, I love Americano")
        eid = e.id
        result = json.loads(ht.build_forget_tool().handler(svc, engram_id=eid))
        assert result["ok"] is True
        assert result["mode"] == "soft"
        assert result["engram_id"] == eid
        again = json.loads(ht.build_forget_tool().handler(svc, engram_id=eid))
        assert again["ok"] is True
        assert again["mode"] == "noop"
        row = svc.store.get(eid)
        assert row is not None
        assert row.forgotten_at > 0
        print("  soft forget: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_forget_hard_actually_deletes():
    banner("forget: hard=True physically removes the row")
    svc, db = _new_svc()
    try:
        e = svc.observe(session_id="s1", actor_id="u1", platform="qq",
                        channel_id="g1", content="I am Bob, I dislike cilantro")
        eid = e.id
        result = json.loads(ht.build_forget_tool().handler(svc, engram_id=eid, hard=True))
        assert result["ok"] is True
        assert result["mode"] == "hard"
        assert svc.store.get(eid) is None
        print("  hard forget: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_forget_errors():
    banner("forget: missing id / unknown id return ok=False")
    svc, db = _new_svc()
    try:
        r1 = json.loads(ht.build_forget_tool().handler(svc, engram_id=""))
        assert r1["ok"] is False and "required" in r1["error"]
        r2 = json.loads(ht.build_forget_tool().handler(svc, engram_id="deadbeef" * 4))
        assert r2["ok"] is False and "not found" in r2["error"]
        print("  forget errors: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_list_recent_filters_by_actor():
    banner("list_recent: filters by actor_id; respects k; newest first")
    svc, db = _new_svc()
    try:
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I live in Shanghai",
            "I am Alice, I dislike cilantro",
        ]:
            svc.observe(session_id="s1", actor_id="u1", platform="qq",
                        channel_id="g1", content=m)
        svc.observe(session_id="s2", actor_id="u2", platform="qq",
                    channel_id="g1", content="Bob unrelated message")
        result = json.loads(ht.build_list_recent_tool().handler(svc, actor_id="u1", k=2))
        assert result["actor_id"] == "u1"
        assert result["k"] == 2
        assert result["count"] == 2, result
        first = result["items"][0]
        sm = (first.get("summary") or "").lower()
        assert "dislike" in sm or "cilantro" in sm, sm
        for it in result["items"]:
            assert "Bob" not in (it.get("summary") or ""), it
        print("  list_recent: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_list_recent_missing_actor():
    banner("list_recent: missing actor_id returns ok=False")
    svc, db = _new_svc()
    try:
        r = json.loads(ht.build_list_recent_tool().handler(svc, actor_id="", k=5))
        assert r["ok"] is False and "required" in r["error"]
        print("  list_recent missing actor: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_search_by_entity_resolves_case_insensitive():
    banner("search_by_entity: case-insensitive resolve + entity_refs join")
    svc, db = _new_svc()
    try:
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee in the morning",
            "I am Alice, I live in Shanghai",
        ]:
            svc.observe(session_id="s1", actor_id="u1", platform="qq",
                        channel_id="g1", content=m)
        r1 = json.loads(ht.build_search_by_entity_tool().handler(svc, entity_name="americano", k=5))
        assert r1["entity_name"] == "americano"
        assert r1["resolved_entity"]["name"].lower() == "americano"
        assert r1["count"] >= 2, r1
        for it in r1["items"]:
            assert "Americano" in (it.get("summary") or ""), it
        r2 = json.loads(ht.build_search_by_entity_tool().handler(svc, entity_name="zzz_no_such", k=5))
        assert r2["ok"] is False and "not found" in r2["error"]
        r3 = json.loads(ht.build_search_by_entity_tool().handler(svc, entity_name="", k=5))
        assert r3["ok"] is False and "required" in r3["error"]
        print("  search_by_entity: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def main():
    test_tools_registered_with_stable_names()
    test_forget_soft_round_trip()
    test_forget_hard_actually_deletes()
    test_forget_errors()
    test_list_recent_filters_by_actor()
    test_list_recent_missing_actor()
    test_search_by_entity_resolves_case_insensitive()
    print("\nALL OK")


if __name__ == "__main__":
    main()
