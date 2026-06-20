"""Graph handler for the page API (v1.29: LLM-centric, RelationStore-sourced).

The knowledge graph is sourced from RelationStore (LLM-summarized
structured triples with per-relation confidence + supersede), NOT from
SemanticStore. Entity nodes are DERIVED from relation endpoints; their
type comes from the LLM-provided subject_type/object_type, falling back
to rule classification.

Endpoints:
  graph_overview()   -> {n_entities, n_relations, sample}
  graph_data(limit)  -> {nodes, edges, truncated}
  graph_query(name)  -> entity + its relations + engram refs
  delete_entity(name)-> hard-delete all relations touching the name
  delete_relation(rid)
  update_relation(rid, confidence)
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


def _rs(service):
    return getattr(service, "relation_store", None)


def _infer_type(name: str, given: str = "") -> str:
    g = (given or "").strip().lower()
    if g and g != "unknown":
        return g
    try:
        from hippocampus.semantic import _classify
        t = _classify(name or "")
        if t and t != "unknown":
            return t
    except Exception:
        pass
    return g or "unknown"


def _eid(name: str) -> str:
    # stable id derived from the (lowercased, trimmed) name
    return "ent_" + (name or "").strip().lower()


class GraphHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    # ---- nodes are derived from relation endpoints ----
    def _collect_nodes(self, rels):
        """name(lower) -> {id, name, type, mentions} from a list of Relation."""
        nodes = {}
        for r in rels:
            for nm, ty in ((r.subject, getattr(r, "subject_type", "")),
                           (r.object, getattr(r, "object_type", ""))):
                key = (nm or "").strip()
                if not key:
                    continue
                lk = key.lower()
                node = nodes.get(lk)
                if node is None:
                    nodes[lk] = {"id": _eid(key), "name": key,
                                 "type": _infer_type(key, ty), "mentions": 1}
                else:
                    node["mentions"] += 1
                    if node["type"] in ("", "unknown"):
                        node["type"] = _infer_type(key, ty)
        return nodes

    def graph_overview(self, service) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        rs = _rs(service)
        if rs is None:
            return self.utils.ok({"n_entities": 0, "n_relations": 0, "sample": []})
        try:
            rels = rs.all_active(limit=10_000_000)
        except Exception as e:
            return self.utils.error(f"all_active failed: {e!r}")
        nodes = self._collect_nodes(rels)
        sample = [{"id": n["id"], "name": n["name"], "type": n["type"]}
                  for n in list(nodes.values())[:10]]
        return self.utils.ok({
            "n_entities": len(nodes),
            "n_relations": len(rels),
            "sample": sample,
        })

    def graph_data(self, service, limit: int = 300) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        rs = _rs(service)
        if rs is None:
            return self.utils.ok({"nodes": [], "edges": [], "truncated": False})
        try:
            cap = max(1, min(int(limit), 2000))
        except Exception:
            cap = 300
        try:
            rels = rs.all_active(limit=10_000_000) or []
        except Exception as e:
            return self.utils.error(f"all_active failed: {e!r}")
        nodes_map = self._collect_nodes(rels)
        nodes = list(nodes_map.values())
        truncated = len(nodes) > cap
        nodes = nodes[:cap]
        keep = {n["name"].strip().lower() for n in nodes}
        edges = []
        for r in rels:
            s = (r.subject or "").strip().lower()
            o = (r.object or "").strip().lower()
            if not s or not o:
                continue
            if s not in keep or o not in keep:
                continue
            edges.append({
                "src": _eid(r.subject),
                "dst": _eid(r.object),
                "predicate": r.predicate or "",
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
        rs = _rs(service)
        if rs is None:
            return self.utils.error("Relation layer disabled.")
        try:
            rels = rs.relations_for(name, limit=200)
        except Exception as e:
            return self.utils.error(f"relations_for failed: {e!r}")
        if not rels:
            return self.utils.error(f"unknown entity: {name}")
        # resolve this entity's type from whichever endpoint matches
        nm_l = name.lower()
        etype = "unknown"
        for r in rels:
            if (r.subject or "").strip().lower() == nm_l:
                etype = _infer_type(r.subject, getattr(r, "subject_type", ""))
                break
            if (r.object or "").strip().lower() == nm_l:
                etype = _infer_type(r.object, getattr(r, "object_type", ""))
                break
        out_rels = []
        for r in rels:
            out_rels.append({
                "id": r.id,
                "src": r.subject,
                "predicate": r.predicate,
                "dst": r.object,
                "confidence": r.confidence,
            })
        refs = []
        try:
            src_ids = {r.source_engram_id for r in rels if r.source_engram_id}
            if src_ids:
                for en in service.store.list_active(limit=10_000_000):
                    if getattr(en, "id", None) in src_ids:
                        refs.append({
                            "id": getattr(en, "id", None),
                            "summary": (getattr(en, "summary", "") or "")[:160],
                        })
        except Exception:
            pass
        return self.utils.ok({
            "entity": {"id": _eid(name), "name": name, "type": etype},
            "relations": out_rels[:100],
            "engram_refs": refs[:100],
        })

    def delete_entity(self, service, eid: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        rs = _rs(service)
        if rs is None:
            return self.utils.error("Relation layer disabled.")
        eid = (eid or "").strip()
        if not eid:
            return self.utils.error("Missing eid.")
        # eid is "ent_<name-lower>"; recover the name to match on.
        name = eid[4:] if eid.startswith("ent_") else eid
        try:
            n = rs.delete_entity(name)
        except Exception as e:
            return self.utils.error(f"delete_entity failed: {e!r}")
        return self.utils.ok({"id": eid, "relations_removed": n})

    def delete_relation(self, service, rid: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        rs = _rs(service)
        if rs is None:
            return self.utils.error("Relation layer disabled.")
        rid = (rid or "").strip()
        if not rid:
            return self.utils.error("Missing rid.")
        try:
            ok = rs.delete_by_id(rid)
        except Exception as e:
            return self.utils.error(f"delete_relation failed: {e!r}")
        if not ok:
            return self.utils.error(f"unknown relation: {rid}")
        return self.utils.ok({"id": rid, "deleted": True})

    def update_relation(self, service, rid: str, confidence) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        rs = _rs(service)
        if rs is None:
            return self.utils.error("Relation layer disabled.")
        rid = (rid or "").strip()
        if not rid:
            return self.utils.error("Missing rid.")
        try:
            c = float(confidence)
        except Exception:
            return self.utils.error("Invalid confidence.")
        try:
            ok = rs.set_confidence(rid, c)
        except Exception as e:
            return self.utils.error(f"update_relation failed: {e!r}")
        if not ok:
            return self.utils.error(f"unknown relation: {rid}")
        return self.utils.ok({"id": rid, "confidence": max(0.0, min(1.0, c))})
