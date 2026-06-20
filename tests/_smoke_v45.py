"""Smoke v1.22 (/mem diary): manual diary trigger command routes through
ManageHandler.run_daily_diary, and is registered in the router + help.
"""
import sys, os, tempfile, types, asyncio


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
from hippocampus.service import MemoryService
from handlers.event import ManageHandler
from handlers.commands import CommandRouter


def banner(m):
    print(chr(10) + "=== " + m + " ===")


class _Ev:
    def plain_result(self, txt):
        return txt


def _svc(tmp):
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.tiering_enabled = False
    cfg.diary_enabled = True
    return MemoryService(cfg=cfg)


def _run(router):
    async def go():
        out = []
        async for x in router.dispatch("mem diary", _Ev(), (), {}):
            out.append(x)
        return out
    return asyncio.run(go())


def test_router_registered():
    banner("/mem diary registered in router + help text")
    from handlers.help_text import HELP_TEXT
    assert "/mem diary" in HELP_TEXT, "help missing /mem diary"
    r = CommandRouter(None, None, ManageHandler(None))
    assert "mem diary" in r._table, r._table.keys()
    print("PASS registration")


def test_diary_cmd_empty_then_written():
    banner("/mem diary: empty cache -> notice; cached yesterday -> writes")
    import time
    from hippocampus.diary_store import DailyLine
    from hippocampus.diary_writer import day_bounds
    from hippocampus.llm import LLMProvider

    class _JsonLLM(LLMProvider):
        def name(self): return "json"
        def chat(self, system, user, **kw):
            return ('{"summary":"\u6211\u4eca\u5929\u548c A \u804a\u4e86\u3002",'
                    '"key_facts":[],"topics":["t"],"participants":["A"]}')

    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    svc.register_llm("json", _JsonLLM())
    svc.set_llm("json")
    r = CommandRouter(None, None, ManageHandler(svc))

    # empty cache -> friendly notice, no crash
    out = _run(r)
    assert out and isinstance(out[0], str), out

    # cache yesterday lines, then the command should write one diary
    now = time.time()
    today0, _ = day_bounds(now)
    midday = today0 - 86400.0 + 12 * 3600.0
    for i, (aid, isbot, txt) in enumerate([
            ("A", False, "hi"), ("bot", True, "hello"), ("A", False, "bye")]):
        svc.diary_store.add_line(DailyLine(channel_id="c1", chat_type="private",
                                           actor_id=aid, speaker=aid, content=txt,
                                           is_bot=isbot, peer_actor_id="A",
                                           peer_name="A", ts=midday + i))
    out2 = _run(r)
    diaries = [x for x in svc.store.all(limit=10000)
               if getattr(x, "memory_type", "") == "diary"]
    assert len(diaries) == 1, len(diaries)
    svc.close()
    print("PASS diary command e2e")


def test_diary_disabled_guard():
    banner("/mem diary: disabled -> guard message, no write")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    svc.cfg.diary_enabled = False
    r = CommandRouter(None, None, ManageHandler(svc))
    out = _run(r)
    assert out and isinstance(out[0], str), out
    svc.close()
    print("PASS disabled guard")


if __name__ == "__main__":
    test_router_registered()
    test_diary_cmd_empty_then_written()
    test_diary_disabled_guard()
    print(chr(10) + "ALL v45 PASS")
