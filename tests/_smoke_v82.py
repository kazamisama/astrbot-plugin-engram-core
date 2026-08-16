"""Smoke v1.76.12 review regression batch (2026-08 review).

Covers bugs found during repository review:
  1. persisted prompt overrides must propagate to cfg._prompt_namespace
     (encoder / summarizer / diary / consolidation consumers)
  2. SpreadingActivation.activate_with_context accepts Cue.session_id again
     (v1.76.7 accidentally dropped the parameter, so session-context seeds
     were silently disabled)
  3. RelationStore.delete_by_source_engram targets llm_relations instead of
     the unrelated SemanticStore `relations` table
  4. GraphRetriever shares the service's canonical graph_store connection
     instead of leaking a second `_graph_store` connection
  5. spread route obeys Cue.memory_types
  6. working-memory prepend obeys Cue.scope_id (persona was already filtered)
  7. _dual_route_config preserves legitimate 0.0 route weights
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryConfig, MemoryService, Cue
from hippocampus.activation import SpreadingActivation
from hippocampus.relation_store import Relation, RelationStore
from hippocampus.retrieval.dual_route import DualRouteRetriever
from hippocampus.retrieval.graph_retriever import GraphRetriever


def _base_cfg(path):
    cfg = MemoryConfig(sqlite_path=path)
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    return cfg


def test_prompt_namespace_propagates_to_cfg():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "p.db")))
    assert getattr(svc.cfg, "_prompt_namespace", None) == svc._prompt_namespace
    assert getattr(svc.encoder._cfg, "_prompt_namespace", None) == svc._prompt_namespace
    svc.close()
    print("R1 prompt namespace propagation: OK")


def test_activate_with_context_accepts_session_id():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "a.db")))
    svc.cfg.enable_semantic = True
    svc.semantic = __import__("hippocampus.semantic", fromlist=["SemanticStore"]).SemanticStore(svc.cfg.sqlite_path)
    svc.activation = SpreadingActivation(svc.semantic, svc.store, svc.cfg, svc.graph_store)
    svc.observe(session_id="sess_A", actor_id="u", platform="test",
                channel_id="c", content="session seed")
    seeds = svc.activation.build_context_seeds(session_id="sess_A", session_count=3)
    assert seeds, seeds
    act = svc.activation.activate_with_context(
        session_id="sess_A", recent_count=0, high_importance_count=0)
    assert act, act
    svc.close()
    print("R2 activation session_id: OK")


def test_relation_cascade_uses_llm_relations():
    rs = RelationStore(os.path.join(tempfile.mkdtemp(), "r.db"))
    rs.add_with_supersede(Relation(subject="Alice", predicate="likes",
                                   object="coffee", source_engram_id="e1"))
    assert rs.all_active(), "setup relation missing"
    assert rs.delete_by_source_engram("e1") == 1
    assert rs.all_active() == []
    print("R3 llm_relations cascade: OK")


def test_graph_retriever_shares_canonical_store():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "g.db")))
    gr = GraphRetriever(service=svc)
    assert gr._graph is svc.graph_store
    assert svc.graph_store is svc._graph_store
    svc.close()
    print("R4 graph retriever canonical store: OK")


def test_spread_route_obeys_memory_types():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "s.db")))
    e = svc.observe(session_id="s", actor_id="u", platform="test",
                    channel_id="c", content="diary-like memory")
    e.memory_type = "diary"
    svc.store.upsert(e)
    dr = DualRouteRetriever(svc)
    cue = Cue(text="diary-like", memory_types=["episodic"],
              activation={e.id: 0.9}, k=5)
    assert dr._spread_route(cue) == []
    svc.close()
    print("R5 spread route memory_types: OK")


def test_working_memory_scope_filter():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "w.db")))
    svc.observe(session_id="s", actor_id="u", platform="test",
                channel_id="c", content="scope leak check", scope_id="scope:b")
    res = svc.recall(Cue(text="scope leak check", actor_id="u", channel_id="c",
                         scope_id="scope:a", k=5, mode="fts"))
    assert all((getattr(e, "scope_id", "") or "") == "scope:a" for e in res.engrams)
    svc.close()
    print("R6 working memory scope filter: OK")


def test_zero_route_weight_is_preserved():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "z.db")))
    svc.cfg.document_route_weight = 0.0
    svc.cfg.graph_route_weight = 1.0
    svc.cfg.dynamic_route_weighting = False
    cfg = svc._dual_route_config()
    assert cfg.document_route_weight == 0.0
    assert cfg.graph_route_weight == 1.0
    svc.close()
    print("R7 zero route weight preserved: OK")

def test_diary_store_scope_partitioning():
    import time
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "d.db")))
    svc.cfg.diary_enabled = True
    base_meta = {
        "channel_id": "c1", "chat_type": "private", "actor_id": "u",
        "speaker": "u", "content": "scoped line", "is_bot": False,
        "group_id": "", "group_name": "", "peer_actor_id": "u",
        "peer_name": "u", "session_id": "s", "platform": "test",
        "persona_id": "p",
    }
    svc.cache_daily_line({**base_meta, "scope_id": "scope:a"})
    svc.cache_daily_line({**base_meta, "scope_id": "scope:b"})
    svc.diary_store.flush_now()

    groups = svc.diary_store.channels_with_lines(0.0, time.time() + 10,
                                                 include_scope=True)
    assert {g[2] for g in groups} == {"scope:a", "scope:b"}, groups
    a_lines = svc.diary_store.lines_in_range("c1", 0.0, time.time() + 10,
                                             persona_id="p", scope_id="scope:a")
    b_lines = svc.diary_store.lines_in_range("c1", 0.0, time.time() + 10,
                                             persona_id="p", scope_id="scope:b")
    assert [ln.scope_id for ln in a_lines] == ["scope:a"]
    assert [ln.scope_id for ln in b_lines] == ["scope:b"]

    e = svc.store_diary(
        {"summary": "scoped diary", "key_facts": [], "topics": [],
         "participants": [], "importance": 0.6,
         "_first_ts": time.time() - 10, "_last_ts": time.time()},
        {"session_id": "s", "actor_id": "u", "platform": "test",
         "channel_id": "c1", "persona_id": "p", "scope_id": "scope:a",
         "chat_type": "private", "day_label": "2026-08-16"})
    assert e is not None and e.scope_id == "scope:a"
    assert svc.diary_store.all_chunks(
        limit=10, persona_id="p", scope_id="scope:a")
    assert svc.diary_store.all_chunks(
        limit=10, persona_id="p", scope_id="scope:b") == []
    svc.close()
    print("R8 diary store scope partitioning: OK")


def test_graph_v2_shared_edge_survives_first_owner_delete():
    svc = MemoryService(_base_cfg(os.path.join(tempfile.mkdtemp(), "g2.db")))
    svc.cfg.enable_graph_indexing = True
    e1 = svc.observe(session_id="s1", actor_id="u", platform="test",
                     channel_id="c", content="Alice likes coffee")
    e2 = svc.observe(session_id="s2", actor_id="u", platform="test",
                     channel_id="c", content="Alice likes coffee")
    g = svc.graph_store
    assert g.graph_stats_v2()["edges"] == 1, g.graph_stats_v2()
    links = g._conn.execute(
        "SELECT source_memory_id FROM graph_edge_memories_v2").fetchall()
    assert {r["source_memory_id"] for r in links} == {e1.id, e2.id}

    svc.store.delete(e1.id)
    assert g.graph_stats_v2()["edges"] == 1, g.graph_stats_v2()
    snap = g.full_graph_snapshot_v2()
    assert len(snap["edges"]) == 1, snap["edges"]
    assert snap["edges"][0]["memory_id"] == e2.id
    svc.close()
    print("R9 graph v2 shared-edge ownership: OK")


def test_backup_restore_uses_live_sqlite_backup():
    tmp = tempfile.mkdtemp()
    from hippocampus.managers.backup_manager import BackupManager
    svc = MemoryService(_base_cfg(os.path.join(tmp, "live.db")))
    e1 = svc.observe(session_id="s", actor_id="u", platform="test",
                     channel_id="c", content="before backup")
    bm = BackupManager(svc.cfg.sqlite_path, os.path.join(tmp, "backups"),
                       version_provider=lambda: "test")
    rec = bm.create(reason="review")
    svc.observe(session_id="s", actor_id="u", platform="test",
                channel_id="c", content="after backup")
    assert svc.store.count_all() == 2

    assert bm.restore(rec.backup_id) is True
    assert svc.store.count_all() == 1
    assert svc.store.get(e1.id) is not None
    svc.observe(session_id="s", actor_id="u", platform="test",
                channel_id="c", content="post restore")
    assert svc.store.count_all() == 2
    svc.close()
    print("R10 live backup restore: OK")






def main():
    test_prompt_namespace_propagates_to_cfg()
    test_activate_with_context_accepts_session_id()
    test_relation_cascade_uses_llm_relations()
    test_graph_retriever_shares_canonical_store()
    test_spread_route_obeys_memory_types()
    test_working_memory_scope_filter()
    test_zero_route_weight_is_preserved()
    test_diary_store_scope_partitioning()
    test_graph_v2_shared_edge_survives_first_owner_delete()
    test_backup_restore_uses_live_sqlite_backup()
    print("\nv1.76.12 review regression: ALL PASS")


if __name__ == "__main__":
    main()
