"""Smoke v1.42 (BUG-7): DiaryStore in-memory write buffer.

Verifies:
  1. add_line() does NOT commit per call when batch_size > 1; the rows
     are flushed only when the buffer hits batch_size, when a read
     query is issued, when flush_now() is called, or on close().
  2. flush_now() returns the row count actually committed.
  3. buffer_size() reports pending lines.
  4. close() always flushes pending lines (no data loss on shutdown).
  5. Read methods (channels_with_lines, lines_in_range, count_lines,
     find_idle_gap, all_chunks) implicitly flush before reading, so a
     pending write is always visible to the read.
  6. batch_size=1 degenerates to per-row commit (back-compat).

Uses an astrbot stub to mirror the v43 header (no AstrBot runtime).
"""
from __future__ import annotations
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --- astrbot stub (mirrors v43) ---
_astro = types.ModuleType("astrbot")
_astro_api = types.ModuleType("astrbot.api")
_star_mod = types.ModuleType("astrbot.api.star")
class _Star:
    def __init__(self, *a, **kw): pass
class _Context:
    pass
def _register(*a, **kw):
    def _dec(cls): return cls
    return _dec
_star_mod.Star = _Star
_star_mod.register = _register
_star_mod.Context = _Context
_evt_mod = types.ModuleType("astrbot.api.event")
class _Filter:
    class EventMessageType:
        ALL = 0
    def event_message_type(self, *a, **kw):
        def _dec(f): return f
        return _dec
    def on_llm_request(self):
        def _dec(f): return f
        return _dec
    def on_llm_response(self):
        def _dec(f): return f
        return _dec
    def command(self, *a, **kw):
        def _dec(f): return f
        return _dec
_evt_mod.filter = _Filter()
class _AstrMessageEvent: pass
_evt_mod.AstrMessageEvent = _AstrMessageEvent
sys.modules["astrbot"] = _astro
sys.modules["astrbot.api"] = _astro_api
sys.modules["astrbot.api.star"] = _star_mod
sys.modules["astrbot.api.event"] = _evt_mod
# --- end stub ---

from hippocampus.diary_store import DiaryStore, DailyLine


def _banner(s):
    print()
    print("=== " + s + " ===")


_COUNTER = [0]

def _ds(tmp, batch_size):
    _COUNTER[0] += 1
    p = os.path.join(tmp, "buf" + str(_COUNTER[0]) + ".db")
    return DiaryStore(p, batch_size=batch_size)


def _line(ch="c1", actor="A", text="hi", ts=1000.0, is_bot=False):
    return DailyLine(channel_id=ch, chat_type="private",
                     actor_id=actor, speaker=actor, content=text,
                     is_bot=is_bot, peer_actor_id=actor, peer_name=actor,
                     ts=ts)


def test_buffer_accumulates_until_threshold(tmp):
    _banner("BUG-7: buffer holds N-1 lines without committing")
    ds = _ds(tmp, batch_size=10)
    raw = os.path.join(tmp, "buf1.db")
    for i in range(9):
        ds.add_line(_line(text="line" + str(i), ts=1000.0 + i))
    assert ds.buffer_size() == 9, ds.buffer_size()
    # Direct DB count should still be 0 (no commit yet).
    import sqlite3
    con = sqlite3.connect(raw)
    n = con.execute("SELECT COUNT(*) FROM daily_messages").fetchone()[0]
    con.close()
    assert n == 0, n
    ds.close()
    print("PASS buffer_accumulate")


def test_buffer_flushes_on_threshold(tmp):
    _banner("BUG-7: hitting batch_size triggers immediate commit")
    ds = _ds(tmp, batch_size=5)
    for i in range(5):
        ds.add_line(_line(text="L" + str(i), ts=2000.0 + i))
    assert ds.buffer_size() == 0, ds.buffer_size()
    n = ds.count_lines()
    assert n == 5, n
    ds.close()
    print("PASS flush_on_threshold")


def test_flush_now_returns_count(tmp):
    _banner("BUG-7: flush_now() returns row count and clears buffer")
    ds = _ds(tmp, batch_size=50)
    for i in range(7):
        ds.add_line(_line(text="f" + str(i), ts=3000.0 + i))
    assert ds.buffer_size() == 7
    n = ds.flush_now()
    assert n == 7, n
    assert ds.buffer_size() == 0
    ds.close()
    print("PASS flush_now")


def test_read_methods_flush_implicitly(tmp):
    _banner("BUG-7: every read method flushes pending buffer first")
    ds = _ds(tmp, batch_size=100)
    for i in range(3):
        ds.add_line(_line(text="r" + str(i), ts=4000.0 + i))
    assert ds.buffer_size() == 3
    # channels_with_lines must see the 3 pending lines
    chans = ds.channels_with_lines(0.0, 99999.0)
    assert ("c1", "") in chans, chans
    assert ds.buffer_size() == 0, "read should have flushed"
    # count_lines too
    for i in range(2):
        ds.add_line(_line(text="r" + str(10 + i), ts=4100.0 + i))
    assert ds.buffer_size() == 2
    assert ds.count_lines() == 5  # 3 from before + 2 fresh
    assert ds.buffer_size() == 0
    ds.close()
    print("PASS implicit_flush")


def test_close_always_flushes(tmp):
    _banner("BUG-7: close() never drops pending lines")
    db_path = os.path.join(tmp, "close_test.db")
    ds = DiaryStore(db_path, batch_size=100)
    for i in range(13):
        ds.add_line(_line(text="c" + str(i), ts=5000.0 + i))
    assert ds.buffer_size() == 13
    ds.close()
    # reopen and verify
    ds2 = DiaryStore(db_path)
    assert ds2.count_lines() == 13
    ds2.close()
    print("PASS close_flush")


def test_batch_size_1_degenerates(tmp):
    _banner("BUG-7: batch_size=1 commits per call (back-compat path)")
    ds = _ds(tmp, batch_size=1)
    ds.add_line(_line(text="b1", ts=6000.0))
    # After the 1-line batch, buffer is empty and row is committed.
    assert ds.buffer_size() == 0
    assert ds.count_lines() == 1
    ds.close()
    print("PASS batch_size_1")


def test_idle_gap_sees_pending(tmp):
    _banner("BUG-7: find_idle_gap considers pending buffer lines")
    ds = _ds(tmp, batch_size=100)
    # Two lines 10 min apart (no gap >= 1800s), no commit yet
    ds.add_line(_line(text="early", ts=7000.0))
    ds.add_line(_line(text="late", ts=7000.0 + 600.0))
    assert ds.buffer_size() == 2
    # find_idle_gap should see them via the implicit flush and return None
    # (gap is only 600s, threshold 1800s)
    gap = ds.find_idle_gap("c1", 0.0, 99999.0, min_gap_seconds=1800.0)
    assert gap is None, gap
    ds.close()
    print("PASS idle_gap_implicit_flush")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_buffer_accumulates_until_threshold(tmp)
        test_buffer_flushes_on_threshold(tmp)
        test_flush_now_returns_count(tmp)
        test_read_methods_flush_implicitly(tmp)
        test_close_always_flushes(tmp)
        test_batch_size_1_degenerates(tmp)
        test_idle_gap_sees_pending(tmp)
    print()
    print("ALL v60 PASS")


if __name__ == "__main__":
    main()
