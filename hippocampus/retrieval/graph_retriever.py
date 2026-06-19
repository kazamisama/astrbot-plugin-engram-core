# v1.4 B4: GraphRetriever -- the high-level facade for the graph route.
# Single-file layout.
# Fuses keyword + vector retrievers, then walks the graph N hops to pull in
# engrams that are transitively related to the matched entities.
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..embeddings import EmbeddingProvider
from ..graph_store import GraphStore
from ..types import Cue, Engram, Entity
from .rrf import RankedCandidate
from ._graph_types import EntityMatch
from .graph_keyword_retriever import GraphKeywordRetriever
from .graph_vector_retriever import GraphVectorRetriever

if TYPE_CHECKING:
    from ..service import MemoryService




class GraphRetriever:
    """Top-level graph retriever. Public search() returns a list of
    RankedCandidate, ready to feed the document route's RRF fusion in
    DualRouteRetriever.

    The retriever owns the GraphStore, the embedder, and the keyword /
    vector sub-retrievers. Construction takes either a MemoryService
    (convenience) or the three components directly.
    """

    def __init__(
        self,
        service: "MemoryService | None" = None,
        *,
        graph: GraphStore | None = None,
        embedder: EmbeddingProvider | None = None,
        max_hops: int = 1,
    ) -> None:
        if service is not None:
            self._service = service
            self._graph = graph if graph is not None else _build_graph_for_service(service)
            self._embedder = embedder if embedder is not None else getattr(service, "embedder", None)
        else:
            if graph is None or embedder is None:
                raise ValueError("GraphRetriever: graph and embedder are required when service is None")
            self._service = None
            self._graph = graph
            self._embedder = embedder
        self._max_hops = max(1, int(max_hops))
        self._keyword = GraphKeywordRetriever(self._graph)
        self._vector = GraphVectorRetriever(self._graph, self._embedder)

    # -- public --------------------------------------------------------

    def search(self, cue: Cue) -> list[RankedCandidate]:
        """Run keyword + vector entity retrieval, fuse, walk the graph N
        hops, hydrate engrams, return RankedCandidate per engram.

        Public signature is `search(cue) -> list[RankedCandidate]` so it can
        be slotted in as the graph route in DualRouteRetriever without
        changing the public RecallResult contract.
        """
        text = (cue.text or "").strip()
        if not text:
            return []
        # Late import: TextProcessor is v1.4 B1. Falling back to a char-level
        # split if it's not importable keeps this module's blast radius small.
        try:
            from ..processors.text_processor import TextProcessor
            tokens = TextProcessor.keyword_preprocess(text) or TextProcessor.tokenize(text)
        except Exception:
            tokens = [t for t in text.split() if t]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []

        # 1) Keyword and vector entity candidates.
        kw = self._keyword.search(tokens, k=16)
        try:
            qvec = self._embedder.embed(text) if self._embedder is not None else []
        except Exception:
            qvec = []
        vc = self._vector.search(qvec, k=16) if qvec else []

        # 2) Fuse: keep top entity by combined (max) score.
        anchors = _fuse_anchors(kw, vc)
        if not anchors:
            return []

        # 3) Walk the graph up to max_hops, collecting engram ids with
        #    depth-decayed scores.
        scored_engrams: dict[str, float] = {}
        matched_names: dict[str, str] = {}
        for anchor in anchors:
            self._expand(anchor, scored_engrams, matched_names,
                          max_hops=self._max_hops, limit=64)

        # 4) Hydrate engrams. Prefer the existing store; fall back to the
        #    service's store when standalone.
        store = self._service.store if self._service is not None else None
        if store is None:
            return []

        # 4a) Fast path: hit the GraphStore reverse index (graph_engram_refs).
        out: list[RankedCandidate] = []
        for eid, score in sorted(scored_engrams.items(), key=lambda kv: kv[1], reverse=True):
            try:
                engram = store.get(eid)  # type: ignore[attr-defined]
            except Exception:
                engram = None
            if engram is None:
                continue
            rc = RankedCandidate(item=engram, raw_score=float(score), rank=0)
            setattr(rc, "_matched_entity", matched_names.get(eid, ""))
            out.append(rc)
        if out:
            for i, c in enumerate(out):
                c.rank = i + 1
            return out

        # 4b) Fallback: the graph_engram_refs table is empty (the legacy
        #     code path wrote entity_refs onto the Engram dataclass but
        #     never mirrored it into the graph store). Walk every active
        #     engram and check `entity_refs` against the matched entities
        #     directly. This preserves v1.3 behaviour for callers that
        #     have not yet been wired to the new GraphStore.
        try:
            legacy = list(store.all(limit=10_000))  # type: ignore[attr-defined]
        except Exception:
            legacy = []
        anchor_ids = {a.entity.id for a in anchors}
        for engram in legacy:
            refs = set(getattr(engram, "entity_refs", []) or [])
            if not (refs & anchor_ids):
                continue
            # Score: anchor.score * 1 / (1 + depth_to_engram) ; depth unknown
            # so use 0 (best). Cap k later.
            best_anchor = max(
                anchors,
                key=lambda a: a.score * (1.0 if a.entity.id in refs else 0.0),
            )
            rc = RankedCandidate(item=engram, raw_score=best_anchor.score, rank=0)
            setattr(rc, "_matched_entity", best_anchor.entity.name)
            out.append(rc)
        out.sort(key=lambda c: c.raw_score, reverse=True)
        for i, c in enumerate(out):
            c.rank = i + 1
        return out

    # -- internals -----------------------------------------------------

    def _expand(
        self,
        anchor: EntityMatch,
        scored_engrams: dict[str, float],
        matched_names: dict[str, str],
        *,
        max_hops: int,
        limit: int,
    ) -> None:
        # Anchor engrams at depth 0.
        for eid in anchor.engram_refs[:limit]:
            prev = scored_engrams.get(eid, 0.0)
            scored_engrams[eid] = max(prev, anchor.score * 1.0)
            matched_names.setdefault(eid, anchor.entity.name)
        if max_hops < 1:
            return
        neighbors = self._graph.neighbors(anchor.entity.id, max_hops=max_hops)
        import math
        for nb_id, depth, _pred in neighbors:
            # depth decay
            decay = 1.0 / (1.0 + depth)
            for eid, w in self._graph.engrams_for(nb_id, limit=limit):
                prev = scored_engrams.get(eid, 0.0)
                cand = anchor.score * decay * float(w)
                if cand > prev:
                    scored_engrams[eid] = cand
                matched_names.setdefault(eid, anchor.entity.name)


