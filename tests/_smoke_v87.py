"""Smoke v1.76.21 injection-content + label batch.

Two defects in the same render loop of InjectHandler (_handle_inject_sync):

  D1  Only ``engram.summary`` was injected, never ``engram.content``. Every
      stored engram's ``content`` is its summary followed by the summarizer's
      "- key fact" bullet lines (true for 399/399 rows when this was written),
      so the narrative went into the prompt and every extracted key fact was
      silently dropped at inject time.

  D2  The block label was ``[近期对话]`` ("recent conversation") while every
      entry carries its own real relative-time marker ("[3个月前]", "[2 天前]")
      on the very same line. The label contradicted its own payload. Renamed
      to ``[长期记忆]``.

Also asserted here:
  - the legacy ``[近期对话]`` label is still in _ENGRAM_INNER_LABELS, so
    blocks injected by <= v1.76.20 are still stripped by the re-injection
    defence (otherwise old blocks would accumulate forever);
  - ``auto_inject_use_content=False`` restores summary-only injection;
  - the character cap truncates on WHOLE LINES (never mid-bullet) and always
    keeps the leading summary line even when it alone exceeds the cap;
  - an engram-like object without a ``content`` attribute still works.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- shim astrbot so handlers.format's `from astrbot.api.event import
# AstrMessageEvent` resolves outside a running AstrBot ---
_astrbot = types.ModuleType("astrbot")
_api = types.ModuleType("astrbot.api")
_event = types.ModuleType("astrbot.api.event")


class AstrMessageEvent:  # typing only; hooks use plain attributes
    pass


_event.AstrMessageEvent = AstrMessageEvent
_api.event = _event
_astrbot.api = _api
sys.modules.setdefault("astrbot", _astrbot)
sys.modules.setdefault("astrbot.api", _api)
sys.modules.setdefault("astrbot.api.event", _event)

FAILURES = []


def banner(msg):
    print("\n=== " + msg + " ===")


def check(cond, msg):
    if cond:
        print("  OK   " + msg)
    else:
        print("  FAIL " + msg)
        FAILURES.append(msg)


def _cfg(**over):
    cfg = type("Cfg", (), {})()
    for k, v in dict(
        auto_inject_enabled=True,
        auto_inject_top_k=3,
        auto_inject_position="before",
        auto_inject_relative_time=True,
        auto_inject_use_content=True,
        auto_inject_content_max_chars=800,
        persona_inject_enabled=False,
        relation_inject_top_n=0,
        diary_inject_top_n=0,
    ).items():
        setattr(cfg, k, v)
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class _Engram:
    def __init__(self, summary, content=None, created_at=0.0):
        self.summary = summary
        if content is not None:
            self.content = content
        self.created_at = created_at


class _Result:
    def __init__(self, engrams):
        self.engrams = engrams


class _Svc:
    def __init__(self, cfg, engrams):
        self.cfg = cfg
        self._engrams = engrams
        self.last_cue = None

    def recall(self, cue):
        self.last_cue = cue
        return _Result(self._engrams)


class _Evt:
    def __init__(self, text):
        self.message_str = text
        self.unified_msg_origin = "test:FriendMessage:1"
        self._text = text

    def get_sender_id(self):
        return "actor-1"

    def get_group_id(self):
        return ""

    def get_platform_name(self):
        return "test"


class _Req:
    def __init__(self, prompt=""):
        self.prompt = prompt
        self.extra_user_content_parts = []

    def __setattr__(self, k, v):
        object.__setattr__(self, k, v)


def _inject(cfg, engrams, prompt="随便说点什么"):
    from handlers.event.inject import InjectHandler

    h = InjectHandler(_Svc(cfg, engrams))
    req = _Req(prompt=prompt)
    asyncio.run(h.handle_inject(_Evt(prompt), req))
    return req


def _text_of(req):
    parts = getattr(req, "extra_user_content_parts", None) or []
    if parts:
        return "\n".join(getattr(p, "text", "") or "" for p in parts)
    return req.prompt or ""


# --------------------------------------------------------------------------
def test_content_is_injected_not_just_summary():
    banner("D1: key facts from content reach the prompt")
    summ = "今天我们聊了记忆插件。"
    body = summ + "\n- 要点一：注入只用了 summary\n- 要点二：要点全被丢掉"
    req = _inject(_cfg(), [_Engram(summ, content=body)])
    txt = _text_of(req)
    check(summ in txt, "summary is present")
    check("要点一" in txt and "要点二" in txt, "both key-fact bullets are present")
    check("[长期记忆]" in txt, "block carries the [长期记忆] label")


def test_bad_cap_value_does_not_kill_injection():
    banner("robustness: a malformed cap must degrade, not abandon injection")
    summ = "摘要仍要注入。"
    body = summ + "\n- 要点也在"
    for bad in (None, "abc", "", -5):
        req = _inject(_cfg(auto_inject_content_max_chars=bad), [_Engram(summ, content=body)])
        txt = _text_of(req)
        check(summ in txt and "要点也在" in txt,
              "cap=%r still injects the full body" % (bad,))


def test_no_double_brackets():
    banner("render: label is emitted exactly once-bracketed")
    req = _inject(_cfg(), [_Engram("摘要。", content="摘要。\n- 要点")])
    txt = _text_of(req)
    check("[[长期记忆]]" not in txt, "no double-bracketed label: %r" % txt)
    check("\n[长期记忆]\n" in txt, "label sits alone on its own line")


def test_falls_back_to_summary_when_content_missing():
    banner("D1: engram without a content attribute still injects")
    summ = "只有摘要也能注入。"
    req = _inject(_cfg(), [_Engram(summ)])
    txt = _text_of(req)
    check(summ in txt, "summary present via fallback")
    check("[长期记忆]" in txt, "block still labelled")


def test_use_content_false_restores_summary_only():
    banner("config: auto_inject_use_content=False -> summary only")
    summ = "摘要在这里。"
    body = summ + "\n- 不该出现的要点"
    req = _inject(_cfg(auto_inject_use_content=False), [_Engram(summ, content=body)])
    txt = _text_of(req)
    check(summ in txt, "summary present")
    check("不该出现的要点" not in txt, "key facts suppressed when the switch is off")


def test_cap_truncates_on_line_boundaries():
    banner("cap: truncation keeps whole lines, never a half bullet")
    summ = "第一行摘要。"
    lines = ["- 要点编号 %02d 内容内容内容" % i for i in range(1, 11)]
    body = "\n".join([summ] + lines)
    # budget: summary (7 chars) + a few bullets only
    req = _inject(_cfg(auto_inject_content_max_chars=60), [_Engram(summ, content=body)])
    txt = _text_of(req)
    check(summ in txt, "summary kept")
    check("要点编号 01" in txt, "first bullet kept")
    check("要点编号 10" not in txt, "later bullets dropped")
    # every emitted bullet line must be complete (ends with the full tail)
    kept = [ln for ln in txt.splitlines() if "要点编号" in ln]
    check(all("内容内容内容" in ln for ln in kept),
          "no bullet was cut mid-line: %r" % (kept,))
    print("  kept %d of 10 bullets under a 60-char cap" % len(kept))


def test_cap_never_drops_the_summary_line():
    banner("cap: an over-long summary line is still kept whole")
    summ = "很长很长的摘要" * 12  # 84 chars, over the 20-char cap
    body = summ + "\n- 要点A"
    req = _inject(_cfg(auto_inject_content_max_chars=20), [_Engram(summ, content=body)])
    txt = _text_of(req)
    check(summ in txt, "summary kept even though it alone exceeds the cap")
    check("要点A" not in txt, "bullets dropped once the budget is spent")


def test_cap_zero_means_no_cap():
    banner("cap: 0 disables the cap")
    summ = "摘要。"
    body = "\n".join([summ] + ["- 要点%02d" % i for i in range(1, 21)])
    req = _inject(_cfg(auto_inject_content_max_chars=0), [_Engram(summ, content=body)])
    txt = _text_of(req)
    check("要点20" in txt, "nothing truncated when cap is 0")


def test_multiline_bodies_are_indented():
    banner("render: continuation lines indented so they cannot read as new entries")
    summ = "摘要行。"
    body = summ + "\n- 要点甲"
    req = _inject(_cfg(), [_Engram(summ, content=body)])
    txt = _text_of(req)
    check("\n  - 要点甲" in txt, "bullet indented under its entry: %r" % txt)


def test_legacy_label_still_stripped():
    banner("D2: blocks labelled [近期对话] (<= v1.76.20) are still stripped")
    from handlers.event.inject import InjectHandler

    parts = []
    try:
        from astrbot.core.agent.message import TextPart
        parts = [
            TextPart(text="<engram-context>\n[近期对话]\nold\n</engram-context>", type="text"),
            TextPart(text="<engram-context>\n[长期记忆]\nnew\n</engram-context>", type="text"),
        ]
    except ImportError:
        class _TP:
            def __init__(self, text):
                self.text = text

        parts = [
            _TP("<engram-context>\n[近期对话]\nold\n</engram-context>"),
            _TP("<engram-context>\n[长期记忆]\nnew\n</engram-context>"),
        ]
    removed = InjectHandler._strip_prior_engram_blocks(parts)
    check(removed == 2, "both legacy and new engram blocks stripped (removed=%d)" % removed)
    check(parts == [], "nothing left")


def test_labels_tuple_has_both():
    banner("labels: new + legacy both registered")
    from handlers.event.inject import InjectHandler
    labels = InjectHandler._ENGRAM_INNER_LABELS
    check("[长期记忆]" in labels, "new label registered")
    check("[近期对话]" in labels, "legacy label retained for stripping")
    check(labels.count("[长期记忆]") == 1, "no duplicate new label")


def test_config_fields_registered():
    banner("config: both new fields exist in MemoryConfig + FieldSpec table")
    from hippocampus.config import MemoryConfig
    from hippocampus.config_manager import _FIELDS

    cfg = MemoryConfig()
    check(cfg.auto_inject_use_content is True, "MemoryConfig.auto_inject_use_content default True")
    check(cfg.auto_inject_content_max_chars == 800, "MemoryConfig cap default 800")
    for f in ("auto_inject_use_content", "auto_inject_content_max_chars"):
        check(f in _FIELDS, "%s in _FIELDS" % f)


def test_schema_json_has_both():
    banner("config: _conf_schema.json exposes both options")
    import json
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "_conf_schema.json")
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    check("auto_inject_use_content" in raw, "use_content in schema")
    check("auto_inject_content_max_chars" in raw, "cap in schema")
    # both must sit in the same nested group as their sibling, i.e. JSON-valid
    blob = json.loads(raw)
    found = []

    def walk(node):
        for k, v in node.items():
            if isinstance(v, dict) and ("type" in v and "default" in v):
                found.append(k)
            elif isinstance(v, dict):
                walk(v)

    walk(blob)
    check("auto_inject_use_content" in found, "use_content parses as a leaf option")
    check("auto_inject_content_max_chars" in found, "cap parses as a leaf option")
    check("auto_inject_relative_time" in found, "sibling still parses (grouping intact)")


if __name__ == "__main__":
    test_content_is_injected_not_just_summary()
    test_bad_cap_value_does_not_kill_injection()
    test_no_double_brackets()
    test_falls_back_to_summary_when_content_missing()
    test_use_content_false_restores_summary_only()
    test_cap_truncates_on_line_boundaries()
    test_cap_never_drops_the_summary_line()
    test_cap_zero_means_no_cap()
    test_multiline_bodies_are_indented()
    test_legacy_label_still_stripped()
    test_labels_tuple_has_both()
    test_config_fields_registered()
    test_schema_json_has_both()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.21 checks passed")
