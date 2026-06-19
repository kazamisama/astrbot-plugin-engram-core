"""Smoke v1.4.6: synthetic-event filter in ObserveHandler.

Scope:
- handlers.event.observe._is_synthetic drops bot-internal cron / wake
  replays so they never enter episodic memory.
- Real user messages still pass through to MemoryService.observe().

Root cause this guards: Engram listens on EventMessageType.ALL, so a
sibling plugin (proactive-reply) replaying a CronMessageEvent wake
prompt would otherwise be stored as if it were a user message
(actor=anonymous/cron, content="[主动消息唤醒]...").
"""
import os, sys, types, asyncio


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
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent
    em.EventMessageType = _MT
    sys.modules["astrbot"] = a
    sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm
    sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def banner(t_):
    print(chr(10) + "=== " + t_ + " ===")


class _RecordingService:
    """Stand-in MemoryService that records observe() calls."""
    def __init__(self):
        self.calls = []
    def observe(self, **kw):
        self.calls.append(kw)
        return object()


def test_is_synthetic_cron_platform():
    banner("_is_synthetic: cron platform dropped")
    from handlers.event.observe import _is_synthetic
    assert _is_synthetic({"platform": "cron", "content": "hi"}) is True
    assert _is_synthetic({"platform": "CRON", "content": "hi"}) is True
    print("  cron platform -> synthetic: OK")


def test_is_synthetic_wake_marker():
    banner("_is_synthetic: wake markers dropped")
    from handlers.event.observe import _is_synthetic
    wake = {"platform": "qq", "content": "[主动消息唤醒]\nfoo"}
    rem = {"platform": "qq", "content": "[预约提醒唤醒]\nbar"}
    assert _is_synthetic(wake) is True
    assert _is_synthetic(rem) is True
    print("  wake markers -> synthetic: OK")


def test_is_synthetic_real_message_passes():
    banner("_is_synthetic: real user message passes")
    from handlers.event.observe import _is_synthetic
    real = {"platform": "qq", "content": "今天天气不错"}
    assert _is_synthetic(real) is False
    print("  real message -> not synthetic: OK")


class _Evt:
    def __init__(self, platform, content, sender="u1"):
        self._p = platform; self._c = content; self._s = sender
        self.unified_msg_origin = platform + ":FriendMessage:s1"
    def get_platform_name(self): return self._p
    def get_sender_id(self): return self._s
    def get_group_id(self): return ""
    def get_message_str(self): return self._c
    @property
    def message_str(self): return self._c


def test_handler_drops_synthetic_keeps_real():
    banner("ObserveHandler.handle_message: drops synthetic, keeps real")
    from handlers.event.observe import ObserveHandler
    svc = _RecordingService()
    h = ObserveHandler(svc)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(h.handle_message(_Evt("cron", "ping")))
        loop.run_until_complete(h.handle_message(_Evt("qq", "[主动消息唤醒]\nx")))
        loop.run_until_complete(h.handle_message(_Evt("qq", "你好啊")))
    finally:
        loop.close()
    assert len(svc.calls) == 1, "expected 1 stored call, got " + str(len(svc.calls))
    assert svc.calls[0]["content"] == "你好啊"
    print("  1 real stored, 2 synthetic dropped: OK")


def main():
    test_is_synthetic_cron_platform()
    test_is_synthetic_wake_marker()
    test_is_synthetic_real_message_passes()
    test_handler_drops_synthetic_keeps_real()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()
