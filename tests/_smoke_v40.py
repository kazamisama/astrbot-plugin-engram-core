"""Smoke v1.17 (B-1): ConversationBuffer + ConversationSummarizer pure logic.

No AstrBot, no DB. Verifies:
  - per-channel interleaving (everyone + bot in one time-ordered buffer)
  - chat-type aware idle flush (private vs group thresholds)
  - identity stamps survive to the flushed record
  - proportional compression target (ratio + cap + floor)
  - LLM-path structured parse (summary/key_facts/relations w/ confidence)
  - no-LLM fallback still yields a summary
  - 9 config fields registered with defaults
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS
from hippocampus.conversation_buffer import ConversationBuffer, ConversationRecord
from hippocampus.summarizer import ConversationSummarizer, target_length
from hippocampus.llm import LLMProvider, RuleLLMProvider


def banner(m):
    print(chr(10) + "=== " + m + " ===")


class _Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def tick(self, dt): self.t += dt


def test_config_fields():
    banner("9 B-1 config fields + defaults")
    for f in ("summary_mode_enabled", "per_message_ingest_debug",
              "summary_idle_seconds_private", "summary_idle_seconds_group",
              "summary_max_messages", "summary_min_chars",
              "summary_compress_ratio", "summary_compress_floor",
              "summary_compress_cap", "summary_compress_cap_group"):
        assert f in _FIELDS, f
        assert f in LABELS, f
    cfg = ConfigManager({}).memory_config
    assert cfg.summary_mode_enabled is True
    assert cfg.per_message_ingest_debug is False
    assert cfg.summary_idle_seconds_private == 1800.0
    assert cfg.summary_idle_seconds_group == 120.0
    assert cfg.summary_max_messages == 30
    assert cfg.summary_compress_ratio == 0.15
    assert cfg.summary_compress_cap == 1200
    assert cfg.summary_compress_cap_group == 400
    print("  fields + defaults OK")


def test_per_channel_interleave():
    banner("per-channel interleave incl. bot, time-ordered")
    clk = _Clock()
    flushed = []
    cfg = MemoryConfig()
    buf = ConversationBuffer(cfg, lambda rec: flushed.append(rec), now_fn=clk)
    base = {"channel_id": "g1", "chat_type": "group", "group_id": "111",
            "group_name": "\u706b\u9505\u7fa4", "platform": "qq", "session_id": "s1"}
    buf.feed(dict(base, actor_id="A", speaker="\u5c0f\u660e", content="\u4eca\u665a\u5403\u706b\u9505\u5417"))
    clk.tick(1)
    buf.feed(dict(base, actor_id="B", speaker="\u5c0f\u7ea2", content="\u884c\u554a"))
    clk.tick(1)
    buf.feed(dict(base, actor_id="bot", speaker="\u673a\u5668\u4eba", content="\u6211\u5e2e\u4f60\u4eec\u8ba2\u4f4d", is_bot=True))
    buf.flush_all()
    assert len(flushed) == 1
    rec = flushed[0]
    assert [ln.actor_id for ln in rec.lines] == ["A", "B", "bot"], "must interleave in order"
    assert rec.lines[2].is_bot
    # participants exclude bot by default
    assert "bot" not in rec.participants(include_bot=False)
    assert "bot" in rec.participants(include_bot=True)
    assert rec.group_name == "\u706b\u9505\u7fa4" and rec.group_id == "111"
    print("  interleave + bot + identity stamps OK")


def test_idle_flush_chat_type():
    banner("idle flush uses private/group thresholds")
    clk = _Clock()
    flushed = []
    cfg = MemoryConfig()
    cfg.summary_idle_seconds_group = 600.0
    cfg.summary_idle_seconds_private = 1800.0
    buf = ConversationBuffer(cfg, lambda rec: flushed.append(rec), now_fn=clk)
    # group channel: idle 600 -> flush after 600s gap on next feed of another channel
    buf.feed({"channel_id": "g1", "chat_type": "group", "actor_id": "A", "content": "hi"})
    clk.tick(601)
    # feed a different channel to trigger idle settle of g1
    buf.feed({"channel_id": "g2", "chat_type": "group", "actor_id": "C", "content": "yo"})
    assert len(flushed) == 1 and flushed[0].channel_id == "g1", "group idle should flush g1"
    # private channel: 601s gap is NOT idle (threshold 1800)
    flushed.clear()
    buf2 = ConversationBuffer(cfg, lambda rec: flushed.append(rec), now_fn=clk)
    buf2.feed({"channel_id": "p1", "chat_type": "private", "actor_id": "U", "content": "hi"})
    clk.tick(601)
    buf2.flush_idle_now()
    assert len(flushed) == 0, "private 601s < 1800 not idle yet"
    clk.tick(1300)
    buf2.flush_idle_now()
    assert len(flushed) == 1, "private idle after 1901s"
    print("  chat-type idle thresholds OK")


def test_target_length():
    banner("proportional compression target")
    assert target_length(1000, 0.15) == 150
    assert target_length(1000, 0.15, cap=100) == 100
    assert target_length(100, 0.15, floor=50) == 50
    assert target_length(0, 0.15) == 0
    print("  ratio/cap/floor OK")


class _JsonLLM(LLMProvider):
    def name(self): return "json"
    def chat(self, system, user, **kw):
        return ('{"summary":"\u5927\u5bb6\u7ea6\u4eca\u665a\u5403\u706b\u9505",'
                '"key_facts":["\u4eca\u665a\u5403\u706b\u9505"],'
                '"topics":["\u996e\u98df"],'
                '"participants":["\u5c0f\u660e","\u5c0f\u7ea2"],'
                '"relations":[{"subject":"\u5c0f\u660e","relation":"\u7ea6","object":"\u5c0f\u7ea2","confidence":0.8}]}')


def _mk_record():
    clk = _Clock()
    flushed = []
    cfg = MemoryConfig()
    buf = ConversationBuffer(cfg, lambda rec: flushed.append(rec), now_fn=clk)
    base = {"channel_id": "g1", "chat_type": "group", "group_id": "111",
            "group_name": "\u706b\u9505\u7fa4"}
    buf.feed(dict(base, actor_id="A", speaker="\u5c0f\u660e", content="\u4eca\u665a\u5403\u706b\u9505\u5417"))
    clk.tick(1)
    buf.feed(dict(base, actor_id="B", speaker="\u5c0f\u7ea2", content="\u884c\u554a\u4e00\u8d77\u53bb"))
    buf.flush_all()
    return flushed[0], cfg


def test_llm_structured():
    banner("LLM path: structured parse w/ relation confidence")
    rec, cfg = _mk_record()
    s = ConversationSummarizer(cfg, llm=_JsonLLM())
    out = s.summarize(rec)
    assert out["summary"], "must have summary"
    assert out["relations"] and out["relations"][0]["confidence"] == 0.8
    assert out["relations"][0]["subject"] == "\u5c0f\u660e"
    assert "_target_chars" in out
    print("  structured summary + relation confidence OK")


def test_persona_prefill():
    banner("persona prefill is applied to system prompt")
    rec, cfg = _mk_record()
    captured = {}
    class _CapLLM(LLMProvider):
        def name(self): return "cap"
        def chat(self, system, user, **kw):
            captured["system"] = system
            return '{"summary":"ok"}'
    s = ConversationSummarizer(cfg, llm=_CapLLM(),
                               persona_provider=lambda r: "\u4eba\u683c\uff1a\u4fa6\u63a2\u8bed\u6c14")
    s.summarize(rec)
    assert "\u4fa6\u63a2\u8bed\u6c14" in captured["system"], "persona must be in system prompt"
    print("  persona prefill OK")


def test_fallback_no_llm():
    banner("no-LLM fallback still yields a summary")
    rec, cfg = _mk_record()
    s = ConversationSummarizer(cfg, llm=RuleLLMProvider())
    out = s.summarize(rec)
    assert out["summary"], "fallback must produce summary text"
    print("  fallback summary OK")


def main():
    test_config_fields()
    test_per_channel_interleave()
    test_idle_flush_chat_type()
    test_target_length()
    test_llm_structured()
    test_persona_prefill()
    test_fallback_no_llm()
    print(chr(10) + "v1.17 B-1 smoke: ALL PASS")


if __name__ == "__main__":
    main()
