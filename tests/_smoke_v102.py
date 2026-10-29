"""Smoke v102 (R9 decision): graph_v2 is a WebUI-only derived view.

Decision record in docs/TODO.md §12: of the five representations that share the
SQLite file, graph_v2 (graph_nodes_v2 / graph_edges_v2 / graph_entries_v2) exists
ONLY for the dashboard's "全量图谱" toggle. Recall must not depend on it -- if it
did, rebuilding or deleting that view would change what memories surface.

This test pins the boundary and keeps the view exercised:

- recall() and recall_dual_route() never touch a graph_v2 table, with every
  SQLite connection traced (including the ones opened lazily after the service
  is constructed)
- the graph_v2 pipeline itself still works: index an engram, then
  full_graph_snapshot_v2() returns nodes / edges / memories and the stats agree
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _smoke_common import install_stub

install_stub()

from hippocampus import Cue, MemoryConfig, MemoryService  # noqa: E402
from hippocampus.types import Engram, Entity  # noqa: E402

GV2_TABLES = (
    "graph_nodes_v2",
    "graph_edges_v2",
    "graph_entries_v2",
    "graph_entry_nodes_v2",
    "graph_edge_memories_v2",
    "graph_entries_v2_fts",
)

TRACED = []
_orig_connect = sqlite3.connect


def _traced_connect(*args, **kwargs):
    conn = _orig_connect(*args, **kwargs)
    conn.set_trace_callback(lambda sql: TRACED.append(sql))
    return conn


FAILS = []
CHECKS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    CHECKS.append(bool(ok))
    if not ok:
        FAILS.append(msg)


def _vec(seed):
    return [((i * 37 + seed) % 17) / 17.0 - 0.5 for i in range(64)]


def test_recall_does_not_touch_graph_v2():
    print(chr(10) + "=== recall never reads graph_v2 ===")
    sqlite3.connect = _traced_connect
    try:
        with tempfile.TemporaryDirectory() as td:
            svc = MemoryService(MemoryConfig(
                sqlite_path=os.path.join(td, "h.db"), embedding_dim=64))
            try:
                e = Engram(
                    content="Alice lives in Beijing and likes coffee",
                    summary="Alice lives in Beijing and likes coffee",
                    actor_id="u1", channel_id="c1",
                    entities=["Alice", "Beijing", "coffee"],
                    topics=["coffee"], embedding=_vec(1),
                    importance=0.8)
                svc.store.upsert(e)
                # give the graph route something to walk, so the trace covers
                # dual-route's graph branch instead of skipping it
                ent = svc.semantic.upsert_entity(
                    Entity(name="Alice", type="person"))
                e.entity_refs = [ent.id]
                svc.store.upsert(e)
                svc._ensure_atom_layer()
                check(svc.graph_store is not None,
                      "graph store available for the boundary test")
                svc.graph_store.add_entity_engram_ref(ent.id, e.id)
                svc._index_graph_v2(e)

                TRACED.clear()
                r1 = svc.recall(Cue(text="coffee in Beijing", actor_id="u1"))
                r2 = svc.recall_dual_route(Cue(text="coffee in Beijing", actor_id="u1"))
                check(bool(r1.engrams) and bool(r2.engrams),
                      "both recall paths returned the seeded memory")
                bad = [sql for sql in TRACED
                       if any(t in sql for t in GV2_TABLES)]
                check(not bad, "no graph_v2 table touched by recall (got %d hits: %r)"
                      % (len(bad), bad[:2]))
            finally:
                svc.close()
    finally:
        sqlite3.connect = _orig_connect


def test_graph_v2_view_still_works():
    print(chr(10) + "=== the dashboard's full-graph view still works ===")
    with tempfile.TemporaryDirectory() as td:
        svc = MemoryService(MemoryConfig(
            sqlite_path=os.path.join(td, "h.db"), embedding_dim=64))
        try:
            e = Engram(content="Bob lives in Shanghai. Bob likes tea.",
                       summary="Bob lives in Shanghai. Bob likes tea.",
                       actor_id="u2", entities=["Bob", "Shanghai", "tea"],
                       topics=["tea"], embedding=_vec(2))
            svc.store.upsert(e)
            svc._ensure_atom_layer()
            check(svc.graph_store is not None, "graph store initialised")
            svc._index_graph_v2(e)
            snap = svc.graph_store.full_graph_snapshot_v2()
            check(bool(snap.get("nodes")), "snapshot has nodes")
            check(bool(snap.get("edges")), "snapshot has edges")
            check(bool(snap.get("memories")), "snapshot lists the source memory")
            stats = svc.graph_store.graph_stats_v2()
            check(stats.get("nodes", 0) >= len(snap["nodes"]),
                  "stats nodes >= snapshot nodes (stats counts storage)")
            labels = {str(n.get("label", "")) for n in snap["nodes"]}
            check(any("Bob" in s for s in labels),
                  "snapshot contains the entity extracted from the memory")
        finally:
            svc.close()


test_recall_does_not_touch_graph_v2()
test_graph_v2_view_still_works()

print(chr(10) + "TOTAL=" + str(len(CHECKS)) + " FAIL=" + str(len(FAILS)))
if FAILS:
    raise SystemExit(1)
