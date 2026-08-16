"""Smoke v1.76.10: persistent full-graph replication."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryConfig, MemoryService


def test_full_graph_index_snapshot_and_cascade():
    cfg = MemoryConfig(sqlite_path=os.path.join(tempfile.mkdtemp(), "h.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    cfg.enable_graph_indexing = True
    cfg.enable_atom_extraction = False
    svc = MemoryService(cfg)
    e = svc.observe(session_id="s", actor_id="u", platform="test",
                    channel_id="g", content="I love coffee",
                    persona_id="p", scope_id="sc")
    svc._ensure_atom_layer()
    assert svc.graph_store is not None

    snap = svc.graph_store.full_graph_snapshot_v2(scope_id="sc", persona_id="p")
    assert snap["nodes"], snap
    assert snap["memories"], snap
    stats = svc.graph_store.graph_stats_v2()
    assert stats["entries"] >= 1 and stats["edges"] >= 1, stats

    svc.store.delete(e.id)
    stats2 = svc.graph_store.graph_stats_v2()
    assert stats2["entries"] == 0 and stats2["edges"] == 0, stats2
    svc.close()
    print("v1.76.10 full-graph + cascade: OK")


def main():
    test_full_graph_index_snapshot_and_cascade()
    print("\nv1.76.10 smoke: ALL PASS")


if __name__ == "__main__":
    main()
