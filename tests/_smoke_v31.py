"""Smoke v1.8: PersonaStore + build_persona + persona injection.

Covers:
  - 3 persona config fields registered with defaults
  - PersonaStore CRUD (upsert/get/all/delete) on a file db
  - MemoryService.build_persona summarizes a speaker's engrams via a
    stub LLM, get_persona returns it; rule LLM ("") -> None
  - InjectHandler injects a persona background block when enabled
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
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS
from hippocampus.persona import PersonaStore, Persona


def banner(m):
    print("\n=== " + m + " ===")


def test_fields_defaults():
    banner("3 persona fields registered + defaults")
    for f in ("enable_persona", "persona_inject_enabled", "persona_max_source"):
        assert f in _FIELDS, f
        assert f in LABELS, f
    cfg = ConfigManager({}).memory_config
    assert cfg.enable_persona is False
    assert cfg.persona_inject_enabled is False
    assert cfg.persona_max_source == 20
    print("  defaults: disabled / inject off / max=20 OK")


def test_persona_store_crud():
    banner("PersonaStore CRUD")
    d = tempfile.mkdtemp()
    store = PersonaStore(os.path.join(d, "p.db"))
    assert store.get("u1") is None
    store.upsert(Persona(actor_id="u1", summary="喜欢侦探小说", platform="qq", source_count=3))
    got = store.get("u1")
    assert got is not None and got.summary == "喜欢侦探小说"
    assert got.source_count == 3
    # update path
    store.upsert(Persona(actor_id="u1", summary="改成喜欢推理", source_count=5))
    got2 = store.get("u1")
    assert got2.summary == "改成喜欢推理" and got2.source_count == 5
    store.upsert(Persona(actor_id="u2", summary="另一个人"))
    assert len(store.all()) == 2
    assert store.delete("u1") is True
    assert store.get("u1") is None
    store.close()
    print("  upsert/get/update/all/delete: OK")


class _StubLLM:
    def name(self):
        return "stub"
    def chat(self, system, user, **k):
        return "该用户偏好技术讨论，活跃于 AstrBot 生态。"


def _build_service(tmp, llm):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "hippo.db")
    cfg.enable_persona = True
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    svc = MemoryService(cfg=cfg)
    svc.llm = llm
    return svc


def test_build_persona_with_llm():
    banner("build_persona summarizes via stub LLM")
    tmp = tempfile.mkdtemp()
    svc = _build_service(tmp, _StubLLM())
    # Seed a few engrams for actor u1
    for i in range(3):
        svc.observe(session_id="s", actor_id="u1", platform="qq",
                    channel_id="g1", content="我在研究 Agent 框架 " + str(i))
    p = svc.build_persona("u1")
    assert p is not None, "expected a persona"
    assert "AstrBot" in p.summary or "技术" in p.summary
    assert svc.get_persona("u1").summary == p.summary
    print("  persona built + stored + retrievable: OK")
    try:
        svc.close()
    except Exception:
        pass


def test_build_persona_rule_llm_none():
    banner("rule LLM ('') -> build_persona returns None")
    tmp = tempfile.mkdtemp()
    class _RuleLLM:
        def name(self): return "rule"
        def chat(self, *a, **k): return ""
    svc = _build_service(tmp, _RuleLLM())
    svc.observe(session_id="s", actor_id="u1", platform="qq",
                channel_id="g1", content="一条消息内容")
    assert svc.build_persona("u1") is None
    print("  empty LLM output -> None: OK")
    try:
        svc.close()
    except Exception:
        pass


def test_inject_persona_block():
    banner("InjectHandler injects persona background when enabled")
    from handlers.event import InjectHandler

    class _Cue:
        pass

    class _Result:
        engrams = []

    class _Svc:
        def __init__(self, cfg):
            self.cfg = cfg
        def get_persona(self, actor_id):
            return Persona(actor_id=actor_id, summary="稳定背景：喜欢推理")
        def recall(self, cue):
            return _Result()

    class _Event:
        unified_msg_origin = "t:FriendMessage:u1"
        message_str = "你好"
        def get_sender_id(self): return "u1"
        def get_group_id(self): return ""
        def get_platform_name(self): return "t"

    class _Req:
        prompt = "原始问题"

    cfg = MemoryConfig()
    cfg.auto_inject_enabled = True
    cfg.persona_inject_enabled = True
    cfg.auto_inject_top_k = 3
    h = InjectHandler(_Svc(cfg))
    req = _Req()
    asyncio.run(h.handle_inject(_Event(), req))
    assert "[用户画像]" in req.prompt, req.prompt
    assert "稳定背景：喜欢推理" in req.prompt
    assert req.prompt.rstrip().endswith("原始问题")
    print("  persona block injected even with no memory hits: OK")


if __name__ == "__main__":
    test_fields_defaults()
    test_persona_store_crud()
    test_build_persona_with_llm()
    test_build_persona_rule_llm_none()
    test_inject_persona_block()
    print("\nALL v1.8 persona smoke tests passed.")