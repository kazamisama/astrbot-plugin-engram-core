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

    def graph_data(self, service, limit: int = 300) -> dict[str, Any]:
        """Build a node-link graph for visualization.

        Walks every entity and its relations_of() once, de-duplicating
        edges (relations_of returns both incoming + outgoing, so the
        same relation surfaces from both endpoints). Returns:
          {nodes: [{id, name, type, mentions}],
           edges: [{src, dst, predicate}],
           truncated: bool}
        Nodes are capped at `limit` to keep the payload renderable.
        """
        if service is None:
            return self.utils.error("Memory service not initialized.")
        sem = service.semantic
        if sem is None:
            return self.utils.ok({"nodes": [], "edges": [], "truncated": False})
        try:
            cap = max(1, min(int(limit), 2000))
        except Exception:
            cap = 300
        try:
            ents = sem.all_entities(limit=10_000_000) or []
        except Exception as e:
            return self.utils.error(f"all_entities failed: {e!r}")
        truncated = len(ents) > cap
        ents = ents[:cap]
        node_ids = set()
        nodes = []
        for e in ents:
            eid = getattr(e, "id", None)
            if eid is None:
                continue
            node_ids.add(eid)
            nodes.append({
                "id": eid,
                "name": getattr(e, "name", None) or eid,
                "type": getattr(e, "type", None) or "unknown",
                "mentions": getattr(e, "mention_count", 0) or 0,
            })
        seen = set()
        edges = []
        for e in ents:
            eid = getattr(e, "id", None)
            if eid is None:
                continue
            try:
                rels = sem.relations_of(eid) or []
            except Exception:
                continue
            for r in rels:
                src = getattr(r, "subject_id", None)
                dst = getattr(r, "object_id", None)
                if not src or not dst:
                    continue
                # keep edges only between nodes we actually return
                if src not in node_ids or dst not in node_ids:
                    continue
                key = (src, dst, getattr(r, "predicate", "") or "")
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "src": src,
                    "dst": dst,
                    "predicate": getattr(r, "predicate", None) or "",
                })
        return self.utils.ok({
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
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
