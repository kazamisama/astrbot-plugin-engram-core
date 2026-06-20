"""Smoke v1.6: SessionAggregator per-speaker conversation buffering.

Covers hippocampus.session_buffer.SessionAggregator with an injected
clock + a recording sink:
  - size cap -> flush merges N lines into ONE observation
  - two speakers in one channel never share a buffer (no cross-speaker merge)
  - idle timeout -> a speaker's stale burst is flushed on the next message
  - quality gate drops empty / too-short / exact-duplicate lines
  - flush_all() drains remaining buffers
Also asserts the 4 new config fields exist with correct defaults.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS
from hippocampus.session_buffer import SessionAggregator


def banner(m):
    print("\n=== " + m + " ===")


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t
    def __call__(self):
        return self.t
    def tick(self, d):
        self.t += d


def _cfg(**over):
    c = MemoryConfig()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _meta(actor, content, channel="g1"):
    return {"session_id": "s", "actor_id": actor, "platform": "test",
            "channel_id": channel, "content": content}


def _mk(cfg, clock):
    sink_calls = []
    agg = SessionAggregator(cfg, lambda m: sink_calls.append(m), now_fn=clock)
    return agg, sink_calls


def test_fields_defaults():
    banner("4 session-agg fields registered + defaults")
    for f in ("session_aggregate_enabled", "session_aggregate_max_messages",
              "session_aggregate_idle_seconds", "session_aggregate_min_chars"):
        assert f in _FIELDS, f
        assert f in LABELS, f
    cfg = ConfigManager({}).memory_config
    assert cfg.session_aggregate_enabled is True
    assert cfg.session_aggregate_max_messages == 5
    assert cfg.session_aggregate_idle_seconds == 8.0
    assert cfg.session_aggregate_min_chars == 0
    print("  defaults: enabled / cap=5 / idle=8 / min=0 OK")


def test_size_cap_merges():
    banner("size cap -> one merged observation")
    clock = _Clock()
    cfg = _cfg(session_aggregate_max_messages=3, session_aggregate_idle_seconds=0)
    agg, calls = _mk(cfg, clock)
    agg.feed(_meta("A", "第一句"))
    agg.feed(_meta("A", "第二句"))
    assert calls == []  # not yet at cap
    agg.feed(_meta("A", "第三句"))
    assert len(calls) == 1, calls
    assert calls[0]["actor_id"] == "A"
    assert calls[0]["content"] == "第一句\n第二句\n第三句", calls[0]["content"]
    print("  3 lines merged into one observation: OK")


def test_two_speakers_no_merge():
    banner("two speakers in one channel -> separate buffers")
    clock = _Clock()
    cfg = _cfg(session_aggregate_max_messages=2, session_aggregate_idle_seconds=0)
    agg, calls = _mk(cfg, clock)
    agg.feed(_meta("A", "甲说一"))
    agg.feed(_meta("B", "乙说一"))
    agg.feed(_meta("A", "甲说二"))  # A hits cap=2 -> flush A only
    assert len(calls) == 1, calls
    assert calls[0]["actor_id"] == "A"
    assert calls[0]["content"] == "甲说一\n甲说二"
    agg.feed(_meta("B", "乙说二"))  # B hits cap=2 -> flush B
    assert len(calls) == 2
    assert calls[1]["actor_id"] == "B"
    assert calls[1]["content"] == "乙说一\n乙说二"
    print("  speakers buffered independently, no cross-merge: OK")


def test_idle_flush():
    banner("idle timeout -> stale burst flushed on next message")
    clock = _Clock()
    cfg = _cfg(session_aggregate_max_messages=10, session_aggregate_idle_seconds=60)
    agg, calls = _mk(cfg, clock)
    agg.feed(_meta("A", "早上好"))
    clock.tick(120)  # A goes idle
    agg.feed(_meta("B", "另一个人"))  # settles idle A buffer first
    assert len(calls) == 1, calls
    assert calls[0]["actor_id"] == "A"
    assert calls[0]["content"] == "早上好"
    print("  idle buffer flushed when another message arrives: OK")


def test_idle_same_speaker_new_burst():
    banner("idle then same speaker -> previous burst flushed, new one starts")
    clock = _Clock()
    cfg = _cfg(session_aggregate_max_messages=10, session_aggregate_idle_seconds=60)
    agg, calls = _mk(cfg, clock)
    agg.feed(_meta("A", "第一波"))
    clock.tick(120)
    agg.feed(_meta("A", "第二波"))  # same speaker, but stale -> flush first
    assert len(calls) == 1, calls
    assert calls[0]["content"] == "第一波"
    agg.flush_all()
    assert len(calls) == 2
    assert calls[1]["content"] == "第二波"
    print("  stale same-speaker burst flushed, new burst separate: OK")


def test_quality_gate():
    banner("quality gate drops empty / short / duplicate")
    clock = _Clock()
    cfg = _cfg(session_aggregate_max_messages=10, session_aggregate_idle_seconds=0,
               session_aggregate_min_chars=2)
    agg, calls = _mk(cfg, clock)
    agg.feed(_meta("A", "  "))        # empty after strip -> drop
    agg.feed(_meta("A", "x"))          # too short (<2) -> drop
    agg.feed(_meta("A", "你好"))       # ok
    agg.feed(_meta("A", "你好"))       # exact dup of last buffered -> drop
    agg.feed(_meta("A", "再见"))       # ok
    agg.flush_all()
    assert len(calls) == 1, calls
    assert calls[0]["content"] == "你好\n再见", calls[0]["content"]
    print("  empty/short/dup dropped, kept 2 distinct lines: OK")


def test_flush_all():
    banner("flush_all drains remaining buffers")
    clock = _Clock()
    cfg = _cfg(session_aggregate_max_messages=10, session_aggregate_idle_seconds=0)
    agg, calls = _mk(cfg, clock)
    agg.feed(_meta("A", "甲方", channel="g1"))
    agg.feed(_meta("B", "乙方", channel="g2"))
    assert calls == []
    agg.flush_all()
    assert len(calls) == 2
    actors = sorted(c["actor_id"] for c in calls)
    assert actors == ["A", "B"], actors
    print("  flush_all drained both buffers: OK")


if __name__ == "__main__":
    test_fields_defaults()
    test_size_cap_merges()
    test_two_speakers_no_merge()
    test_idle_flush()
    test_idle_same_speaker_new_burst()
    test_quality_gate()
    test_flush_all()
    print("\nALL v1.6 session-aggregate smoke tests passed.")