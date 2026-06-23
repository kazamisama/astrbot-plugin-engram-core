"""Smoke v1.20 (B-3 diary layer): DiaryStore cut-point/TTL/chunks,
DiaryWriter compression + logical-day cut, end-to-end store_diary,
chunk recall, and run_daily_diary. Uses astrbot stub (mirrors v41 header).
"""
import sys, os, tempfile, types, time


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
from hippocampus.diary_store import DiaryStore, DailyLine, DiaryChunk
from hippocampus.diary_writer import (
    DiaryWriter, target_length, resolve_cut, day_bounds, split_chunks)


def banner(m):
    print(chr(10) + "=== " + m + " ===")


def test_diarystore_cut_ttl():
    banner("DiaryStore: idle-gap cut, range query, TTL purge")
    tmp = tempfile.mkdtemp()
    ds = DiaryStore(os.path.join(tmp, "d.db"))
    base = 1_000_000.0
    # 3 messages then a 40min gap then 1 message
    for off in (0, 60, 120, 120 + 2400, 120 + 2400 + 60):
        ds.add_line(DailyLine(channel_id="c1", actor_id="A", content="hi",
                              ts=base + off))
    gap = ds.find_idle_gap("c1", base, base + 10000, min_gap_seconds=1800.0)
    assert gap is not None and abs(gap - (base + 120)) < 1e-6, gap
    rng = ds.lines_in_range("c1", base, base + 200)
    assert len(rng) == 3, len(rng)
    # TTL purge: drop everything older than base+200
    n = ds.purge_older_than(base + 200)
    assert n == 3, n
    assert ds.count_lines() == 2, ds.count_lines()
    ds.close()
    print("PASS cut/ttl")


def test_diarystore_chunks():
    banner("DiaryStore: chunk add / fetch / delete")
    tmp = tempfile.mkdtemp()
    ds = DiaryStore(os.path.join(tmp, "d.db"))
    chunks = [DiaryChunk(diary_id="dia1", channel_id="c1", seq=i,
                         text="t" + str(i), embedding=[0.1 * i],
                         ts_start=i, ts_end=i + 1) for i in range(3)]
    assert ds.add_chunks(chunks) == 3
    got = ds.chunks_for_diary("dia1")
    assert len(got) == 3 and got[0].seq == 0, [c.seq for c in got]
    assert ds.delete_chunks_for_diary("dia1") == 3
    assert ds.count_chunks() == 0
    ds.close()
    print("PASS chunks")


def test_compression_and_split():
    banner("DiaryWriter: per-person compression + chunk split + cut degrade")
    # private (1 participant): total*0.025
    assert target_length(10000, 0.025, 1, floor=50, cap=2500) == 250
    # group 5 participants: total*0.025/5, but floor 50 kicks in
    assert target_length(2000, 0.025, 5, floor=50, cap=2500) == 50
    # cap clamps
    assert target_length(1_000_000, 0.025, 1, floor=50, cap=2500) == 2500
    # split_chunks: 2 paragraphs -> 2 chunks with proportional ts
    text = "para one here\n\npara two here"
    pieces = split_chunks(text, 100.0, 200.0, max_chars=400)
    assert len(pieces) == 2, len(pieces)
    assert pieces[0][2] == 100.0 and abs(pieces[1][3] - 200.0) < 1e-6
    print("PASS compression/split")


def test_resolve_cut_degrade():
    banner("DiaryWriter: resolve_cut degrades to 00:00 when no night gap")
    tmp = tempfile.mkdtemp()
    ds = DiaryStore(os.path.join(tmp, "d.db"))
    # a day with continuous early-morning chatter (no 30min gap) -> degrade
    base, _ = day_bounds(1_700_000_000.0)
    for off in range(0, 3600, 300):  # every 5min in first hour
        ds.add_line(DailyLine(channel_id="c1", actor_id="A", content="x",
                              ts=base + off))
    cut = resolve_cut(ds, "c1", base, night_hours=6.0, min_gap_seconds=1800.0)
    assert abs(cut - base) < 1e-6, (cut, base)  # degraded to 00:00
    ds.close()
    print("PASS cut degrade")


