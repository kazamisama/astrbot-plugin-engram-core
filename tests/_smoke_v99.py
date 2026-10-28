"""Smoke v1.77.2: the dead graph adjacency layer is gone.

What changed:
- GraphStore no longer owns graph_adjacency / add_relation / neighbors /
  all_relations / rebuild_from_semantic.
- GraphRetriever no longer walks N hops (no max_hops parameter); it only
  hydrates the anchors' engrams through graph_engram_refs.
- The delete cascade no longer rebuilds the graph from semantic relations,
  so a hard delete cannot silently switch the multi-hop route on.

Tests:
1. GraphStore API surface: no adjacency methods; fresh DB has no
   graph_adjacency table; stats() reports only engram_refs.
2. Legacy graph_adjacency table is dropped when GraphStore opens the DB.
3. Deleting an engram removes its reverse-index rows and does NOT rebuild
   an adjacency table (a sentinel row survives).
4. DualRouteConfig has no graph_relation_hops; GraphRetriever has no
   max_hops; the reverse-index path still returns hits end-to-end.
"""
import os, sys, time, tempfile

from _smoke_common import install_stub as _install_stub

_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main  # noqa: F401  (plugin package import sanity)
from hippocampus import (
    MemoryConfig, MemoryService, Cue, Engram, GraphStore, GraphRetriever,
)
from hippocampus.retrieval import DualRouteConfig, DualRouteRetriever


def banner(t): print("\n=== " + t + " ===")


def _new_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return tmp.name


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_graphstore_surface():
    banner("GraphStore: adjacency API and table are gone")
    for name in ("add_relation", "neighbors", "all_relations",
                 "rebuild_from_semantic"):
        assert not hasattr(GraphStore, name), "stale API: " + name
    db = _new_db()
    try:
        gs = GraphStore(db)
        tables = _tables(gs._conn)
        assert "graph_adjacency" not in tables, tables
        assert "graph_engram_refs" in tables, tables
        gs.add_entity_engram_ref("e1", "en1")
        assert gs.stats() == {"engram_refs": 1}, gs.stats()
        gs.close()
        print("  surface: OK (no adjacency writers/readers, stats exact)")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_legacy_table_dropped():
    banner("GraphStore drops a legacy graph_adjacency table on open")
    db = _new_db()
    try:
        import sqlite3
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE graph_adjacency (entity_id TEXT, neighbor_id TEXT,"
            " predicate TEXT, weight REAL);"
            "INSERT INTO graph_adjacency VALUES ('a','b','p',1.0);")
        conn.commit(); conn.close()
        gs = GraphStore(db)
        assert "graph_adjacency" not in _tables(gs._conn)
        gs.close()
        print("  legacy drop: OK")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_delete_cascade_does_not_rebuild_adjacency():
    banner("hard delete: reverse index cleaned, no adjacency rebuild")
    db = _new_db()
    svc = None
    try:
        cfg = MemoryConfig(sqlite_path=db, embedding_dim=32,
                           enable_prospective=False)
        svc = MemoryService(cfg)
        svc.graph_store = GraphStore(db)
        e = svc.observe(session_id="s1", actor_id="u1", platform="mock",
                        channel_id="c1", content="Alice likes coffee")
        svc.graph_store = GraphStore(db)
        svc.graph_store.add_entity_engram_ref("ent_alice", e.id)
        assert svc.graph_store.stats()["engram_refs"] >= 1
        # Simulate a stale adjacency table left by an older build.
        svc.graph_store._conn.execute(
            "CREATE TABLE IF NOT EXISTS graph_adjacency"
            " (entity_id TEXT, neighbor_id TEXT, predicate TEXT, weight REAL)")
        svc.graph_store._conn.execute(
            "INSERT INTO graph_adjacency VALUES ('x','y','p',1.0)")
        svc.graph_store._conn.commit()
        start = time.time()
        assert svc.store.delete(e.id), "delete should remove the row"
        elapsed = time.time() - start
        assert svc.graph_store.stats()["engram_refs"] == 0
        left = svc.graph_store._conn.execute(
            "SELECT COUNT(*) FROM graph_adjacency").fetchone()[0]
        assert left == 1, "delete must not rebuild adjacency (got %d)" % left
        assert elapsed < 5.0, "delete path took %.2fs" % elapsed
        print("  delete cascade: OK (no rebuild, %.3fs)" % elapsed)
    finally:
        import gc
        try:
            if svc is not None:
                svc.close()
        except Exception:
            pass
        del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_retriever_reverse_index_path():
    banner("GraphRetriever: reverse index still feeds the graph route")
    import inspect
    assert "graph_relation_hops" not in DualRouteConfig.__dataclass_fields__
    assert "max_hops" not in inspect.signature(GraphRetriever.__init__).parameters
    db = _new_db()
    svc = None
    try:
        cfg = MemoryConfig(sqlite_path=db, embedding_dim=32,
                           enable_prospective=False)
        svc = MemoryService(cfg)
        from hippocampus.types import Entity
        svc.semantic.upsert_entity(Entity(id="ent_alice", name="Alice",
                                          type="person", mention_count=5))
        e = svc.observe(session_id="s1", actor_id="u1", platform="mock",
                        channel_id="c1", content="Alice is here")
        svc.graph_store = GraphStore(db)
        svc.graph_store.add_entity_engram_ref("ent_alice", e.id)
        gr = GraphRetriever(svc)
        rcs = gr.search(Cue(text="Alice", k=5))
        assert rcs and any(rc.item.id == e.id for rc in rcs), rcs
        # Dual-route graph route uses the same retriever.
        dr = DualRouteRetriever(svc, DualRouteConfig())
        hits = dr.explain(Cue(text="Alice", k=5))
        assert any(h.route.value == "graph" for h in hits), hits
        print("  reverse-index path: OK (retriever + dual route)")
    finally:
        import gc
        try:
            if svc is not None:
                svc.close()
        except Exception:
            pass
        del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def main():
    test_graphstore_surface()
    test_legacy_table_dropped()
    test_delete_cascade_does_not_rebuild_adjacency()
    test_retriever_reverse_index_path()
    print("\nALL OK")


if __name__ == "__main__":
    main()
