"""Smoke v1.79.1: graph refs are written by one helper, no scan fallback.

The graph route used to keep a full-table fallback for databases where
engram.entity_refs had been populated without mirroring into
graph_engram_refs. The fallback hid a real write bug: _mirror_relation_
to_semantic() appended entity ids on the dataclass after the row had
already been stored, so summaries never indexed their entities and the
fallback had to scan every engram on each miss.

v1.79.1 makes _link_engram_refs() the single writer for both
representations and backfills old rows once, when the graph store is
first created. These checks pin that contract:

1. store_summary() with relations persists entity_refs AND graph refs.
2. Rows that only carry entity_refs are backfilled when the graph store
   is built; the backfill is idempotent.
3. GraphRetriever makes no store.all() call when the reverse index has no
   rows for the matched entities.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryConfig, MemoryService
from hippocampus.types import Cue, Engram, Entity, Relation
from hippocampus.retrieval.graph_retriever import GraphRetriever

FAILS = []


def banner(msg):
    print("\n=== " + msg + " ===")


def check(cond, msg):
    if cond:
        print("  OK   " + msg)
    else:
        print("  FAIL " + msg)
        FAILS.append(msg)


def _svc(td):
    cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"),
                       embedding_name="hash", llm_name="rule",
                       enable_prospective=False)
    cfg.enable_atom_extraction = True
    cfg.enable_graph_indexing = True
    return MemoryService(cfg)


def _ref_rows(svc):
    return {r[0] for r in svc.graph_store._conn.execute(
        "SELECT engram_id FROM graph_engram_refs")}


def test_summary_relations_are_indexed():
    banner("store_summary: relations persist refs + reverse index")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            e = svc.store_summary(
                {"summary": "Alice likes espresso",
                 "key_facts": ["likes espresso"],
                 "relations": [{"subject": "Alice", "relation": "likes",
                                 "object": "espresso", "confidence": 0.9}]},
                {"session_id": "s1", "actor_id": "u1", "platform": "mock",
                 "channel_id": "c1", "memory_type": "episodic"})
            check(e is not None, "summary stored")
            reloaded = svc.store.get(e.id)
            check(reloaded is not None and len(reloaded.entity_refs) == 2,
                  "entity_refs persisted with the row")
            check(e.id in _ref_rows(svc),
                  "graph_engram_refs carries the engram")
            n = svc.graph_store._conn.execute(
                "SELECT COUNT(*) FROM graph_engram_refs WHERE engram_id=?",
                (e.id,)).fetchone()[0]
            check(n == 2, "two entity refs indexed (got %r)" % (n,))
        finally:
            svc.close()


def test_backfill_existing_rows():
    banner("backfill: entity_refs-only rows are mirrored once")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            ent = svc.semantic.upsert_entity(Entity(name="Bob", type="person"))
            e = Engram(content="Bob was here", summary="Bob was here",
                       entity_refs=[ent.id])
            svc.store.upsert(e)
            # Simulate a pre-v1.79.1 row: refs on the engram, index empty.
            svc._ensure_atom_layer()
            svc.graph_store._conn.execute("DELETE FROM graph_engram_refs")
            svc.graph_store._conn.commit()
            svc.graph_store.close()
            svc.graph_store = None
            svc.activation._graph = None
            svc._ensure_atom_layer()
            check(e.id in _ref_rows(svc), "backfill re-indexed the stored ref")
            before = len(_ref_rows(svc))
            svc.graph_store.close()
            svc.graph_store = None
            svc._ensure_atom_layer()
            check(len(_ref_rows(svc)) == before, "second backfill is a no-op")
        finally:
            svc.close()


def test_graph_route_has_no_scan_fallback():
    banner("GraphRetriever: a miss does not scan the engram table")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            svc.semantic.upsert_entity(Entity(name="Carol", type="person"))
            e = Engram(content="Carol unrelated note",
                       summary="Carol unrelated note", entity_refs=[])
            svc.store.upsert(e)
            orig = svc.store.all
            calls = {"n": 0}

            def counting(*a, **kw):
                calls["n"] += 1
                return orig(*a, **kw)

            svc.store.all = counting
            try:
                gr = GraphRetriever(svc)
                hits = gr.search(Cue(text="Carol", k=5))
            finally:
                svc.store.all = orig
            check(calls["n"] == 0,
                  "no full scan on a graph-route miss (got %d)" % calls["n"])
            check(isinstance(hits, list), "search still returns a list")
        finally:
            svc.close()


def main():
    test_summary_relations_are_indexed()
    test_backfill_existing_rows()
    test_graph_route_has_no_scan_fallback()
    if FAILS:
        print("\nFAILED %d check(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        raise SystemExit(1)
    print("\nall v1.79.1 graph-ref checks passed")


if __name__ == "__main__":
    main()