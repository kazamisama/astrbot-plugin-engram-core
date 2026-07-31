"""Smoke v1.73: public cross-plugin coordination helper.

v1.73 extracts InjectHandler's v1.67.2 re-injection defense into the
public ``engram_core_helpers.strip_injected_blocks`` so external plugins
writing to ``req.extra_user_content_parts`` (e.g. xml_structured_output's
``<xml-extra>`` memo blocks) can strip their own prior blocks before
appending fresh ones -- otherwise blocks accumulate linearly across
turns and pollute the LLM context window.

Covers:
  1. attribute-bearing root tag (<xml-extra scope=...>) is matched
  2. inner_labels empty -> root-tag-only match; given -> label required
  3. other plugins' parts / non-string parts are never touched
  4. root_tag accepted with or without angle brackets
  5. InjectHandler._strip_prior_engram_blocks delegates (legacy
     [今日回顾] label still stripped)
  6. 5-turn simulation: engram blocks and xml-extra memo block each
     stay bounded (no accumulation); memo stays LAST regardless of
     auto_inject_position before/after
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
from engram_core_helpers import strip_injected_blocks


def banner(m):
    print("\n=== " + m + " ===")


def _tp(text):
    from astrbot.core.agent.message import TextPart as _TP
    return _TP(text=text, type="text")


MEMO_BLOCK = ('<xml-extra scope="shirley" plugin="xml_structured_output" version="0.3.0">\n'
              '  <memo-block>\n    <item>- task</item>\n  </memo-block>\n</xml-extra>')


def _memo_plugin_round(parts_list):
    """Simulates xml_structured_output v0.3.0: strip own history, then append."""
    strip_injected_blocks(parts_list, root_tag="xml-extra", inner_labels=("memo-block",))
    parts_list.append(_tp(MEMO_BLOCK))


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


def test_attr_bearing_root_tag():
    banner("strip_injected_blocks: attribute-bearing root tag matched")
    parts = [_tp(MEMO_BLOCK), _tp("plain user text")]
    removed = strip_injected_blocks(parts, root_tag="xml-extra", inner_labels=("memo-block",))
    assert removed == 1, removed
    assert len(parts) == 1 and parts[0].text == "plain user text"
    print("  <xml-extra scope=...> stripped, plain text kept: OK")


def test_inner_labels_semantics():
    banner("strip_injected_blocks: inner_labels empty vs given")
    # empty inner_labels -> root-tag-only match
    parts = [_tp("<xml-extra>\nanything\n</xml-extra>"), _tp("keep")]
    removed = strip_injected_blocks(parts, root_tag="xml-extra")
    assert removed == 1 and len(parts) == 1
    # given inner_labels -> block without the label is kept (defensive miss)
    parts = [_tp("<xml-extra>\nno-known-label\n</xml-extra>"), _tp("keep")]
    removed = strip_injected_blocks(parts, root_tag="xml-extra", inner_labels=("memo-block",))
    assert removed == 0 and len(parts) == 2
    print("  tag-only match works; label filter prevents false positive: OK")


def test_other_plugins_untouched():
    banner("strip_injected_blocks: other plugins / non-string parts untouched")
    parts = [
        _tp("<engram-context>\n[近期对话]\nx\n</engram-context>"),  # engram's own tag
        _tp("<RAG-Faiss-Memory>m</RAG-Faiss-Memory>"),
        _tp(None),
        _tp("<xml-extra>\nunclosed"),
        _tp("hello"),
    ]
    removed = strip_injected_blocks(parts, root_tag="xml-extra", inner_labels=("memo-block",))
    assert removed == 0 and len(parts) == 5
    assert strip_injected_blocks([], root_tag="xml-extra") == 0
    assert strip_injected_blocks(None, root_tag="xml-extra") == 0
    assert strip_injected_blocks([_tp(MEMO_BLOCK)], root_tag="") == 0
    print("  no false positives; empty/None/empty-tag safe: OK")


def test_root_tag_normalisation():
    banner("strip_injected_blocks: root_tag with/without angle brackets")
    for form in ("xml-extra", "<xml-extra>", "</xml-extra>"):
        parts = [_tp(MEMO_BLOCK)]
        removed = strip_injected_blocks(parts, root_tag=form, inner_labels=("memo-block",))
        assert removed == 1 and not parts, form
    print("  all three root_tag forms accepted: OK")


def test_handler_delegates_and_strips_legacy():
    banner("InjectHandler._strip_prior_engram_blocks: delegation + legacy label")
    parts = [
        _tp("<engram-context>\n[今日回顾]\nold-v1.67-block\n</engram-context>"),
        _tp("<engram-context>\n[最近日记]\nold-diary\n</engram-context>"),
        _tp(MEMO_BLOCK),  # another plugin's block must survive
    ]
    removed = InjectHandler._strip_prior_engram_blocks(parts)
    assert removed == 2, removed
    assert len(parts) == 1 and parts[0].text == MEMO_BLOCK
    print("  legacy + current engram labels stripped via helper, xml-extra kept: OK")


def test_five_turn_coordination():
    banner("5-turn simulation: engram + xml-extra bounded, memo LAST")
    for position in ("before", "after"):
        cfg = _cfg(auto_inject_enabled=True, auto_inject_top_k=1, auto_inject_position=position)
        h = InjectHandler(_Svc(cfg))
        req = _Req(prompt="hello")
        for turn in range(5):
            asyncio.run(h.handle_inject(_FakeEvent("turn %d" % turn), req))
            _memo_plugin_round(req.extra_user_content_parts)
        parts = req.extra_user_content_parts
        engram_parts = [p for p in parts if (p.text or "").strip().startswith("<engram-context>")]
        memo_parts = [p for p in parts if (p.text or "").strip().startswith("<xml-extra")]
        assert len(engram_parts) == 1, (position, len(engram_parts))
        assert len(memo_parts) == 1, (position, len(memo_parts))
        assert len(parts) == 2, (position, len(parts))
        assert parts[-1].text == MEMO_BLOCK, position
        assert parts[0] is engram_parts[0], position
        print("  position=%s: engram=1, memo=1, memo last: OK" % position)


def test_auto_inject_disabled_memo_still_works():
    banner("auto_inject off: external memo injection unaffected")
    cfg = _cfg(auto_inject_enabled=False)
    h = InjectHandler(_Svc(cfg))
    req = _Req(prompt="hello")
    for turn in range(3):
        asyncio.run(h.handle_inject(_FakeEvent("turn %d" % turn), req))
        _memo_plugin_round(req.extra_user_content_parts)
    parts = req.extra_user_content_parts
    assert len(parts) == 1 and parts[0].text == MEMO_BLOCK
    print("  engram disabled -> exactly 1 memo block, no accumulation: OK")


if __name__ == "__main__":
    test_attr_bearing_root_tag()
    test_inner_labels_semantics()
    test_other_plugins_untouched()
    test_root_tag_normalisation()
    test_handler_delegates_and_strips_legacy()
    test_five_turn_coordination()
    test_auto_inject_disabled_memo_still_works()
    print("\nALL v1.73 cross-plugin coordination smoke tests passed.")