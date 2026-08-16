"""Smoke v1.18 (B-1 wiring): ObserveHandler routes via ConversationBuffer,
bot replies captured, summary stored as one engram. Uses astrbot stub.
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


class _JsonLLM(LLMProvider):
    def name(self): return "json"
    def chat(self, system, user, **kw):
        return '{"summary":"\u4f1a\u8bdd\u603b\u7ed3","key_facts":["a"],"topics":["t"],"participants":["A"],"relations":[]}'


class _Event:
    """Minimal AstrBot-like event with the getters _extract uses."""
    def __init__(self, gid, sender, text):
        self._gid = gid; self._sender = sender; self._text = text
        self.unified_msg_origin = "qq:Group:" + (gid or sender)
        self.message_str = text
    def get_group_id(self): return self._gid
    def get_sender_id(self): return self._sender
    def get_sender_name(self): return self._sender
    def get_platform_name(self): return "qq"
    def get_message_str(self): return self._text


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
    cfg.per_message_ingest_debug = False
    cfg.summary_idle_seconds_group = 600.0
    svc = MemoryService(cfg=cfg)
    svc.register_llm("json", _JsonLLM())
    svc.set_llm("json")
    return svc


def test_summary_mode_stores_one_engram():
    banner("summary mode: buffer -> flush -> one summary engram")
    from handlers.event.observe import ObserveHandler
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    h = ObserveHandler(svc)
    ev1 = _Event("g1", "A", "\u4eca\u665a\u5403\u706b\u9505\u5417")
    ev2 = _Event("g1", "B", "\u884c\u554a")
    asyncio.run(h.handle_message(ev1))
    asyncio.run(h.handle_message(ev2))
    # bot reply
    class _Resp:
        completion_text = "\u6211\u5e2e\u4f60\u4eec\u8ba2\u4f4d"
    asyncio.run(h.handle_bot_message(ev2, _Resp.completion_text))
    # nothing stored yet (not flushed)
    assert len(svc.store.all(limit=10)) == 0, "should not store before flush"
    # force flush
    h._get_conv_buffer().flush_all()
    rows = svc.store.all(limit=10)
    assert len(rows) == 1, "exactly one summary engram, got " + str(len(rows))
    e = rows[0]
    assert e.summary == "\u4f1a\u8bdd\u603b\u7ed3"
    # identity stamp present
    assert any(t.startswith("chat:group") for t in (e.tags or [])), e.tags
    print("  one summary engram + identity stamp OK")
    try: svc.close()
    except Exception: pass


def test_debug_per_message_ingest():
    banner("debug flag: per-message ingest also stores raw")
    from handlers.event.observe import ObserveHandler
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    svc.cfg.per_message_ingest_debug = True
    svc.cfg.session_aggregate_enabled = False
    h = ObserveHandler(svc)
    ev = _Event("g2", "A", "\u4f60\u597d\u4e16\u754c\u6d4b\u8bd5")
    asyncio.run(h.handle_message(ev))
    # per-message ingest stored one raw engram immediately
    rows = svc.store.all(limit=10)
    assert len(rows) >= 1, "debug per-message ingest should store raw"
    print("  debug per-message ingest OK")
    try: svc.close()
    except Exception: pass


def main():
    test_summary_mode_stores_one_engram()
    test_debug_per_message_ingest()
    print(chr(10) + "v1.18 B-1 wiring smoke: ALL PASS")


if __name__ == "__main__":
    main()
