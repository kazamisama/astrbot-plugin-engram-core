"""Retrieval layer (v1.3).

Split out of hippocampus/recall.py for v1.3 dual-route support.
- rrf.py                 Reciprocal Rank Fusion algorithm + result dataclass
- dual_route.py          Document route + graph route + RRF merge

The original `recall.py` PatternCompleter still works; it now delegates to
retrieval.rrf.rrf_fuse for the merge step.
"""
from .rrf import (
    RRFFusion,
    RankedCandidate,
    FusedCandidate,
    rrf_fuse,
    RRF_K_DEFAULT,
)
from .dual_route import DualRouteRetriever, DualRouteConfig, RouteKind
from ._graph_types import EntityMatch
from .graph_retriever import GraphRetriever
from .graph_keyword_retriever import GraphKeywordRetriever
from .graph_vector_retriever import GraphVectorRetriever

__all__ = [
    "RRFFusion",
    "RankedCandidate",
    "FusedCandidate",
    "rrf_fuse",
    "RRF_K_DEFAULT",
    "DualRouteRetriever",
    "DualRouteConfig",
    "RouteKind",
    "GraphRetriever",
    "EntityMatch",
    "GraphKeywordRetriever",
    "GraphVectorRetriever",
]