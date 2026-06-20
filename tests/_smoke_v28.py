"""Smoke v1.5: auto memory injection (on_llm_request) path.

Covers handlers.event.InjectHandler.handle_inject end to end with a
fake service + fake event + fake ProviderRequest:
  - disabled (auto_inject_enabled=False) -> req.prompt untouched
  - enabled  -> top-k summaries spliced into req.prompt (before/after)
  - empty query / no hits -> no-op
  - exceptions inside recall -> swallowed, req.prompt untouched

Also asserts ConfigManager exposes the 3 new fields with correct
defaults.
"""
import sys, os, types


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

import asyncio

from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS
from handlers.event import InjectHandler


def banner(msg):
    print("\n=== " + msg + " ===")


class _FakeEvent:
    def __init__(self, content):
        self._content = content
        self.unified_msg_origin = "test:FriendMessage:u1"
        self.message_str = content
    def get_sender_id(self):
        return "actor-1"
    def get_group_id(self):
        return ""
    def get_platform_name(self):
        return "test"


class _FakeReq:
    def __init__(self, prompt="你好"):
        self.prompt = prompt


class _FakeEngram:
    def __init__(self, summary):
        self.summary = summary


class _FakeResult:
    def __init__(self, engrams):
        self.engrams = engrams


class _FakeService:
    def __init__(self, cfg, engrams=None, raise_on_recall=False):
        self.cfg = cfg
        self._engrams = engrams or []
        self._raise = raise_on_recall
        self.last_cue = None
    def recall(self, cue):
        self.last_cue = cue
        if self._raise:
            raise RuntimeError("boom")
        return _FakeResult(list(self._engrams))


def _cfg(**over):
    c = MemoryConfig()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def test_fields_registered_with_defaults():
    banner("3 auto-inject fields registered + defaults")
    for f in ("auto_inject_enabled", "auto_inject_top_k", "auto_inject_position"):
        assert f in _FIELDS, f
        assert f in LABELS, f
    cm = ConfigManager({})
    cfg = cm.memory_config
    assert cfg.auto_inject_enabled is True
    assert cfg.auto_inject_top_k == 3
    assert cfg.auto_inject_position == "before"
    print("  defaults: enabled=True top_k=3 position=before OK")


def test_disabled_is_noop():
    banner("disabled -> req.prompt untouched")
    svc = _FakeService(_cfg(auto_inject_enabled=False),
                       engrams=[_FakeEngram("用户喜欢侦探小说")])
    h = InjectHandler(svc)
    req = _FakeReq("原始消息")
    asyncio.run(h.handle_inject(_FakeEvent("查一下"), req))
    assert req.prompt == "原始消息", req.prompt
    assert svc.last_cue is None  # recall never called
    print("  no injection when disabled: OK")


def test_enabled_before():
    banner("enabled before -> memory spliced ahead of prompt")
    svc = _FakeService(_cfg(auto_inject_enabled=True, auto_inject_top_k=2,
                            auto_inject_position="before"),
                       engrams=[_FakeEngram("用户喜欢侦探小说"),
                                _FakeEngram("用户在做 AstrBot 插件"),
                                _FakeEngram("第三条不该出现")])
    h = InjectHandler(svc)
    req = _FakeReq("我刚才说到哪了")
    asyncio.run(h.handle_inject(_FakeEvent("我刚才说到哪了"), req))
    assert req.prompt.startswith("[近期对话]"), req.prompt  # v1.20 B-3 relabel
    assert "用户喜欢侦探小说" in req.prompt
    assert "用户在做 AstrBot 插件" in req.prompt
    assert "第三条不该出现" not in req.prompt  # top_k=2 cap
    assert req.prompt.rstrip().endswith("我刚才说到哪了"), req.prompt
    # cue carried actor/channel from _extract
    assert svc.last_cue.actor_id == "actor-1"
    print("  top-2 injected before prompt, k cap honored: OK")


def test_enabled_after():
    banner("enabled after -> memory appended after prompt")
    svc = _FakeService(_cfg(auto_inject_enabled=True, auto_inject_top_k=1,
                            auto_inject_position="after"),
                       engrams=[_FakeEngram("用户喜欢侦探小说")])
    h = InjectHandler(svc)
    req = _FakeReq("继续")
    asyncio.run(h.handle_inject(_FakeEvent("继续"), req))
    assert req.prompt.startswith("继续"), req.prompt
    assert req.prompt.rstrip().endswith("用户喜欢侦探小说"), req.prompt
    print("  injected after prompt: OK")


def test_empty_query_noop():
    banner("empty query -> no-op")
    svc = _FakeService(_cfg(auto_inject_enabled=True),
                       engrams=[_FakeEngram("x")])
    h = InjectHandler(svc)
    req = _FakeReq("原文")
    asyncio.run(h.handle_inject(_FakeEvent(""), req))
    assert req.prompt == "原文", req.prompt
    print("  empty query produced no injection: OK")


def test_no_hits_noop():
    banner("no recall hits -> no-op")
    svc = _FakeService(_cfg(auto_inject_enabled=True), engrams=[])
    h = InjectHandler(svc)
    req = _FakeReq("原文")
    asyncio.run(h.handle_inject(_FakeEvent("查询"), req))
    assert req.prompt == "原文", req.prompt
    print("  no hits -> prompt untouched: OK")


def test_recall_exception_swallowed():
    banner("recall raises -> swallowed, prompt untouched")
    svc = _FakeService(_cfg(auto_inject_enabled=True), raise_on_recall=True)
    h = InjectHandler(svc)
    req = _FakeReq("原文")
    asyncio.run(h.handle_inject(_FakeEvent("查询"), req))
    assert req.prompt == "原文", req.prompt
    print("  exception swallowed, LLM request safe: OK")


if __name__ == "__main__":
    test_fields_registered_with_defaults()
    test_disabled_is_noop()
    test_enabled_before()
    test_enabled_after()
    test_empty_query_noop()
    test_no_hits_noop()
    test_recall_exception_swallowed()
    print("\nALL v1.5 auto-inject smoke tests passed.")