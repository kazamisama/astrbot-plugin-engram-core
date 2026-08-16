"""Smoke v1.31: ObserveHandler.handle_poke records a QQ poke notice as one
named line (real actor names) into daily cache + conversation buffer, so the
summary doesn't lose who poked whom. Mirrors the v41 stub harness.
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
from hippocampus.llm import LLMProvider


def banner(m):
    print(chr(10) + "=== " + m + " ===")


class _MsgObj:
    def __init__(self, raw):
        self.raw_message = raw
        self.self_id = raw.get("self_id")


class _Bot:
    async def call_action(self, action, **kw):
        if action == "get_login_info":
            return {"nickname": "\u6a58\u96ea\u8389"}
        if action == "get_group_member_info":
            return {"card": "\u88ab\u6233\u7684\u4eba", "nickname": "nick"}
        return {}


class _PokeEvent:
    """Poke notice event: get_message_str empty, raw_message carries poke."""
    def __init__(self, raw, sender_name):
        self.message_obj = _MsgObj(raw)
        self._sender_name = sender_name
        self.unified_msg_origin = "qq:GroupMessage:" + str(raw.get("group_id") or "")
        self.message_str = ""
        self.bot = _Bot()
    def get_group_id(self): return str(self.message_obj.raw_message.get("group_id") or "")
    def get_sender_id(self): return str(self.message_obj.raw_message.get("user_id") or "")
    def get_sender_name(self): return self._sender_name
    def get_platform_name(self): return "qq"
    def get_self_id(self): return str(self.message_obj.raw_message.get("self_id") or "")
    def get_message_str(self): return ""


def _svc(tmp):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.tiering_enabled = False
    cfg.summary_mode_enabled = True
    cfg.diary_enabled = True
    cfg.summary_idle_seconds_group = 600.0
    svc = MemoryService(cfg=cfg)
    return svc


def _run(coro):
    # Python 3.10+ / 3.14 compatible: get_event_loop() no longer creates
    # a loop implicitly, so use asyncio.run() for each isolated coroutine.
    return asyncio.run(coro)


def test_poke_to_bot_records_named_line():
    banner("poke notice -> named line in daily cache + buffer")
    from handlers.event.observe import ObserveHandler
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    h = ObserveHandler(svc)
    raw = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
           "self_id": "100", "user_id": "200", "target_id": "100", "group_id": "g9"}
    ev = _PokeEvent(raw, "\u5f20\u4e09")
    _run(h.handle_poke(ev))
    # daily cache must contain the poke line with sender real name as speaker
    ds = svc.diary_store
    lines = ds.lines_for_day(None) if hasattr(ds, "lines_for_day") else None
    # FIX v1.59: DiaryStore.add_line is buffered (v1.42 BUG-7). The
    # poke line lives in ds._buffer until the batch fills, a read
    # query goes through ds (which auto-flushes), flush_now() is
    # called, or close() runs. A raw sqlite3 connection skips all
    # of that, so it can't see the in-memory buffer. Flush first.
    try:
        ds.flush_now()
    except Exception:
        pass
    # fallback: query db directly
    import sqlite3
    con = sqlite3.connect(svc.cfg.sqlite_path)
    rows = con.execute("SELECT speaker, content, is_bot FROM daily_messages").fetchall()
    con.close()
    assert len(rows) == 1, "exactly one poke line, got " + str(len(rows))
    speaker, content, is_bot = rows[0]
    assert speaker == "\u5f20\u4e09", "speaker should be sender real name, got " + repr(speaker)
    assert "\u5f20\u4e09" in content, "content must name the sender, got " + repr(content)
    assert "\u6a58\u96ea\u8389" in content, "content must name the bot target, got " + repr(content)
    assert is_bot == 0, "sender is not bot"
    print("  poke line: " + repr(content))
    try: svc.close()
    except Exception: pass


def test_non_poke_notice_ignored():
    banner("non-poke notice -> ignored")
    from handlers.event.observe import ObserveHandler
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    h = ObserveHandler(svc)
    raw = {"post_type": "notice", "notice_type": "group_increase", "sub_type": "approve",
           "self_id": "100", "user_id": "200", "group_id": "g9"}
    ev = _PokeEvent(raw, "\u5f20\u4e09")
    _run(h.handle_poke(ev))
    import sqlite3
    con = sqlite3.connect(svc.cfg.sqlite_path)
    try:
        n = con.execute("SELECT count(*) FROM daily_messages").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0  # table never created => nothing inserted => ignored
    con.close()
    assert n == 0, "non-poke notice must be ignored"
    print("  non-poke notice ignored OK")
    try: svc.close()
    except Exception: pass


def main():
    test_poke_to_bot_records_named_line()
    test_non_poke_notice_ignored()
    print(chr(10) + "v1.31 poke smoke: ALL PASS")


if __name__ == "__main__":
    main()
