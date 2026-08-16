"""Deterministic full-graph extractor (v1.76.10)."""
from __future__ import annotations

import hashlib
from typing import Any

from .graph_models import ExtractedGraphV2, GraphEdgeV2, GraphEntryV2, GraphNodeV2


def canonicalize(value: str) -> str:
    return (value or "").strip().casefold()


def _entry_key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


class GraphExtractorV2:
    def __init__(self, *, max_topics: int = 6, max_persons: int = 8,
                 max_facts: int = 8) -> None:
        self.max_topics = max_topics
        self.max_persons = max_persons
        self.max_facts = max_facts

    def extract(self, engram, *, name_map: dict[str, str] | None = None,
                facts: list[str] | None = None) -> ExtractedGraphV2:
        graph = ExtractedGraphV2()
        summary = (engram.summary or engram.content or "").strip()
        topics = list(dict.fromkeys([t for t in (engram.topics or []) if t]))[: self.max_topics]
        persons: list[str] = []
        if name_map:
            persons = list(dict.fromkeys(str(v) for v in name_map.values() if v))[: self.max_persons]
        if not persons:
            try:
                from .semantic import _classify
                persons = list(dict.fromkeys(
                    str(x) for x in (engram.entities or [])
                    if x and _classify(str(x)) == "person"))[: self.max_persons]
            except Exception:
                persons = []
        fact_values = [str(x).strip() for x in (facts or []) if str(x).strip()][: self.max_facts]
        if not fact_values and summary:
            fact_values = [summary]

        node_map: dict[str, GraphNodeV2] = {}

        def add_node(node_type: str, value: str, meta: dict | None = None) -> str:
            key = f"{node_type}:{canonicalize(value)}"
            if key not in node_map and canonicalize(value):
                node_map[key] = GraphNodeV2(
                    node_type=node_type, value=value.strip(),
                    canonical_value=canonicalize(value), metadata=meta or {})
            return key

        topic_keys = [add_node("topic", t) for t in topics]
        person_keys = [add_node("person", p, {"name_map": bool(name_map)})
                       for p in persons]
        fact_keys = [add_node("fact", f, {"summary": summary[:240]})
                     for f in fact_values]
        topic_keys = [k for k in topic_keys if k in node_map]
        person_keys = [k for k in person_keys if k in node_map]
        fact_keys = [k for k in fact_keys if k in node_map]
        graph.nodes = list(node_map.values())

        scope = (getattr(engram, "scope_id", "") or "") or None
        meta_base = {
            "canonical_summary": summary[:500],
            "importance": float(getattr(engram, "importance", 0.5) or 0.5),
            "memory_type": getattr(engram, "memory_type", "episodic"),
        }

        def add_entry(entry_type: str, content: str, keys: list[str],
                      relation_type: str | None = None,
                      confidence: float = 0.8) -> None:
            graph.entries.append(GraphEntryV2(
                entry_key=_entry_key(entry_type, engram.id, relation_type or "",
                                     *keys, content),
                source_memory_id=engram.id,
                session_id=getattr(engram, "session_id", "") or None,
                persona_id=getattr(engram, "persona_id", "") or None,
                scope_id=scope,
                entry_type=entry_type,
                content=content[:1200],
                node_keys=keys,
                relation_type=relation_type,
                metadata={**meta_base, "graph_confidence": confidence}))

        for key in fact_keys:
            add_entry("fact", f"Fact: {node_map[key].value}. Summary: {summary}",
                      [key], "fact", 0.9)
        for key in topic_keys:
            add_entry("topic", f"Topic: {node_map[key].value}. Summary: {summary}",
                      [key], "topic", 0.75)
        for key in person_keys:
            add_entry("participant",
                      f"Participant: {node_map[key].value}. Summary: {summary}",
                      [key], "participant", 0.7)

        def add_edge_entry(edge: GraphEdgeV2, relation: str, summary_text: str) -> None:
            src = node_map.get(edge.source_key)
            dst = node_map.get(edge.target_key)
            if not src or not dst:
                return
            content = (f"{src.value} {relation} {dst.value}. "
                       f"Summary: {summary_text}")
            graph.entries.append(GraphEntryV2(
                entry_key=_entry_key("edge", engram.id, relation,
                                     edge.source_key, edge.target_key,
                                     content),
                source_memory_id=engram.id,
                session_id=getattr(engram, "session_id", "") or None,
                persona_id=getattr(engram, "persona_id", "") or None,
                scope_id=scope,
                entry_type="edge",
                content=content[:1200],
                node_keys=[edge.source_key, edge.target_key],
                relation_type=relation,
                metadata={**meta_base, "graph_confidence": edge.confidence}))

        for tkey in topic_keys:
            for fkey in fact_keys:
                edge = GraphEdgeV2(tkey, fkey, "describes", engram.id,
                                   confidence=0.82,
                                   metadata={"summary": summary[:240]})
                graph.edges.append(edge)
                add_edge_entry(edge, "describes", summary)

        for pkey in person_keys:
            for fkey in fact_keys:
                edge = GraphEdgeV2(pkey, fkey, "mentioned_in", engram.id,
                                   confidence=0.88,
                                   metadata={"summary": summary[:240]})
                graph.edges.append(edge)
                add_edge_entry(edge, "mentioned_in", summary)

        for i, first in enumerate(person_keys):
            for second in person_keys[i + 1:]:
                edge = GraphEdgeV2(first, second, "co_occurs_with",
                                   engram.id, confidence=0.7,
                                   metadata={"summary": summary[:240]})
                graph.edges.append(edge)
                add_edge_entry(edge, "co_occurs_with", summary)

        if not graph.entries and summary:
            key = add_node("summary", summary)
            if key in node_map:
                graph.nodes = list(node_map.values())
                graph.entries.append(GraphEntryV2(
                    entry_key=_entry_key("summary", engram.id, key, summary),
                    source_memory_id=engram.id,
                    session_id=getattr(engram, "session_id", "") or None,
                    persona_id=getattr(engram, "persona_id", "") or None,
                    scope_id=scope,
                    entry_type="summary",
                    content=f"Summary: {summary}",
                    node_keys=[key],
                    relation_type="summary",
                    metadata={**meta_base, "graph_confidence": 0.6}))
        return graph
