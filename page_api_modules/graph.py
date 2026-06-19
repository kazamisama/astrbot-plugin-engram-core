"""Graph handler for the page API (B9).

Endpoints:
  graph_overview()   -> {n_entities, n_relations, sample}
  graph_query(name)  -> resolve a single entity and return its
                       engram_refs + outgoing relations
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class GraphHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def graph_overview(self, service) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        sem = service.semantic
        if sem is None:
            return self.utils.ok({"n_entities": 0, "n_relations": 0,
                                   "sample": []})
        try:
            ents = sem.all_entities(limit=10_000_000)
            n_entities = len(ents)
        except Exception:
            n_entities = -1
        # SemanticStore has no all_relations(); n_relations is
        # reported as -1 until B9.x. entities are Entity dataclasses.
        n_relations = -1
        sample = []
        try:
            for e in (ents or [])[:10]:
                sample.append({
                    "id": getattr(e, "id", None),
                    "name": getattr(e, "name", None),
                    "type": getattr(e, "type", None),
                })
        except Exception:
            pass
        return self.utils.ok({
            "n_entities": n_entities,
            "n_relations": n_relations,
            "sample": sample,
        })

    def graph_query(self, service, name: str = "") -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        name = (name or "").strip()
        if not name:
            return self.utils.error("Missing name.")
        sem = service.semantic
        if sem is None:
            return self.utils.error("Semantic layer disabled.")
        try:
            ent = sem.find_entity_by_name(name)
        except Exception as e:
            return self.utils.error(f"resolve failed: {e!r}")
        if ent is None:
            return self.utils.error(f"unknown entity: {name}")
        eid = getattr(ent, "id", None)
        out_rels = []
        try:
            # SemanticStore.relations_of(eid) returns relations
            # involving eid (incoming + outgoing).
            for r in (sem.relations_of(eid) or []):
                # Relation has subject_id / object_id (entity ids);
                # resolve to names via get_entity() for human-readable
                # output. Falls back to the raw id on resolution fail.
                src_id = getattr(r, "subject_id", None)
                dst_id = getattr(r, "object_id", None)
                src_ent = sem.get_entity(src_id) if src_id else None
                dst_ent = sem.get_entity(dst_id) if dst_id else None
                out_rels.append({
                    "src": getattr(src_ent, "name", src_id),
                    "predicate": getattr(r, "predicate", None),
                    "dst": getattr(dst_ent, "name", dst_id),
                })
        except Exception:
            pass
        refs = []
        try:
            for en in service.store.list_active(limit=10_000_000):
                entity_refs = getattr(en, "entity_refs", None) or []
                if eid in entity_refs:
                    refs.append({
                        "id": getattr(en, "id", None),
                        "summary": (getattr(en, "summary", "") or "")[:160],
                    })
        except Exception:
            pass
        return self.utils.ok({
            "entity": {"id": eid, "name": getattr(ent, "name", None),
                       "type": getattr(ent, "type", None)},
            "relations": out_rels[:100],
            "engram_refs": refs[:100],
        })
