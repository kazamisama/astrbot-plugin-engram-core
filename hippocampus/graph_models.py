"""Persistent full-graph data models (v1.76.10, livingmemory-inspired)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNodeV2:
    node_type: str
    value: str
    canonical_value: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        return f"{self.node_type}:{self.canonical_value}"


@dataclass(slots=True)
class GraphEdgeV2:
    source_key: str
    target_key: str
    relation_type: str
    source_memory_id: str
    confidence: float = 0.8
    weight: float = 1.0
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def edge_key(self) -> str:
        return f"{self.source_key}|{self.relation_type}|{self.target_key}|{self.source_memory_id}"

    @property
    def semantic_edge_key(self) -> str:
        return f"{self.source_key}|{self.relation_type}|{self.target_key}"


@dataclass(slots=True)
class GraphEntryV2:
    entry_key: str
    source_memory_id: str
    session_id: str | None
    persona_id: str | None
    scope_id: str | None
    entry_type: str
    content: str
    node_keys: list[str] = field(default_factory=list)
    relation_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractedGraphV2:
    nodes: list[GraphNodeV2] = field(default_factory=list)
    edges: list[GraphEdgeV2] = field(default_factory=list)
    entries: list[GraphEntryV2] = field(default_factory=list)
