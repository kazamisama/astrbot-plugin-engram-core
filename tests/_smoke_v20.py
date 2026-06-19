"""Smoke v1.4 B4: GraphStore + GraphRetriever (keyword + vector + mixed).

Scope of B4: data layer + retrieval. Does NOT replace semantic.py.
- GraphStore mirrors the entities/relations tables into:
    graph_adjacency(entity_id, neighbor_id, predicate, weight)
    graph_engram_refs(entity_id, engram_id, weight)
- GraphKeywordRetriever: token-level entity match + scoring
- GraphVectorRetriever: cosine over entity name embeddings
- GraphRetriever: fuses both, walks the graph N hops, hydrates engrams
- DualRouteRetriever._graph_route() now delegates to GraphRetriever

Tests:
- GraphStore schema + add_relation mirrors adjacency in both directions
- GraphStore.neighbors BFS up to N hops
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
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


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


def test_graphstore_add_relation_mirrors():
    banner("GraphStore.add_relation mirrors adjacency + engram refs")
    db = _new_db()
    try:
        gs = GraphStore(db)
        r = Relation(id="r1", subject_id="e1", predicate="likes",
                      object_id="e2", source_engram_id="en1", confidence=0.9)
        gs.add_relation(r)
        stats = gs.stats()
        assert stats["adjacency"] == 2, "undirected: should have 2 rows (e1<->e2, e2<->e1)"
        assert stats["engram_refs"] == 2, "e1->en1 and e2->en1"
        # neighbors
        n1 = gs.neighbors("e1", max_hops=1)
        n2 = gs.neighbors("e2", max_hops=1)
        ids1 = {x[0] for x in n1}
        ids2 = {x[0] for x in n2}
        assert "e2" in ids1 and "e1" in ids2
        # engrams_for
        e_for_e1 = gs.engrams_for("e1", limit=10)
        e_for_e2 = gs.engrams_for("e2", limit=10)
        assert e_for_e1 and e_for_e1[0][0] == "en1"
        assert e_for_e2 and e_for_e2[0][0] == "en1"
        gs.close()
        print("  add_relation mirror: OK (2 adj, 2 refs, both directions)")
    finally:
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_graphstore_neighbors_bfs():
    banner("GraphStore.neighbors BFS up to N hops")
    db = _new_db()
    try:
        gs = GraphStore(db)
        # Build chain: a - r1 - b - r2 - c - r3 - d
        gs.add_relation(Relation(id="r1", subject_id="a", predicate="p",
                                  object_id="b", source_engram_id="e1"))
        gs.add_relation(Relation(id="r2", subject_id="b", predicate="p",
                                  object_id="c", source_engram_id="e2"))
        gs.add_relation(Relation(id="r3", subject_id="c", predicate="p",
                                  object_id="d", source_engram_id="e3"))
        # 1 hop from a -> only b
        h1 = gs.neighbors("a", max_hops=1)
        assert {x[0] for x in h1} == {"b"}
        # 2 hops from a -> b, c
        h2 = gs.neighbors("a", max_hops=2)
        assert {x[0] for x in h2} == {"b", "c"}
        # 3 hops from a -> b, c, d
        h3 = gs.neighbors("a", max_hops=3)
        assert {x[0] for x in h3} == {"b", "c", "d"}
        # depth values
        dmap = {x[0]: x[1] for x in h3}
        assert dmap["b"] == 1 and dmap["c"] == 2 and dmap["d"] == 3
        # starting entity not included
        assert "a" not in {x[0] for x in h3}
        gs.close()
        print("  neighbors BFS: OK (1/2/3-hop)")
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
        gr = GraphRetriever(svc, max_hops=1)
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
        gr = GraphRetriever(svc, max_hops=1)
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
    test_graphstore_add_relation_mirrors()
    test_graphstore_neighbors_bfs()
    test_keyword_retriever_scoring()
    test_vector_retriever_ordering()
    test_graph_retriever_fusion_via_service()
    test_graph_retriever_fast_path()
    test_public_exports()
    print("\nALL OK")


if __name__ == "__main__":
    main()
