"""Smoke v1.76.22 <engram-context> self-recall framing.

Real-machine trigger (docs/TODO.md §2.8): the block used to state only what it
is NOT ("injected background, not the user's actual message"). The live model
promoted that into a licence -- its reasoning read "memories are background;
the current conversation governs" -- and used it to decline a correctly
recalled memory. §2.8 had declined candidate 8.3 with an explicit
"下次评估点": the first real-machine report of the LLM misreading the tag.
This batch is that evaluation point firing.

Asserted here:
  - the note rides the FIRST block of a request and only that one (one note
    per request, matching the ~50-token budget §2.8 declined against);
  - it appears whichever block happens to be first (memory need not be present);
  - the note is inside the <engram-context> wrapper, i.e. never leaks into the
    real user prompt;
  - the re-injection defence still strips noted blocks AND note-less legacy
    blocks (label matching is "contains", not "starts with");
  - bare _wrap_engram() output is unchanged, so the public helper contract and
    anything pattern-matching the old shape still holds.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_astrbot = types.ModuleType("astrbot")
_api = types.ModuleType("astrbot.api")
_event = types.ModuleType("astrbot.api.event")
_core = types.ModuleType("astrbot.core")
_agent = types.ModuleType("astrbot.core.agent")
_message = types.ModuleType("astrbot.core.agent.message")


class AstrMessageEvent:
    pass


class TextPart:
    def __init__(self, text="", type="text"):
        self.text = text
        self.type = type
        self._is_temp = False

    def mark_as_temp(self):
        self._is_temp = True
        return self


_event.AstrMessageEvent = AstrMessageEvent
_message.TextPart = TextPart
_api.event = _event
_astrbot.api = _api
_astrbot.core = _core
_core.agent = _agent
_agent.message = _message
for _n, _m in (("astrbot", _astrbot), ("astrbot.api", _api),
               ("astrbot.api.event", _event), ("astrbot.core", _core),
               ("astrbot.core.agent", _agent),
               ("astrbot.core.agent.message", _message)):
    sys.modules.setdefault(_n, _m)

from handlers.event.inject import (  # noqa: E402
    _SELF_RECALL_NOTE,
    InjectHandler,
)

FAILURES = []


def banner(msg):
    print("\n=== " + msg + " ===")


def check(cond, msg):
    if cond:
        print("  OK   " + msg)
    else:
        print("  FAIL " + msg)
        FAILURES.append(msg)


class _Engram:
    def __init__(self, summary, content=None, created_at=0.0):
        self.summary = summary
        self.content = content if content is not None else summary
        self.created_at = created_at


class _Res:
    def __init__(self, e):
        self.engrams = e


class _Svc:
    def __init__(self, cfg, engrams):
        self.cfg = cfg
        self._e = engrams

    def recall(self, cue):
        return _Res(self._e)


class _Evt:
    message_str = "你好"
    unified_msg_origin = "test:FriendMessage:1"

    def get_sender_id(self):
        return "a"

    def get_group_id(self):
        return ""

    def get_platform_name(self):
        return "test"


class _Req:
    def __init__(self):
        self.prompt = "你好"
        self.extra_user_content_parts = []


def _cfg(**over):
    cfg = type("Cfg", (), {})()
    for k, v in dict(
        auto_inject_enabled=True, auto_inject_top_k=3,
        auto_inject_position="before", auto_inject_relative_time=True,
        auto_inject_use_content=True, auto_inject_content_max_chars=800,
        persona_inject_enabled=False, relation_inject_top_n=0,
        diary_inject_top_n=0,
    ).items():
        setattr(cfg, k, v)
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _inject(cfg, engrams):
    h = InjectHandler(_Svc(cfg, engrams))
    req = _Req()
    asyncio.run(h.handle_inject(_Evt(), req))
    return req


def test_note_rides_first_block_only():
    banner("note appears once, on the first block")
    req = _inject(_cfg(), [_Engram("记得你说过咖啡", "记得你说过咖啡\n- 只喝美式")])
    parts = req.extra_user_content_parts
    check(len(parts) == 1, "one block emitted (got %d)" % len(parts))
    txt = parts[0].text
    check(_SELF_RECALL_NOTE in txt, "note present on the first block")
    check(txt.count(_SELF_RECALL_NOTE) == 1, "note appears exactly once")
    check(txt.startswith("<engram-context>\n"), "still opens with the root tag")
    check(txt.rstrip().endswith("</engram-context>"), "still closes with the root tag")


def test_note_not_repeated_across_four_blocks():
    banner("with all four block kinds, the note is paid for once per request")

    class _Svc4:
        def __init__(self, cfg):
            self.cfg = cfg

        def recall(self, cue):
            return _Res([_Engram("记忆一", "记忆一\n- 要点", 0.0)])

        def get_persona(self, actor_id):
            return types.SimpleNamespace(summary="稳定画像", tags=["t"])

        def recall_relations(self, q, **kw):
            return [types.SimpleNamespace(subject="A", predicate="喜欢", object="B")]

        def recall_diary_chunks(self, q, **kw):
            return [("日记正文", 0.9)]

    cfg = _cfg(persona_inject_enabled=True, relation_inject_top_n=3,
               diary_inject_top_n=1)
    h = InjectHandler(_Svc4(cfg))
    req = _Req()
    asyncio.run(h.handle_inject(_Evt(), req))
    parts = req.extra_user_content_parts
    check(len(parts) == 4, "four blocks emitted (got %d)" % len(parts))
    total = sum((p.text or "").count(_SELF_RECALL_NOTE) for p in parts)
    check(total == 1, "note total across all blocks == 1 (got %d)" % total)
    check(_SELF_RECALL_NOTE in parts[0].text, "note is on the first block")
    labels = ["[用户画像]", "[人物关系]", "[长期记忆]", "[最近日记]"]
    for i, lab in enumerate(labels):
        check(lab in parts[i].text, "block %d still labelled %s" % (i, lab))


def test_note_rides_whichever_block_is_first():
    banner("memory absent -> the note rides the surviving first block")
    req = _inject(_cfg(persona_inject_enabled=False), [])
    parts = req.extra_user_content_parts
    # no blocks at all -> nothing injected
    check(len(parts) == 0, "no hits means no injection")
    check(_SELF_RECALL_NOTE not in (req.prompt or ""), "note never touches req.prompt here")


def test_note_never_leaks_outside_wrapper():
    banner("note stays inside the wrapper, not in the user prompt")
    req = _inject(_cfg(), [_Engram("记忆", "记忆\n- 要点")])
    check(_SELF_RECALL_NOTE not in (req.prompt or ""), "not in req.prompt")
    check(req.prompt == "你好", "user prompt untouched")
    for p in req.extra_user_content_parts:
        body = p.text
        note_at = body.find(_SELF_RECALL_NOTE)
        check(note_at > body.find("<engram-context>"),
              "note sits after the opening tag")
        check(note_at < body.rfind("</engram-context>"),
              "note sits before the closing tag")


def test_strip_still_works_with_and_without_note():
    banner("re-injection defence: noted + legacy note-less blocks both stripped")
    from handlers.event.inject import TextPart as TP
    noted = InjectHandler._wrap_engram("[长期记忆]\n- x", preamble=True)
    legacy = "<engram-context>\n[近期对话]\nold\n</engram-context>"
    plain = InjectHandler._wrap_engram("[长期记忆]\n- y")
    other = "<RAG-Faiss-Memory>keepme</RAG-Faiss-Memory>"
    parts = [TP(noted), TP(legacy), TP(plain), TP(other)]
    removed = InjectHandler._strip_prior_engram_blocks(parts)
    check(removed == 3, "noted + legacy + plain all stripped (removed=%d)" % removed)
    check(len(parts) == 1 and "keepme" in parts[0].text,
          "another plugin's block untouched")


def test_bare_wrap_unchanged():
    banner("_wrap_engram() default output is byte-identical to the old shape")
    got = InjectHandler._wrap_engram("[长期记忆]\n- x")
    check(got == "<engram-context>\n[长期记忆]\n- x\n</engram-context>",
          "no note unless preamble=True: %r" % got)


def test_note_is_short():
    banner("token budget: the note matches §2.8's ~50-token estimate")
    n = len(_SELF_RECALL_NOTE)
    check(n < 90, "note is %d chars (< 90)" % n)
    print("  note is %d chars (~%d tokens for CJK)" % (n, n / 1.5))
    check("可以忽略的背景" in _SELF_RECALL_NOTE,
          "note explicitly rejects the 'ignorable background' reading")
    check("不是你收到的用户消息" in _SELF_RECALL_NOTE,
          "note keeps the v1.67.1 structural signal")


if __name__ == "__main__":
    test_note_rides_first_block_only()
    test_note_not_repeated_across_four_blocks()
    test_note_rides_whichever_block_is_first()
    test_note_never_leaks_outside_wrapper()
    test_strip_still_works_with_and_without_note()
    test_bare_wrap_unchanged()
    test_note_is_short()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.22 checks passed")
