"""Smoke v49: graph entity hard-delete + relation confidence/delete (v1.28).

Backend-level coverage:
  SemanticStore low-level ops (still used by rule path) +
  GraphHandler ops now sourced from RelationStore (v1.29 LLM-centric).
  - delete_entity removes the entity AND every relation touching it.
  - set_relation_confidence clamps to [0,1] and persists.
  - delete_relation removes a single edge.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    from hippocampus.semantic import SemanticStore
    from hippocampus.types import Entity, Relation

    td = tempfile.mkdtemp()
    sem = SemanticStore(os.path.join(td, "sem.db"))

    a = sem.upsert_entity(Entity(name="Alice", type="person"))
    b = sem.upsert_entity(Entity(name="Shanghai", type="place"))
    c = sem.upsert_entity(Entity(name="Bob", type="person"))
    r1 = Relation(subject_id=a.id, predicate="resides_in", object_id=b.id,
                  confidence=0.8)
    r2 = Relation(subject_id=c.id, predicate="knows", object_id=a.id,
                  confidence=0.5)
    sem.add_relation(r1)
    sem.add_relation(r2)

    # set_relation_confidence clamps and persists
    assert sem.set_relation_confidence(r1.id, 1.7) is True
    got = [x for x in sem.relations_of(a.id) if x.id == r1.id][0]
    assert got.confidence == 1.0, got.confidence
    assert sem.set_relation_confidence(r1.id, -0.3) is True
    got = [x for x in sem.relations_of(a.id) if x.id == r1.id][0]
    assert got.confidence == 0.0, got.confidence
    assert sem.set_relation_confidence("nope", 0.5) is False
    print("  set_relation_confidence clamp+persist: OK")

    # delete a single relation
    assert sem.delete_relation(r2.id) is True
    assert sem.delete_relation(r2.id) is False
    assert all(x.id != r2.id for x in sem.relations_of(a.id))
    print("  delete_relation: OK")

    # delete entity cascades to its remaining relations (r1)
    n = sem.delete_entity(a.id)
    assert n == 1, n
    assert sem.get_entity(a.id) is None
    assert sem.relations_of(b.id) == []
    print("  delete_entity cascade: OK")

    # GraphHandler wrappers now operate on RelationStore (v1.29).
    from page_api_modules.graph import GraphHandler, _eid
    from hippocampus.relation_store import RelationStore, Relation as RSRel
    rs = RelationStore(os.path.join(td, "rel.db"))
    rs.add_with_supersede(RSRel(subject="Alice", predicate="resides_in",
                                object="Shanghai", confidence=0.8,
                                subject_type="person", object_type="place"))
    rs.add_with_supersede(RSRel(subject="Bob", predicate="knows",
                                object="Alice", confidence=0.5,
                                subject_type="person", object_type="person"))
    class _Utils:
        def ok(self, d): return {"status": "ok", "data": d}
        def error(self, m): return {"status": "error", "message": m}
    class _Svc:
        relation_store = rs
        store = None
    gh = GraphHandler(_Utils())
    # update/delete by relation id
    one = rs.all_active()[0]
    assert gh.update_relation(_Svc(), one.id, 0.42)["status"] == "ok"
    assert abs(rs.get_by_id(one.id).confidence - 0.42) < 1e-9
    assert gh.update_relation(_Svc(), "nope", 0.5)["status"] == "error"
    assert gh.delete_relation(_Svc(), "nope")["status"] == "error"
    assert gh.delete_relation(_Svc(), one.id)["status"] == "ok"
    assert rs.get_by_id(one.id) is None
    # entity hard-delete removes every relation touching the name
    assert gh.delete_entity(_Svc(), _eid("Alice"))["status"] == "ok"
    assert rs.relations_for("Alice") == []
    # graph_query type comes from LLM-provided subject_type
    rs.add_with_supersede(RSRel(subject="Carol", predicate="likes",
                                object="tea", confidence=0.6,
                                subject_type="person", object_type="object"))
    gq = gh.graph_query(_Svc(), name="Carol")
    assert gq["status"] == "ok" and gq["data"]["entity"]["type"] == "person", gq
    print("  GraphHandler wrappers (RelationStore): OK")

    print("v49 PASS")


if __name__ == "__main__":
    main()
