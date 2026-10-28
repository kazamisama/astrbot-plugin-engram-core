"""Retrieval layer (v1.3).

Split out of hippocampus/recall.py for v1.3 dual-route support.
- rrf.py                 Reciprocal Rank Fusion algorithm + result dataclass
- dual_route.py          Document route + graph route + RRF merge

v1.77.0: DualRouteRetriever is now the ONLY retrieval pipeline. The
original PatternCompleter (recall.py) was merged into it and deleted --
its tier routing, DG expansion, metamemory confidences, mood/activation/
frequency bonuses and cue.mode handling all live in dual_route.py.
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