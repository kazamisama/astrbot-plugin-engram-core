"""Reciprocal Rank Fusion.

Implements the classic RRF formula (Cormack et al. 2009):
    rrf_score(d) = sum over routes r of 1 / (k + rank_r(d))

where rank_r(d) is 1-based rank of document d in route r (1 = top hit).
Higher rrf_score = more confident. The k constant (typically 60) dampens
the impact of high ranks so that documents appearing consistently in the
middle of multiple lists still surface.

In hippocampus v1.3 this layer is shared by:
- PatternCompleter.recall (document route: vector + FTS5) - the original use
- DualRouteRetriever (document route + graph route) - the new use
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Generic, Iterable, TypeVar

RRF_K_DEFAULT = 60

T = TypeVar("T")


@dataclass
class RankedCandidate(Generic[T]):
    """One hit from a single route, before fusion."""
    item: T
    raw_score: float
    rank: int  # 1-based within the route


@dataclass
class FusedCandidate(Generic[T]):
    """One item after RRF merge across routes."""
    item: T
    rrf_score: float
    contributions: dict[str, float] = field(default_factory=dict)
    """route_name -> rrf contribution. Useful for debug / explain."""


class RRFFusion:
    """Reciprocal Rank Fusion over a list of ranked candidate lists.

    Usage:
        fusion = RRFFusion(k=60)
        fused = fusion.fuse([
            ("vector", [RankedCandidate(item=e1, raw_score=0.9, rank=1), ...]),
            ("fts",    [RankedCandidate(item=e1, raw_score=0.7, rank=3), ...]),
        ])
    """
    def __init__(self, k: int = RRF_K_DEFAULT) -> None:
        if k < 1:
            raise ValueError("RRF k must be >= 1, got " + str(k))
        self.k = k

    def fuse(self, routes: Iterable[tuple[str, list[RankedCandidate]]]) -> list[FusedCandidate]:
        # Key by a stable identity of the item, not Python id() (which can differ
        # for equal-but-rebuilt dataclass instances from different SQL rows).
        # Strategy: try `item.id` (Engram, Entity) first, then fall back to id().
        scores: dict[str, float] = {}
        contribs: dict[str, dict[str, float]] = {}
        last_item: dict[str, object] = {}
        for route_name, lst in routes:
            for cand in lst:
                key = self._key(cand.item)
                contrib = 1.0 / (self.k + cand.rank)
                scores[key] = scores.get(key, 0.0) + contrib
                contribs.setdefault(key, {})[route_name] = contrib
                last_item[key] = cand.item
        fused = [
            FusedCandidate(item=last_item[k_], rrf_score=s, contributions=contribs[k_])
            for k_, s in scores.items()
        ]
        fused.sort(key=lambda x: x.rrf_score, reverse=True)
        return fused

    @staticmethod
    def _key(item) -> str:
        # Prefer a business-stable id; fall back to str(id()) for unhashable / id-less items.
        bid = getattr(item, "id", None)
        if isinstance(bid, str) and bid:
            return "id:" + bid
        return "pyid:" + str(id(item))


def rrf_fuse(
    *ranked_lists: list[tuple[object, float]],
    k_const: int = RRF_K_DEFAULT,
) -> list[tuple[object, float]]:
    """Backwards-compatible tuple-based RRF used by the original
    PatternCompleter. Prefer the RRFFusion class for new code.

    Each ranked_list is [(item, raw_score)] sorted by raw_score desc. Returns
    [(item, rrf_score)] sorted by rrf_score desc.
    """
    fusion = RRFFusion(k=k_const)
    routes = []
    for idx, lst in enumerate(ranked_lists):
        named = [
            RankedCandidate(item=it, raw_score=sc, rank=r + 1)
            for r, (it, sc) in enumerate(lst)
        ]
        routes.append(("route_" + str(idx), named))
    return [(fc.item, fc.rrf_score) for fc in fusion.fuse(routes)]