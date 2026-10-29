"""Smoke v0.9: DG pattern separation (merge / new) + kill switch.

v1.78.0: the "link" action and the recall-side cluster expansion were removed
together with Engram.similar_to -- under the default summary pipeline nothing
wrote those links, and no recall path consumed them. An observation that is
not similar enough to merge is now simply stored as a new engram.
"""
import os, tempfile, sys, types, asyncio, json

from _smoke_common import install_stub as _install_astrbot_stub

_install_astrbot_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main
from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.separation import PatternSeparator
from hippocampus.storage import _cos
from hippocampus.types import Engram
from main import (HippocampusStar, render_stats, find_and_forget,
                  format_graph)


def banner(t): print(chr(10) + "=== " + t + " ===")


def test_resolve_bands():
    banner("resolve: merge above threshold, new below it (no link band)")
    sep = PatternSeparator(MemoryConfig())
    anchor = Engram(content="anchor", embedding=[1.0, 0.0])
    same = Engram(content="same", embedding=[1.0, 0.0])
    mid = Engram(content="mid", embedding=[0.8, 0.6])   # cos 0.8, in the old link band
    other = Engram(content="other", embedding=[0.0, 1.0])
    assert sep.resolve(same, [anchor]) == ("merge", anchor)
    assert sep.resolve(mid, [anchor]) == ("new", None)
    assert sep.resolve(other, [anchor]) == ("new", None)
    assert sep.resolve(Engram(content="first"), []) == ("new", None)
    assert not hasattr(PatternSeparator, "apply_link")
    assert not hasattr(PatternSeparator, "expand_cluster")
    assert not hasattr(Engram(), "similar_to")
    assert not hasattr(Engram(), "cluster_id")
    assert not hasattr(Engram(), "profile_fact_id")
    print("  resolve bands + removed link API: OK")


def test_service_merge_and_new():
    banner("service: identical observation merges, unrelated observation is new")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                           dedup_enabled=False)
        svc = MemoryService(cfg)
        sid = "chat-1"; actor = "u1"
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        a2 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        b = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                        channel_id="g1", content="Bob dislikes cilantro intensely")
        print("a1=" + a1.id[:8] + " a2=" + a2.id[:8] + " b=" + b.id[:8])
        assert a1.id == a2.id, "identical text must merge into one engram"
        assert b.id != a1.id, "unrelated text must be stored separately"
        assert len(svc.store.all(limit=100)) == 2
        cos1b = _cos(a1.embedding, b.embedding)
        print("cos(a1,b)=" + str(round(cos1b, 3)))
        result = svc.recall(Cue(text="Americano coffee", actor_id=actor,
                                channel_id="g1", k=3))
        assert a1.id in [e.id for e in result.engrams]
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect(); _gc.collect()
        print("  merge + new: OK")


def test_disabled():
    banner("disabled: enable_separation=False stores every observation separately")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                           enable_separation=False, dedup_enabled=False)
        svc = MemoryService(cfg)
        sid = "chat-1"; actor = "u1"
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        a2 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        print("a1=" + a1.id[:8] + " a2=" + a2.id[:8])
        assert a1.id != a2.id, "separation disabled must never merge"
        assert len(svc.store.all(limit=100)) == 2
        result = svc.recall(Cue(text="Americano coffee", actor_id=actor,
                                channel_id="g1", k=3))
        assert a1.id in [e.id for e in result.engrams]
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect(); _gc.collect()
        print("  disabled: OK")


if __name__ == "__main__":
    test_resolve_bands()
    test_service_merge_and_new()
    test_disabled()
    print(chr(10) + "ALL OK")
