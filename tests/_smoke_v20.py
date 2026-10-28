"""Smoke v1.4 B4: GraphStore + GraphRetriever (keyword + vector + mixed).

Scope of B4: data layer + retrieval. Does NOT replace semantic.py.
- GraphStore owns the entity->engram reverse index:
    graph_engram_refs(entity_id, engram_id, weight)
  (v1.77.2 removed the never-written graph_adjacency mirror; the graph
  route no longer walks N hops)
- GraphKeywordRetriever: token-level entity match + scoring
- GraphVectorRetriever: cosine over entity name embeddings
- GraphRetriever: fuses both, hydrates engrams from the reverse index
- DualRouteRetriever._graph_route() now delegates to GraphRetriever

Tests:
- GraphStore reverse index: add / lookup / remove / stats
- GraphStore drops a legacy graph_adjacency table on open
- GraphStore.engrams_for reverse index
- GraphKeywordRetriever exact / partial / alias matches
- GraphVectorRetriever cosine ordering
- GraphRetriever fusion: top keyword anchor + top vector anchor
- GraphRetriever.search produces RankedCandidate list ready for RRF
- DualRouteRetriever end-to-end still works (text -> graph -> engrams)
- Legacy fallback: when graph_engram_refs is empty but engram.entity_refs
  is populated, GraphRetriever.search still returns hits (v1.3 path)
"""
import os, sys, types, tempfile


from _smoke_common import install_stub as _install_stub


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main
from hippocampus import (
    MemoryConfig, MemoryService, Cue, Entity, Relation, Engram,
    GraphStore, GraphRetriever, EntityMatch,
    GraphKeywordRetriever, GraphVectorRetriever,
)
from hippocampus.retrieval import DualRouteRetriever, DualRouteConfig


def banner(t): print("\n=== " + t + " ===")


def _new_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    return tmp.name


