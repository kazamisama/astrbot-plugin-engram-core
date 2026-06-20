"""Smoke v50: LLM-centric knowledge graph (v1.29).

The WebUI graph is sourced from RelationStore (LLM triples); entity type
comes from LLM subject_type/object_type (fallback rule _classify). This
is the fix for: ?? entities surfaced as type=unknown, and LLM relation
confidence never persisted (RelationStore collided with SemanticStore on
the `relations` table name; it now uses `llm_relations`).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_SH = "\u4e0a\u6d77"
_XM = "\u5c0f\u660e"


def main():
    from hippocampus import MemoryService, MemoryConfig
    from page_api_modules.graph import GraphHandler, _eid

    class _Utils:
        def ok(self, d): return {"status": "ok", "data": d}
        def error(self, m): return {"status": "error", "message": m}

    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    svc = MemoryService(cfg)
    gh = GraphHandler(_Utils())
    try:
        summary = {
            "summary": "s", "key_facts": [], "topics": [],
            "participants": ["Alice"],
            "relations": [
                {"subject": "Alice", "relation": "resides_in",
                 "object": _SH, "confidence": 0.9,
                 "subject_type": "person", "object_type": "place"},
                {"subject": "Alice", "relation": "knows",
                 "object": _XM, "confidence": 0.7,
                 "subject_type": "person", "object_type": "person"},
            ],
            "participant_names": {},
        }
        identity = {"chat_type": "group", "actor_id": "conversation",
                    "channel_id": "g1", "memory_type": "episodic"}
        svc.store_summary(summary, identity)

        # 1) relations landed in RelationStore (llm_relations)
        assert svc.relation_store.count_active() == 2, svc.relation_store.count_active()

        # 2) graph_data derives nodes from endpoints w/ LLM types
        gd = gh.graph_data(svc)["data"]
        names = {n["name"]: n["type"] for n in gd["nodes"]}
        assert names.get("Alice") == "person", names
        assert names.get(_SH) == "place", names
        # 3) ??-typed object surfaces as person (was unknown pre-v1.29)
        assert names.get(_XM) == "person", names
        assert len(gd["edges"]) == 2, gd["edges"]

        gq = gh.graph_query(svc, name=_XM)["data"]
        assert gq["entity"]["type"] == "person", gq
        assert all(r["id"] for r in gq["relations"]), gq

        rid = gq["relations"][0]["id"]
        assert gh.update_relation(svc, rid, 0.33)["status"] == "ok"
        assert abs(svc.relation_store.get_by_id(rid).confidence - 0.33) < 1e-9
        assert gh.delete_relation(svc, rid)["status"] == "ok"
        assert svc.relation_store.get_by_id(rid) is None

        r = gh.delete_entity(svc, _eid("Alice"))
        assert r["status"] == "ok", r
        assert svc.relation_store.relations_for("Alice") == []
        print("v50 OK")
    finally:
        svc.close()
        try: os.unlink(db)
        except OSError: pass


if __name__ == "__main__":
    main()
