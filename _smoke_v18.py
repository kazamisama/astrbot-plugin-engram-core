"""Smoke v1.4 SessionFilter + /mem session: 6 filter rules + cmd_mem_session.

Tests:
- platform blocklist / allowlist
- channel blocklist / allowlist
- actor allowlist
- blocked_keywords (case-insensitive substring)
- observe() returns a denied Engram with _filter_denied / _filter_reason
- observe() does NOT store denied engrams in the DB
- format_session() renders the current policy
- /mem session is registered on HippocampusStar
- enable_session_filter=False (default) -> all messages captured (back-compat)
"""
import os, tempfile, sys, types


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
import main
from hippocampus import MemoryService, MemoryConfig
from hippocampus.session_filter import (
    SessionFilter, FilterContext, FilterVerdict, FilterDecision,
)
from handlers import format_session


def banner(t): print("\n=== " + t + " ===")


def test_filter_disabled_default():
    banner("filter disabled by default: all messages pass")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32, enable_prospective=False)
    svc = MemoryService(cfg)
    try:
        e = svc.observe(session_id="s1", actor_id="alice", platform="qq",
                        channel_id="group-x", content="this is spam content")
        assert not getattr(e, "_filter_denied", False)
        assert e.summary == "this is spam content" or "spam" in (e.content or "")
        assert len(svc.store.list_active(limit=100)) == 1
        print("  default OFF: OK (1 engram stored)")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_filter_enabled_keyword_blocks():
    banner("filter ON + blocked_keywords drops the message")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32, enable_prospective=False,
                       enable_session_filter=True, blocked_keywords=["spam", "广告"])
    svc = MemoryService(cfg)
    try:
        e_bad = svc.observe(session_id="s1", actor_id="alice", platform="qq",
                            channel_id="group-x", content="this is SPAM content")
        assert getattr(e_bad, "_filter_denied", False), "should be denied"
        assert getattr(e_bad, "_filter_reason") == "keyword_blocked"
        assert getattr(e_bad, "_filter_matched") == "spam"  # case-insensitive match
        # nothing stored
        assert len(svc.store.list_active(limit=100)) == 0
        e_good = svc.observe(session_id="s1", actor_id="alice", platform="qq",
                             channel_id="group-x", content="this is fine content")
        assert not getattr(e_good, "_filter_denied", False)
        assert len(svc.store.list_active(limit=100)) == 1
        print("  keyword filter: OK (1 denied, 1 stored)")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_filter_platform_allowlist():
    banner("platform allowlist: only listed platforms pass")
    sf = SessionFilter(MemoryConfig(
        enable_session_filter=True, platform_allowlist=["qq", "wx"]))
    assert sf.is_allowed(FilterContext(platform="qq", channel_id="c1", actor_id="u1", content="x"))
    assert sf.is_allowed(FilterContext(platform="wx", channel_id="c1", actor_id="u1", content="x"))
    d = sf.decide(FilterContext(platform="telegram", channel_id="c1", actor_id="u1", content="x"))
    assert not d.is_pass() and d.reason == "platform_not_allowed"
    print("  platform allowlist: OK")


def test_filter_platform_blocklist_overrides():
    banner("platform blocklist wins over allowlist")
    sf = SessionFilter(MemoryConfig(
        enable_session_filter=True,
        platform_allowlist=["qq", "telegram"],
        platform_blocklist=["telegram"]))
    # qq allowed
    assert sf.is_allowed(FilterContext(platform="qq", channel_id="c1", actor_id="u1", content="x"))
    # telegram in both -> block wins
    d = sf.decide(FilterContext(platform="telegram", channel_id="c1", actor_id="u1", content="x"))
    assert not d.is_pass() and d.reason == "platform_blocked"
    print("  blocklist priority: OK")


def test_filter_channel_rules():
    banner("channel allowlist + blocklist")
    sf = SessionFilter(MemoryConfig(
        enable_session_filter=True,
        channel_allowlist=["room-1", "room-2"],
        channel_blocklist=["room-2"]))
    # room-1 allowed
    assert sf.is_allowed(FilterContext(platform="qq", channel_id="room-1", actor_id="u1", content="x"))
    # room-2 in both -> block wins
    d = sf.decide(FilterContext(platform="qq", channel_id="room-2", actor_id="u1", content="x"))
    assert not d.is_pass() and d.reason == "channel_blocked"
    # room-3 not in allow -> denied
    d = sf.decide(FilterContext(platform="qq", channel_id="room-3", actor_id="u1", content="x"))
    assert not d.is_pass() and d.reason == "channel_not_allowed"
    print("  channel rules: OK")


def test_filter_actor_allowlist():
    banner("actor allowlist: only listed users captured")
    sf = SessionFilter(MemoryConfig(
        enable_session_filter=True, actor_allowlist=["alice", "bob"]))
    assert sf.is_allowed(FilterContext(platform="qq", channel_id="c1", actor_id="alice", content="x"))
    d = sf.decide(FilterContext(platform="qq", channel_id="c1", actor_id="charlie", content="x"))
    assert not d.is_pass() and d.reason == "actor_not_allowed"
    print("  actor allowlist: OK")


def test_filter_disabled_overrides_all():
    banner("enable_session_filter=False -> all pass regardless of rules")
    sf = SessionFilter(MemoryConfig(
        enable_session_filter=False,
        blocked_keywords=["spam"],
        platform_allowlist=["qq"]))
    # spam normally would block; with enable=False it's pass
    d = sf.decide(FilterContext(platform="telegram", channel_id="c1", actor_id="u1", content="SPAM"))
    assert d.is_pass() and d.reason == "disabled"
    print("  disabled overrides: OK")


def test_format_session_renders():
    banner("format_session: shows current policy + quick test")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32, enable_prospective=False,
                       enable_session_filter=True,
                       platform_allowlist=["qq", "wx"],
                       blocked_keywords=["spam", "广告"])
    svc = MemoryService(cfg)
    try:
        out = format_session(svc)
        assert "## session filter" in out
        assert "enabled:        True" in out
        assert "qq, wx" in out  # allowlist rendered
        assert "spam" in out
        # quick test section should appear
        assert "### quick test" in out
        assert "PASS" in out or "DENY" in out
        print("  format_session: OK")
        print("  ---")
        for ln in out.splitlines():
            print("   ", ln)
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_format_session_disabled():
    banner("format_session with filter disabled: omits quick test")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32, enable_prospective=False)
    svc = MemoryService(cfg)
    try:
        out = format_session(svc)
        assert "enabled:        False" in out
        assert "### quick test" not in out
        print("  disabled format: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_cmd_mem_session_registered():
    banner("/mem session command registered on HippocampusStar")
    assert hasattr(main.HippocampusStar, "cmd_mem_session")
    print("  cmd_mem_session: OK")


if __name__ == "__main__":
    test_filter_disabled_default()
    test_filter_enabled_keyword_blocks()
    test_filter_platform_allowlist()
    test_filter_platform_blocklist_overrides()
    test_filter_channel_rules()
    test_filter_actor_allowlist()
    test_filter_disabled_overrides_all()
    test_format_session_renders()
    test_format_session_disabled()
    test_cmd_mem_session_registered()
    print("\nALL OK")