def test_graphstore_engram_refs():
    banner("GraphStore reverse index: add / lookup / remove / stats")
    db = _new_db()
    try:
        gs = GraphStore(db)
        gs.add_entity_engram_ref("e1", "en1", weight=1.0)
        gs.add_entity_engram_ref("e2", "en1", weight=0.5)
        assert gs.stats() == {"engram_refs": 2}, gs.stats()
        e1 = gs.engrams_for("e1", limit=10)
        e2 = gs.engrams_for("e2", limit=10)
        assert e1 and e1[0] == ("en1", 1.0), e1
        assert e2 and e2[0] == ("en1", 0.5), e2
        assert gs.remove_engram_refs("en1") == 2
        assert gs.stats()["engram_refs"] == 0
        gs.add_entity_engram_ref("e1", "en2")
        gs.remove_entity("e1")
        assert gs.stats()["engram_refs"] == 0
        # Schema contract: the dead adjacency mirror must not come back.
        names = {r[0] for r in gs._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "graph_adjacency" not in names, names
        assert "graph_engram_refs" in names
        gs.close()
        print("  reverse index: OK (no graph_adjacency table)")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_graphstore_drops_legacy_adjacency():
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
        names = {r[0] for r in gs._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "graph_adjacency" not in names, names
        gs.close()
        print("  legacy table dropped: OK")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_keyword_retriever_scoring():
    banner("GraphKeywordRetriever scoring: exact > partial > alias")
    db = _new_db()
    try:
        # Write entities directly via the same SQLite file.
        import sqlite3, json, time as _t
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE entities (
              id TEXT PRIMARY KEY, name TEXT, type TEXT,
              aliases TEXT, attributes TEXT,
              mention_count INTEGER,
              created_at REAL, last_seen REAL,
              source_engram_ids TEXT
            );
        """)
        now = _t.time()
        def ins(eid, name, aliases, mc):
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, 'thing', ?, '{}', ?, ?, ?, '[]')",
                (eid, name, json.dumps(aliases, ensure_ascii=False), mc, now, now),
            )
        ins("e_alice", "Alice", [], 5)
        ins("e_alicia", "Alicia", ["Alice"], 3)
        ins("e_bob",   "Bob",   [], 2)
        ins("e_am",    "Americano", [], 1)
        conn.commit()
        conn.close()
        gs = GraphStore(db)
        kr = GraphKeywordRetriever(gs)
        # Exact "Alice" -> e_alice wins
        m = kr.search(["Alice"], k=4)
        assert m, "expected at least one match"
        assert m[0].entity.id == "e_alice"
        assert m[0].source == "keyword"
        # Multi-token: "Americano Bob" -> e_am + e_bob
        m2 = kr.search(["Americano", "Bob"], k=4)
        ids = {x.entity.id for x in m2}
        assert {"e_am", "e_bob"}.issubset(ids)
        # No match for nonsense
        assert kr.search(["zzzzzzzzzzz"], k=4) == []
        gs.close()
        print("  keyword scoring: OK (exact match wins, multi-token, miss)")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_vector_retriever_ordering():
    banner("GraphVectorRetriever: cosine ordering of entity names")
    db = _new_db()
    try:
        import sqlite3, json, time as _t
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE entities (
              id TEXT PRIMARY KEY, name TEXT, type TEXT,
              aliases TEXT, attributes TEXT,
              mention_count INTEGER,
              created_at REAL, last_seen REAL,
              source_engram_ids TEXT
            );
        """)
        now = _t.time()
        for eid, name, mc in [("e_am", "Americano", 5),
                                ("e_lt", "Latte", 3),
                                ("e_t",  "Tea", 1)]:
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, 'drink', '[]', '{}', ?, ?, ?, '[]')",
                (eid, name, mc, now, now),
            )
        conn.commit(); conn.close()
        from hippocampus.embeddings import HashEmbeddingProvider
        gs = GraphStore(db)
        emb = HashEmbeddingProvider(dim=32)
        vr = GraphVectorRetriever(gs, emb)
        # Query vec: embed "Americano" -> should rank e_am first
        m = vr.search(emb.embed("Americano"), k=3)
        assert m and m[0].entity.id == "e_am", m
        # Sanity: same-string query must be top-1 by self-cosine.
        m2 = vr.search(emb.embed("Latte"), k=3)
        assert m2 and m2[0].entity.id == "e_lt"
        # Negative result shape: a query whose token has no overlap with any
        # entity name returns [] (filter `score > 0`).
        m3 = vr.search(emb.embed("XyzQqq"), k=3)
        assert m3 == []
        gs.close()
        print("  vector ordering: OK (e_am top-1 for query 'Americano')")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_graph_retriever_fusion_via_service():
    banner("GraphRetriever: full search via MemoryService + dual route")
    db = _new_db()
    try:
        cfg = MemoryConfig(sqlite_path=db, embedding_dim=32,
                             enable_prospective=False)
        svc = MemoryService(cfg)
        from hippocampus.types import Entity, Relation
        svc.semantic.upsert_entity(Entity(id="ent_alice", name="Alice",
                                             type="person", mention_count=5))
        svc.semantic.upsert_entity(Entity(id="ent_am", name="Americano",
                                             type="drink", mention_count=3))
        svc.semantic.add_relation(Relation(subject_id="ent_alice", predicate="likes",
                                            object_id="ent_am", confidence=0.9))
        e1 = svc.observe(session_id="s1", actor_id="u1", platform="mock",
                         channel_id="c1", content="Alice loves Americano coffee")
        # Mirror the legacy entity_refs path (this is what v17 tests do too).
        e1.entity_refs = ["ent_alice", "ent_am"]
        svc.store.upsert(e1)
        # Trigger dual route; graph route should hit Alice via fallback.
        dr = DualRouteRetriever(svc, DualRouteConfig())
        res = dr.search(Cue(text="tell me about Alice", k=5))
        assert len(res.engrams) >= 1
        # _matched_entity must be set on at least one graph hit
        hits = dr.explain(Cue(text="tell me about Alice", k=5))
        assert any(h.route.value == "graph" for h in hits), \
            "graph route should still find Alice via entity_refs fallback"
        # Direct GraphRetriever invocation
        gr = GraphRetriever(svc)
        rcs = gr.search(Cue(text="Alice Americano", k=5))
        # Empty graph_engram_refs, but fallback returns at least the e1
        assert rcs, "GraphRetriever should fall back to entity_refs path"
        assert any(getattr(rc, "_matched_entity", "") for rc in rcs)
        print("  GraphRetriever fallback: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_graph_retriever_fast_path():
    banner("GraphRetriever: fast path uses graph_engram_refs")
    db = _new_db()
    try:
        cfg = MemoryConfig(sqlite_path=db, embedding_dim=32,
                             enable_prospective=False)
        svc = MemoryService(cfg)
        from hippocampus.types import Entity, Relation
        svc.semantic.upsert_entity(Entity(id="ent_alice", name="Alice",
                                             type="person", mention_count=5))
        e1 = svc.observe(session_id="s1", actor_id="u1", platform="mock",
                         channel_id="c1", content="Alice is here")
        # No entity_refs on engram, but mirror into GraphStore.
        gs = GraphStore(db)
        gs.add_entity_engram_ref("ent_alice", e1.id, weight=1.0)
        gr = GraphRetriever(svc)
        rcs = gr.search(Cue(text="Alice", k=5))
        assert rcs, "fast path: should hit the reverse index"
        assert any(rc.item.id == e1.id for rc in rcs)
        gs.close()
        print("  fast path: OK (reverse index hit, no fallback scan)")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_public_exports():
    banner("hippocampus + retrieval public surface exposes the B4 symbols")
    import hippocampus, hippocampus.retrieval as r
    for name in ("GraphStore", "GraphRetriever", "EntityMatch",
                 "GraphKeywordRetriever", "GraphVectorRetriever"):
        assert name in hippocampus.__all__, name
        assert hasattr(hippocampus, name), name
    for name in ("GraphRetriever", "EntityMatch",
                 "GraphKeywordRetriever", "GraphVectorRetriever"):
        assert name in r.__all__, name
    print("  public surface: OK")


def main():
    test_graphstore_engram_refs()
    test_graphstore_drops_legacy_adjacency()
    test_keyword_retriever_scoring()
    test_vector_retriever_ordering()
    test_graph_retriever_fusion_via_service()
    test_graph_retriever_fast_path()
    test_public_exports()
    print("\nALL OK")


if __name__ == "__main__":
    main()
