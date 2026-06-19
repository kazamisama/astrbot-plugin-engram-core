"""Dual-route retrieval: document route + graph route + RRF merge.

The v1.3 retrieval architecture for hippocampus:

    query ----+----------------------+
              |                      |
              v                      v
     +------------------+    +------------------+
     | document route   |    | graph route      |
     | (vector+FTS5     |    | (entity match +  |
     |  hybrid)         |    |  1-hop relations)|
     +------------------+    +------------------+
              |                      |
              v                      v
        RankedCandidate         RankedCandidate
              |                      |
              +-------+   +----------+
                      v   v
                    RRFFusion
                       |
                       v
               FusedCandidate list

Why two routes?
- Document route:    high precision on lexical + semantic similarity
- Graph route:       high recall on entity-anchored facts ("the user told me
                     they live in Shanghai" - the city is an entity, the
                     fact is a relation, the engram is a node)

Why RRF over weighted sum?
- The two routes have incommensurable raw score scales (cosine distance vs.
  entity match count). RRF is rank-based so it needs no calibration and is
  robust to outliers in either route.

Behaviour:
- If a route returns nothing, the other route still contributes fully.
- Items appearing in both routes get a natural boost (RRF sums).
- The result list is sorted by rrf_score desc, ties broken by item id.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

from ..types import Cue, Engram, RecallResult
from .rrf import RRFFusion, RankedCandidate, FusedCandidate

if TYPE_CHECKING:
    from ..service import MemoryService


class RouteKind(str, Enum):
    DOCUMENT = "document"
    GRAPH = "graph"


@dataclass
class DualRouteConfig:
    """Tunables for dual-route retrieval."""
    # route weights are *not* used at the RRF level (RRF is unweighted).
    # They are exposed here for future score-blending experiments; for now
    # they only affect the candidate_k multiplier per route.
    document_candidate_k: int = 32
    graph_candidate_k: int = 16
    # How deep to walk the graph from a matched entity
    graph_relation_hops: int = 1
    # If the graph route returns no entity matches, skip it entirely
    skip_empty_graph_route: bool = True
    # RRF k constant (lower = more weight to top ranks)
    rrf_k: int = 60


@dataclass
class RouteHit:
    """A single (engram, route_kind, raw_score) hit. Used for explain()."""
    engram: Engram
    route: RouteKind
    raw_score: float
    rrf_contribution: float
    matched_entity: str | None = None
    """If this hit came from the graph route, which entity triggered it."""


class DualRouteRetriever:
    """Coordinates document + graph retrieval routes and merges via RRF.

    Construct with a MemoryService. The retriever borrows the service's
    cfg for candidate_k caps and pulls from store / semantic directly.

    Result of search() is RecallResult with scores replaced by rrf_score
    (still 0..~1 range, comparable across queries).
    """
    def __init__(self, service: "MemoryService", cfg: DualRouteConfig | None = None) -> None:
        self._service = service
        self.cfg = cfg or DualRouteConfig()

    def search(self, cue: Cue) -> RecallResult:
        """Run both routes synchronously. Returns RecallResult sorted by rrf_score."""
        doc_hits = self._document_route(cue)
        graph_hits = self._graph_route(cue)
        if self.cfg.skip_empty_graph_route and not graph_hits:
            routes = [("document", doc_hits)]
        else:
            routes = [("document", doc_hits), ("graph", graph_hits)]
        fusion = RRFFusion(k=self.cfg.rrf_k)
        fused = fusion.fuse(routes)
        # trim to k
        top = fused[: max(1, cue.k)]
        engrams = [fc.item for fc in top]
        scores = [fc.rrf_score for fc in top]
        return RecallResult(engrams=engrams, scores=scores, confidences=None)

    async def asearch(self, cue: Cue) -> RecallResult:
        """Async variant: runs both routes concurrently via asyncio.to_thread.
        The routes themselves are CPU-light (SQLite calls) so this is mainly
        useful when callers are already inside an event loop.
        """
        doc_task = asyncio.to_thread(self._document_route, cue)
        graph_task = asyncio.to_thread(self._graph_route, cue)
        doc_hits, graph_hits = await asyncio.gather(doc_task, graph_task)
        if self.cfg.skip_empty_graph_route and not graph_hits:
            routes = [("document", doc_hits)]
        else:
            routes = [("document", doc_hits), ("graph", graph_hits)]
        fusion = RRFFusion(k=self.cfg.rrf_k)
        fused = fusion.fuse(routes)
        top = fused[: max(1, cue.k)]
        return RecallResult(
            engrams=[fc.item for fc in top],
            scores=[fc.rrf_score for fc in top],
            confidences=None,
        )

    def explain(self, cue: Cue) -> list[RouteHit]:
        """Diagnostic: returns the per-route hits with rrf contribution
        broken out. Useful for /mem search debug and tests."""
        doc_hits = self._document_route(cue)
        graph_hits = self._graph_route(cue)
        fusion = RRFFusion(k=self.cfg.rrf_k)
        fused = fusion.fuse([("document", doc_hits), ("graph", graph_hits)])
        by_id: dict[str, FusedCandidate] = {id(fc.item) and getattr(fc.item, "id", None) or str(id(fc.item)): fc for fc in fused}
        out: list[RouteHit] = []
        for cand in doc_hits:
            item_id = getattr(cand.item, "id", None) or str(id(cand.item))
            fc = by_id.get(item_id)
            if fc is None:
                continue
            out.append(RouteHit(
                engram=cand.item, route=RouteKind.DOCUMENT,
                raw_score=cand.raw_score,
                rrf_contribution=fc.contributions.get("document", 0.0),
            ))
        for cand in graph_hits:
            item_id = getattr(cand.item, "id", None) or str(id(cand.item))
            fc = by_id.get(item_id)
            if fc is None:
                continue
            out.append(RouteHit(
                engram=cand.item, route=RouteKind.GRAPH,
                raw_score=cand.raw_score,
                rrf_contribution=fc.contributions.get("graph", 0.0),
                matched_entity=getattr(cand, "_matched_entity", None),
            ))
        out.sort(key=lambda h: h.rrf_contribution, reverse=True)
        return out

    # --- route implementations -----------------------------------------
    def _document_route(self, cue: Cue) -> list[RankedCandidate]:
        """Vector + FTS5 hybrid, RRF-merged into a single ranked list."""
        embedder = self._service.embedder
        store = self._service.store
        k = self.cfg.document_candidate_k
        vec_pairs: list[tuple[Engram, float]] = []
        fts_pairs: list[tuple[Engram, float]] = []
        try:
            qvec = embedder.embed(cue.text)
            vec_pairs = store.vector_search(
                qvec, k=k, actor_id=cue.actor_id, channel_id=cue.channel_id,
                memory_types=cue.memory_types)
        except Exception:
            pass
        try:
            fts_pairs = store.fts_search(
                cue.text, k=k, actor_id=cue.actor_id, channel_id=cue.channel_id,
                memory_types=cue.memory_types)
        except Exception:
            pass
        fusion = RRFFusion(k=self.cfg.rrf_k)
        merged = fusion.fuse([
            ("vector", [RankedCandidate(item=e, raw_score=s, rank=i + 1)
                        for i, (e, s) in enumerate(vec_pairs)]),
            ("fts",    [RankedCandidate(item=e, raw_score=s, rank=i + 1)
                        for i, (e, s) in enumerate(fts_pairs)]),
        ])
        return [
            RankedCandidate(item=fc.item, raw_score=fc.rrf_score, rank=i + 1)
            for i, fc in enumerate(merged)
        ]

    def _graph_route(self, cue: Cue) -> list[RankedCandidate]:
        """Entity match + 1-hop relation -> engram candidates.

        v1.4 B4: delegates to GraphRetriever, which uses GraphStore for an
        O(matches) entity->engram lookup instead of scanning every engram.
        The public signature is preserved (list[RankedCandidate]) so the
        RRF fusion in search() is unaffected.
        """
        if self._service.semantic is None:
            return []
        graph_retriever = getattr(self._service, "_graph_retriever", None)
        if graph_retriever is None:
            from .graph_retriever import GraphRetriever
            graph_retriever = GraphRetriever(self._service, max_hops=self.cfg.graph_relation_hops)
            self._service._graph_retriever = graph_retriever
        # Cue.k caps the candidate count; fall back to graph_candidate_k
        # when the caller didn't specify a smaller window.
        k = max(1, min(self.cfg.graph_candidate_k * 4, cue.k or self.cfg.graph_candidate_k * 4))
        return graph_retriever.search(cue)[:k]