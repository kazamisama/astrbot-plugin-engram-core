"""Smoke v1.76.5: visualization + realistic memory landing batch.

Covers:
  A recall debug route breakdown + weighted dual-route scoring
  B stats distributions (importance/tier/valence/stream/atom)
  C archive/restore + batch delete
  E source retention / re-summarize hook
  F dynamic route weighting
  G atom temporal TTL fields
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryConfig, MemoryService, Cue
from hippocampus.memory_atom_models import make_preference_atom, stamp_atom_temporal


def _service(tmp):
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "h.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    cfg.enable_atom_extraction = True
    return MemoryService(cfg)


def test_visualization_and_lifecycle():
    svc = _service(tempfile.mkdtemp())
    a = svc.observe(session_id="s", actor_id="u1", platform="test",
                    channel_id="g1", content="alpha memory")
    b = svc.observe(session_id="s", actor_id="u1", platform="test",
                    channel_id="g1", content="beta memory")
    assert svc.store.soft_forget(a.id)
    assert svc.restore_engram(a.id)
    assert svc.store.get(a.id).forgotten_at == 0.0
    r = svc.batch_delete_engrams([a.id, b.id], hard=False)
    assert r["soft_deleted"] == 2, r

    from page_api_modules.stats import StatsHandler
    from page_api_modules.utils import PageApiUtils
    data = StatsHandler(PageApiUtils()).get_stats(svc)["data"]
    assert data["importance_distribution"]
    assert data["status_breakdown"]["total"] >= 2
    assert "tier_breakdown" in data and "valence_breakdown" in data
    svc.close()
    print("A/B/C lifecycle + stats: OK")


def test_dual_route_breakdown_and_weighting():
    svc = _service(tempfile.mkdtemp())
    svc.observe(session_id="s", actor_id="u1", platform="test",
                channel_id="g1", content="张三喜欢美式咖啡")
    d = svc.explain_dual_route(Cue(text="张三喜欢什么", k=5))
    assert d["items"], d
    assert "score_breakdown" in d["items"][0]
    bd = d["items"][0]["score_breakdown"]
    for k in ("retrieval", "importance", "recency", "final_score"):
        assert k in bd, bd

    from hippocampus.retrieval.dual_route import DualRouteRetriever
    dr = DualRouteRetriever(svc, svc._dual_route_config())
    d_w, g_w, intent = dr._route_weights_for_query("张三和谁是朋友")
    assert intent == "relationship"
    assert g_w > d_w
    d2, g2, _ = dr._route_weights_for_query("什么是咖啡")
    assert d2 > g2
    svc.close()
    print("A/F dual-route breakdown + dynamic weights: OK")


def test_source_and_atom_temporal():
    svc = _service(tempfile.mkdtemp())
    e = svc.store_summary(
        {"summary": "高重要性回忆", "key_facts": ["f1"], "topics": ["t"],
         "participants": [], "relations": [], "importance": 0.9},
        {"session_id": "s", "actor_id": "u1", "platform": "test",
         "channel_id": "g1", "persona_id": "p", "memory_type": "episodic",
         "source_lines": [{"actor_id": "u1", "speaker": "张三", "content": "hi",
                            "ts": time.time(), "is_bot": False}]})
    assert e is not None
    lines = svc.store.get_memory_source(e.id)
    assert lines and lines[0]["speaker"] == "张三"

    svc._ensure_atom_layer()
    atom = stamp_atom_temporal(make_preference_atom("alice", "likes", "coffee"))
    assert atom.ttl_days >= 30.0
    assert atom.expires_at > time.time()
    assert 0.0 <= atom.temporal_score() <= 1.0
    svc.atom_store.upsert(atom)
    assert svc.atom_store.by_triple("alice", "likes", "coffee") is not None
    svc.close()
    print("E/G source retention + atom temporal: OK")


def main():
    test_visualization_and_lifecycle()
    test_dual_route_breakdown_and_weighting()
    test_source_and_atom_temporal()
    print("\nv1.76.5 smoke: ALL PASS")


if __name__ == "__main__":
    main()
