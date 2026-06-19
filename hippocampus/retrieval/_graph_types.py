"""v1.4 B4: shared types for the graph retrieval layer.
Lives in its own sub-module so that graph_retriever / graph_keyword_retriever
/ graph_vector_retriever can all import EntityMatch without forming a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..types import Entity


@dataclass
class EntityMatch:
    """One entity the graph route anchored on, plus the engrams it implies."""
    entity: Entity
    score: float
    source: str  # "keyword" / "vector" / "fused"
    engram_refs: list[str] = field(default_factory=list)
    depth: int = 0  # 0 = anchor, >0 = N-hop neighbor


__all__ = ["EntityMatch"]