class _JsonLLM(LLMProvider):
    def name(self): return "json"
    def chat(self, system, user, **kw):
        return ('{"summary":"\u6211\u4eca\u5929\u548c A \u804a\u4e86 tea\u3002",'
                '"key_facts":["A \u559c\u6b22 tea"],"topics":["tea"],'
                '"participants":["A"]}')


def _svc(tmp):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.tiering_enabled = False
    cfg.diary_enabled = True
    svc = MemoryService(cfg=cfg)
    svc.register_llm("json", _JsonLLM())
    svc.set_llm("json")
    return svc


def test_store_diary_and_chunk_recall():
    banner("store_diary -> diary engram (memory_type=diary) + chunk recall")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    diary = {"summary": "\u6211\u4eca\u5929\u548c A \u804a\u4e86 tea\u3002",
             "key_facts": ["A \u559c\u6b22 tea"], "topics": ["tea"],
             "participants": ["A"], "_first_ts": 100.0, "_last_ts": 200.0}
    identity = {"actor_id": "A", "channel_id": "c1", "chat_type": "private",
                "peer_actor_id": "A", "peer_name": "A", "day_label": "2026-06-19"}
    e = svc.store_diary(diary, identity)
    assert e is not None and e.memory_type == "diary", getattr(e, "memory_type", None)
    assert "kind:diary" in e.tags and "day:2026-06-19" in e.tags, e.tags
    # chunk recall finds the tea chunk
    hits = svc.recall_diary_chunks("tea", top_n=1)
    assert hits and "tea" in hits[0][0], hits
    # layered recall: diary must NOT appear in episodic/semantic recall
    from hippocampus import Cue
    res = svc.recall(Cue(text="tea", memory_types=["episodic", "semantic"], k=5))
    assert all(getattr(x, "memory_type", "") != "diary" for x in res.engrams)
    svc.close()
    print("PASS store_diary/chunk recall/layering")


def test_run_daily_diary_end_to_end():
    banner("run_daily_diary: cache yesterday -> one diary written + TTL purge")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    now = time.time()
    today0, _ = day_bounds(now)
    y0 = today0 - 86400.0  # yesterday 00:00
    # cache a handful of yesterday lines (incl bot) at midday
    midday = y0 + 12 * 3600.0
    for i, (aid, isbot, txt) in enumerate([
            ("A", False, "\u4f60\u597d"), ("bot", True, "\u4f60\u597d\u5440"),
            ("A", False, "\u4eca\u5929\u5929\u6c14\u4e0d\u9519")]):
        svc.cache_daily_line({"channel_id": "c1", "chat_type": "private",
                              "actor_id": aid, "speaker": aid, "content": txt,
                              "is_bot": isbot, "peer_actor_id": "A",
                              "peer_name": "A", "ts": midday + i})
    # NOTE: cache_daily_line stamps ts=now; override by inserting directly
    # to land them in yesterday. Re-do via diary_store with explicit ts.
    from hippocampus.diary_store import DailyLine
    svc.diary_store.purge_older_than(now + 1)  # clear the now-stamped ones
    for i, (aid, isbot, txt) in enumerate([
            ("A", False, "hi"), ("bot", True, "hello"),
            ("A", False, "nice day")]):
        svc.diary_store.add_line(DailyLine(channel_id="c1", chat_type="private",
                                           actor_id=aid, speaker=aid, content=txt,
                                           is_bot=isbot, peer_actor_id="A",
                                           peer_name="A", ts=midday + i))
    res = svc.run_daily_diary(now=now)
    # FIX (v1.41): run_daily_diary returns (written, failed)
    n, failed = res if isinstance(res, tuple) else (int(res or 0), [])
    assert n == 1, (n, failed)
    assert failed == [], failed
    # the diary engram exists
    diaries = [x for x in svc.store.all(limit=10000)
               if getattr(x, "memory_type", "") == "diary"]
    assert len(diaries) == 1, len(diaries)
    svc.close()
    print("PASS run_daily_diary e2e")


if __name__ == "__main__":
    test_diarystore_cut_ttl()
    test_diarystore_chunks()
    test_compression_and_split()
    test_resolve_cut_degrade()
    test_store_diary_and_chunk_recall()
    test_run_daily_diary_end_to_end()
    print(chr(10) + "ALL v43 PASS")
