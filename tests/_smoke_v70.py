"""Smoke v1.67.1: issue #8 — TextPart path order + visual separation.

Real-world bug (reported 2026-07-02): with auto_inject_enabled=True and
the v1.66+ TextPart path, the four injected blocks were ending up in
the reverse of the declared order persona->relation->memory->diary,
because the previous loop used `parts_list.insert(0, part)` which is
LIFO.  On top of that, the LLM could not visually distinguish the
injected blocks from the real user message — they were treated as
parallel user questions and answered as such.

This smoke covers the TextPart code path end to end with all four
blocks enabled, asserting:
  ①  declared order is preserved: persona -> relation -> memory -> diary
  ②  every TextPart is wrapped in <engram-context>...</engram-context>
  ③  every TextPart is marked temp (mark_as_temp was called)
  ④  the inner [用户画像] / [人物关系] / [近期对话] / [最近日记] label
      is still present (backward compatibility)
  ⑤  the real user message is not modified

v28 and v31 only exercise the fallback (string-concat) path because
their fake _Req / _Req does not have an `extra_user_content_parts`
attribute.  That is why the original bug slipped past CI.  v70
installs a minimal `astrbot.core.agent.message` stub so the real
TextPart import succeeds and the structural code path is taken.
"""
import sys, os, types


def _install_stub():
    a = types.ModuleType("astrbot"); ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star"); em = types.ModuleType("astrbot.api.event")
    msg_pkg = types.ModuleType("astrbot.core")
    msg_ag = types.ModuleType("astrbot.core.agent")
    msg_am = types.ModuleType("astrbot.core.agent.message")
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
    # Minimal TextPart — real one in astrbot.core.agent.message; we only
    # need the surface the plugin uses: text=, type=, mark_as_temp().
    class TextPart:
        def __init__(self, text, type="text"):
            self.text = text
            self.type = type
            self._is_temp = False
        def mark_as_temp(self):
            self._is_temp = True
            return self
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    msg_am.TextPart = TextPart
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em
    sys.modules["astrbot.core"] = msg_pkg
    sys.modules["astrbot.core.agent"] = msg_ag
    sys.modules["astrbot.core.agent.message"] = msg_am


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from hippocampus.config import MemoryConfig
from handlers.event import InjectHandler


def banner(m):
    print("\n=== " + m + " ===")


class _FakeEvent:
    def __init__(self, content):
        self._content = content
        self.unified_msg_origin = "test:FriendMessage:u1"
        self.message_str = content
    def get_sender_id(self): return "actor-1"
    def get_group_id(self): return ""
    def get_platform_name(self): return "test"


class _ReqWithParts:
    """Real-ish ProviderRequest: has the structural slot the v1.66 path uses."""
    def __init__(self, prompt="查一下engram注入机制"):
        self.prompt = prompt
        self.extra_user_content_parts = []


class _FakeEngram:
    def __init__(self, summary):
        self.summary = summary
        self.created_at = 0.0


class _FakeResult:
    def __init__(self, engrams):
        self.engrams = engrams


class _Rel:
    def __init__(self, subject, predicate, obj=""):
        self.subject = subject; self.predicate = predicate; self.object = obj


class _FakePersona:
    def __init__(self, summary="", tags=None):
        self.summary = summary; self.tags = tags or []


