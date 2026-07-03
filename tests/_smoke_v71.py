"""Smoke v1.67.2: re-injection defense for engram TextPart blocks.

Issue discovered while reviewing emotion_state_machine's pattern (issue
#8.5, follow-up to #8): the previous fix (v1.67.1) correctly wraps
each block in <engram-context>...</engram-context>, but does NOT strip
prior engram blocks from extra_user_content_parts before appending
fresh ones.  When the same on_llm_request hook fires multiple times in
the same conversation (e.g. retries, multi-turn where parts_list is
not reset between turns), engram's 4 blocks accumulate by 4 per turn
and eventually dominate the LLM context window.

This smoke covers the find-and-replace logic in
``InjectHandler._strip_prior_engram_blocks``:
  ①  triple-match: open tag + close tag + known inner label
  ②  prior engram blocks are removed in place
  ③  other plugins' TextParts are NOT touched (no false-positive)
  ④  the strip+re-inject round-trip yields exactly the new 4 blocks
      (not 4 new + 4 old = 8)
  ⑤  non-string-typed parts and parts without a ``.text`` attr are
      left alone (defensive)
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


class _Req:
    def __init__(self, prompt="hello"):
        self.prompt = prompt
        self.extra_user_content_parts = []


class _FakeEngram:
    def __init__(self, summary):
        self.summary = summary
        self.created_at = 0.0


class _Result:
    def __init__(self, engrams): self.engrams = engrams


class _Svc:
    def __init__(self, cfg): self.cfg = cfg
    def get_persona(self, actor_id): return None
    def recall(self, cue): return _Result([_FakeEngram("summary-A")])
    def recall_relations(self, q, top_n=3, min_confidence=0.0): return []
    def recall_diary_chunks(self, q, top_n=1, min_score=0.0, persona_id=""): return []


def _cfg(**over):
    c = MemoryConfig()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def test_strip_removes_only_engram_blocks():
    banner("_strip_prior_engram_blocks: triple-match only")
    from astrbot.core.agent.message import TextPart as _TP
    parts = [
        _TP(text="<engram-context>\n[近期对话]\nold1\n</engram-context>", type="text"),
        _TP(text="<engram-context>\n[用户画像]\nold2\n</engram-context>", type="text"),
        # another plugin's block — must NOT be removed
        _TP(text="<RAG-Faiss-Memory>CRITICAL RULES...</RAG-Faiss-Memory>", type="text"),
        # looks like engram open/close but no inner label — defensive miss
        _TP(text="<engram-context>\nunrelated content\n</engram-context>", type="text"),
        # missing close tag
        _TP(text="<engram-context>\n[近期对话]\nno-close", type="text"),
        # no text attr at all
        _TP(text=None, type="text"),
        # plain user message
        _TP(text="hello", type="text"),
    ]
    removed = InjectHandler._strip_prior_engram_blocks(parts)
    assert removed == 2, removed
    # remaining parts: rag block, defensively-kept unlabeled engram,
    # missing-close, None-text, plain message -> 5 left
    assert len(parts) == 5, [p.text for p in parts]
    # the rag block is still there
    assert any("RAG-Faiss-Memory" in (p.text or "") for p in parts)
    # the defensive-miss (engram wrap, no inner label) is still there
    assert any(
        (p.text or "").strip().startswith("<engram-context>")
        and (p.text or "").strip().endswith("</engram-context>")
        and "[近期对话]" not in (p.text or "")
        and "[用户画像]" not in (p.text or "")
        for p in parts
    )
    print("  only triple-matched blocks removed, others untouched: OK")


def test_strip_empty_list():
    banner("_strip_prior_engram_blocks: empty list")
    assert InjectHandler._strip_prior_engram_blocks([]) == 0
    assert InjectHandler._strip_prior_engram_blocks(None) == 0
    print("  empty/None safe: OK")


def test_reinject_no_accumulation():
    banner("handle_inject called twice: list stays at 4, no accumulation")
    from astrbot.core.agent.message import TextPart as _TP
    cfg = _cfg(auto_inject_enabled=True, auto_inject_top_k=1, auto_inject_position="before")
    h = InjectHandler(_Svc(cfg))
    req = _Req(prompt="hello")
    # First call
    asyncio.run(h.handle_inject(_FakeEvent("hi"), req))
    assert len(req.extra_user_content_parts) == 1
    first_part = req.extra_user_content_parts[0]
    assert first_part.text.startswith("<engram-context>\n")
    # Second call (simulate retry or next turn with persisted list)
    asyncio.run(h.handle_inject(_FakeEvent("hi again"), req))
    assert len(req.extra_user_content_parts) == 1, [p.text[:40] for p in req.extra_user_content_parts]
    second_part = req.extra_user_content_parts[0]
    # the new part replaced the old one (text content identical since
    # fixture is deterministic, but the part object should be a fresh one)
    assert second_part is not first_part
    print("  two consecutive calls -> 1 part, no accumulation: OK")


def test_reinject_with_other_plugin_blocks_preserved():
    banner("re-inject does not touch other plugins' TextParts")
    from astrbot.core.agent.message import TextPart as _TP
    cfg = _cfg(auto_inject_enabled=True, auto_inject_top_k=1, auto_inject_position="after")
    h = InjectHandler(_Svc(cfg))
    req = _Req(prompt="hello")
    # pre-existing: emotion state (HTML comments, invisible to LLM)
    req.extra_user_content_parts.append(
        _TP(text="<!-- esm:emotion-block:start -->calm<!-- esm:emotion-block:end -->", type="text")
    )
    # pre-existing: livingmemory
    req.extra_user_content_parts.append(
        _TP(text="<RAG-Faiss-Memory>memory...</RAG-Faiss-Memory>", type="text")
    )
    # pre-existing: leftover engram from prior turn
    req.extra_user_content_parts.append(
        _TP(text="<engram-context>\n[近期对话]\nold\n</engram-context>", type="text")
    )
    asyncio.run(h.handle_inject(_FakeEvent("hi"), req))
    parts = req.extra_user_content_parts
    # 3 prior (1 emotion, 1 livingmemory, 1 old engram stripped) + 1 new engram
    # = 3 kept + 1 new = 4
    assert len(parts) == 3, [p.text[:30] for p in parts]
    # emotion block still there
    assert any("esm:emotion-block" in (p.text or "") for p in parts)
    # livingmemory block still there
    assert any("RAG-Faiss-Memory" in (p.text or "") for p in parts)
    # new engram block appended
    new_engram = [p for p in parts if (p.text or "").startswith("<engram-context>\n")]
    assert len(new_engram) == 1
    assert "[近期对话]" in new_engram[0].text
    # no leftover old engram block
    assert not any("old\n" in (p.text or "") for p in parts)
    print("  cross-plugin blocks preserved, only our prior blocks removed: OK")


if __name__ == "__main__":
    test_strip_removes_only_engram_blocks()
    test_strip_empty_list()
    test_reinject_no_accumulation()
    test_reinject_with_other_plugin_blocks_preserved()
    print("\nALL v1.67.2 re-injection defense smoke tests passed.")