def _fuse_anchors(kw: list[EntityMatch], vc: list[EntityMatch]) -> list[EntityMatch]:
    """Pick the strongest entity across the two retrievers. We keep the
    top-1 keyword anchor AND top-1 vector anchor (so a query that matches
    a name exactly is not drowned out by a vector shadow on a different
    entity). If both retrievers agreed on the same entity we merge the
    score (sum, capped at 1.0).
    """
    by_id: dict[str, EntityMatch] = {}
    for m in (kw or []):
        prev = by_id.get(m.entity.id)
        if prev is None or m.score > prev.score:
            by_id[m.entity.id] = m
    for m in (vc or []):
        prev = by_id.get(m.entity.id)
        if prev is None:
            by_id[m.entity.id] = m
        else:
            prev.score = min(1.0, prev.score + m.score)
            prev.source = "fused"
    return list(by_id.values())


def _build_graph_for_service(service) -> GraphStore:
    """Attach a GraphStore to the service's SQLite file, lazily."""
    store = getattr(service, "store", None)
    db_path = getattr(store, "_db_path", None) if store is not None else None
    if db_path is None:
        raise ValueError("GraphRetriever: cannot derive db_path from service.store")
    if not hasattr(service, "_graph_store") or service._graph_store is None:
        service._graph_store = GraphStore(db_path)
    return service._graph_store


__all__ = ["GraphRetriever", "EntityMatch"]