class _FakeService:
    """Service stub with all four recall paths returning data."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_cue = None
    def get_persona(self, actor_id):
        return _FakePersona(summary="稳定背景：偏好技术讨论", tags=["技术", "AstrBot"])
    def recall(self, cue):
        self.last_cue = cue
        return _FakeResult([
            _FakeEngram("用户喜欢侦探小说"),
            _FakeEngram("用户在做 AstrBot 插件"),
        ])
    def recall_relations(self, query, top_n=3, min_confidence=0.0):
        return [_Rel("chiriu", "works_on", "engram-core")]
    def recall_diary_chunks(self, query, top_n=1, min_score=0.0, persona_id=""):
        return [("- 用户最近在排查 engram 注入块顺序问题", 0.9)]


def _cfg(**over):
    c = MemoryConfig()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def test_textpart_path_order_and_wrapping():
    banner("TextPart path: 4 blocks, declared order + <engram-context> wrap + mark_as_temp")
    cfg = _cfg(
        auto_inject_enabled=True,
        auto_inject_top_k=3,
        auto_inject_position="before",
        persona_inject_enabled=True,
        relation_inject_top_n=3,
        diary_inject_top_n=1,
    )
    h = InjectHandler(_FakeService(cfg))
    req = _ReqWithParts(prompt="查一下engram注入机制")
    asyncio.run(h.handle_inject(_FakeEvent("查一下engram注入机制"), req))

    parts = req.extra_user_content_parts
    # ① exactly 4 parts, declared order preserved
    assert len(parts) == 4, len(parts)
    inner_labels = ["[用户画像]", "[人物关系]", "[近期对话]", "[最近日记]"]
    for i, label in enumerate(inner_labels):
        assert label in parts[i].text, (
            f"block {i} missing inner label {label!r}: {parts[i].text!r}"
        )

    # ② every part wrapped in <engram-context>...</engram-context>
    for i, p in enumerate(parts):
        assert p.text.startswith("<engram-context>\n"), (
            f"block {i} not wrapped at start: {p.text[:40]!r}"
        )
        assert p.text.rstrip().endswith("</engram-context>"), (
            f"block {i} not wrapped at end: ...{p.text[-40:]!r}"
        )

    # ③ every part is marked temp
    for i, p in enumerate(parts):
        assert getattr(p, "_is_temp", False) is True, (
            f"block {i} not marked temp: type={p.type!r}"
        )
        assert p.type == "text"

    # ④ the real user message in prompt is untouched
    assert req.prompt == "查一下engram注入机制", req.prompt
    # ⑤ persona tag chips still present
    assert "标签：技术 / AstrBot" in parts[0].text
    assert "稳定背景：偏好技术讨论" in parts[0].text
    print("  order=persona->relation->memory->diary, wrapped, temp: OK")


def test_textpart_path_position_after():
    banner("TextPart path: position=after, blocks appear after existing parts")
    # Pre-existing part in the list (e.g. social_context or a user attachment);
    # engram's blocks must be appended, not prepended, and must not
    # mutate the prior part.
    from astrbot.core.agent.message import TextPart as _TP
    cfg = _cfg(
        auto_inject_enabled=True,
        auto_inject_top_k=1,
        auto_inject_position="after",
        # persona/relation/diary all default-enabled -> expect all 4 engram
        # blocks plus the 1 prior part.
    )
    h = InjectHandler(_FakeService(cfg))
    req = _ReqWithParts(prompt="原始问题")
    req.extra_user_content_parts.append(_TP(text="<prior>existing</prior>", type="text"))
    asyncio.run(h.handle_inject(_FakeEvent("继续"), req))

    parts = req.extra_user_content_parts
    # 1 prior + 4 engram blocks (persona off by default, but relation /
    # memory / diary are on -> 3; corrected: persona is False by default,
    # so 1 prior + 3 engram = 4 parts).
    assert len(parts) == 4, [p.text[:30] for p in parts]
    assert parts[0].text == "<prior>existing</prior>"
    # the original prior part must NOT be re-marked by engram
    assert getattr(parts[0], "_is_temp", False) is False
    # the 3 engram blocks (relation / memory / diary) come after,
    # each wrapped, each temp, in declared order.
    engram_blocks = parts[1:]
    for p in engram_blocks:
        assert p.text.startswith("<engram-context>\n"), p.text[:40]
        assert p.text.rstrip().endswith("</engram-context>"), p.text[-40:]
        assert getattr(p, "_is_temp", False) is True
    assert "[人物关系]" in engram_blocks[0].text
    assert "[近期对话]" in engram_blocks[1].text
    assert "[最近日记]" in engram_blocks[2].text
    print("  after-position: engram blocks appended after prior, declared order kept: OK")


def test_textpart_path_disabled_blocks_skipped():
    banner("TextPart path: only-memory when persona/relation/diary disabled")
    cfg = _cfg(
        auto_inject_enabled=True,
        auto_inject_top_k=2,
        auto_inject_position="before",
        persona_inject_enabled=False,
        relation_inject_top_n=0,
        diary_inject_top_n=0,
    )
    h = InjectHandler(_FakeService(cfg))
    req = _ReqWithParts(prompt="hello")
    asyncio.run(h.handle_inject(_FakeEvent("hello"), req))

    parts = req.extra_user_content_parts
    assert len(parts) == 1, len(parts)
    assert "[近期对话]" in parts[0].text
    assert "[用户画像]" not in parts[0].text
    assert "[人物关系]" not in parts[0].text
    assert "[最近日记]" not in parts[0].text
    assert parts[0].text.startswith("<engram-context>\n")
    assert parts[0].text.rstrip().endswith("</engram-context>")
    assert getattr(parts[0], "_is_temp", False) is True
    print("  selective gating respected + wrap/mark present: OK")


if __name__ == "__main__":
    test_textpart_path_order_and_wrapping()
    test_textpart_path_position_after()
    test_textpart_path_disabled_blocks_skipped()
    print("\nALL v1.67.1 issue #8 (TextPart order + wrap) smoke tests passed.")
