"""Smoke v1.2: metamemory (recall confidence) + episodic->semantic consolidation
+ forgetting-curve visualization.

Independent smoke. Mocks astrbot.api so it does not require a real AstrBot runtime.
"""
import os, tempfile, sys, types, asyncio


from _smoke_common import install_stub as _install_stub


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main
from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.types import Entity, Relation, Engram
from hippocampus.metamemory import recall_confidence, confidence_label, is_tip_of_tongue
from hippocampus.storage import HippocampalStore
from hippocampus.embeddings import HashEmbeddingProvider
from main import format_confidence, format_decaycurve, render_stats


def banner(t): print(chr(10) + "=== " + t + " ===")


def _svc(td, **over):
    cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), **over)
    return MemoryService(cfg)


def test_storage_confidence_column():
    banner("storage: confidence column round-trips + migration is idempotent")
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "h.db")
        s = HippocampalStore(db, HashEmbeddingProvider(64))
        e = Engram(content="hi", summary="hi", confidence=0.83)
        e.embedding = [0.1] * 64
        s.upsert(e)
        assert abs(s.get(e.id).confidence - 0.83) < 1e-9
        s.close()
        # reopen exercises _migrate_v12 against an existing file (idempotent)
        s2 = HippocampalStore(db, HashEmbeddingProvider(64))
        assert abs(s2.get(e.id).confidence - 0.83) < 1e-9
        s2.close()
        print("  confidence column: OK")


def test_recall_confidence_present_and_aligned():
    banner("metamemory: recall returns confidences aligned with engrams")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        for t in ["I like coffee", "I really like coffee", "I love espresso"]:
            svc.observe(session_id="s1", actor_id="u1", platform="qq",
                        channel_id="g1", content=t)
        r = svc.recall(Cue(text="coffee", actor_id="u1", channel_id="g1", k=5))
        assert r.confidences is not None
        assert len(r.confidences) == len(r.engrams)
        assert all(0.0 <= c <= 1.0 for c in r.confidences)
        labels = [confidence_label(c, svc.cfg) for c in r.confidences]
        assert set(labels) <= {"high", "medium", "low"}
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  recall confidence: OK ->", [round(c, 2) for c in r.confidences])


def test_metamemory_disabled():
    banner("metamemory: disabling cfg.metamemory_enabled drops confidences")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td, metamemory_enabled=False)
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                    channel_id="g1", content="hello world")
        r = svc.recall(Cue(text="hello", actor_id="u1", channel_id="g1", k=5))
        assert r.confidences is None
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  metamemory disabled: OK")


def test_tip_of_tongue_helper():
    banner("metamemory: tip-of-tongue helper fires on low confidence + nonzero score")
    cfg = MemoryConfig()
    assert is_tip_of_tongue(0.1, 0.5, cfg) is True
    assert is_tip_of_tongue(0.9, 0.5, cfg) is False
    assert is_tip_of_tongue(0.1, 0.0, cfg) is False
    print("  tip-of-tongue: OK")


def test_decaycurve_render():
    banner("forgetting curve: render for all + single id")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        e = svc.observe(session_id="s1", actor_id="u1", platform="qq",
                        channel_id="g1", content="a memory that will fade")
        out_all = format_decaycurve(svc, "all")
        assert "forgetting curve" in out_all or "遗忘曲线" in out_all
        assert e.id[:8] in out_all
        out_one = format_decaycurve(svc, e.id)
        assert e.id[:8] in out_one
        out_bad = format_decaycurve(svc, "deadbeef")
        assert "no engram" in out_bad or "未找到" in out_bad
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  decaycurve: OK")


def test_main_helpers_and_stats():
    banner("main.py: format_confidence + render_stats expose v1.2")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                    channel_id="g1", content="espresso every morning")
        conf = format_confidence(svc, "espresso")
        assert "metamemory for" in conf or "元记忆评估" in conf
        stats = render_stats(svc)
        assert "--- v1.2 ---" in stats and ("metamemory:" in stats or "元记忆：" in stats)
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  main helpers + stats: OK")


def test_v11_regression():
    banner("regression: v1.1 profile build + activation still work on top of v1.2")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        actor = "u9"
        a1 = svc.observe(session_id="s1", actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        a2 = svc.observe(session_id="s1", actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee very much")
        res = svc.force_consolidate()
        assert "replayed" in res and "abstracted" not in res
        # spreading activation API still callable
        acts = svc.spread_activation(["coffee"])
        assert isinstance(acts, dict)
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  v1.1 regression: OK")


if __name__ == "__main__":
    test_storage_confidence_column()
    test_recall_confidence_present_and_aligned()
    test_metamemory_disabled()
    test_tip_of_tongue_helper()
    test_decaycurve_render()
    test_main_helpers_and_stats()
    test_v11_regression()
    print(chr(10) + "ALL OK")