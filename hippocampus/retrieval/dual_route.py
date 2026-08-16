"""Dual-route retrieval: document route + graph route + RRF merge.

The v1.62 retrieval architecture for hippocampus:

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
              +-------+   +----------+   +----------+
                      v   v           v
                    RRFFusion   (spread route:
                       |         activation-ranked)
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
    SPREAD = "spread"
    ATOM = "atom"


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
    # v1.63: MMR diversity rerank. After RRF fusion, apply Maximal
    # Marginal Relevance to reduce redundancy in the top-k.
    # lambda=1.0 = pure relevance, lambda=0.0 = pure diversity.
    # Uses similar_to links as the diversity distance metric
    # (lightweight; no embedding load required).
    mmr_enabled: bool = True
    mmr_lambda: float = 0.75
    # v1.62: spreading-activation route (pre-excitation + context spread).
    # When enabled, the spread route contributes activation-ranked engrams
    # as a third input to RRF fusion alongside document + graph routes.
    spread_route_enabled: bool = True
    spread_candidate_k: int = 16
    # RRF k constant (lower = more weight to top ranks)
    rrf_k: int = 60
    # v1.76.5: final score = alpha*retrieval + beta*importance + gamma*recency
    score_alpha: float = 0.5
    score_beta: float = 0.25
    score_gamma: float = 0.25
    recency_decay_rate: float = 0.01
    # v1.76.5: dynamic document/graph route weighting + cross-route bonus
    document_route_weight: float = 0.65
    graph_route_weight: float = 0.35
    cross_route_bonus: float = 0.08
    dynamic_route_weighting: bool = True


@dataclass
class RouteHit:
    """A single (engram, route_kind, raw_score) hit. Used for explain()."""
    engram: Engram
    route: RouteKind
    raw_score: float
    rrf_contribution: float
    matched_entity: str | None = None
    """If this hit came from the graph route, which entity triggered it."""


@dataclass
class _ScoredCandidate:
    item: Engram
    score: float
    breakdown: dict


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

    def _route_maps(self, hits: list[RankedCandidate]) -> tuple[dict, float]:
        raw_by_id = {}
        max_raw = 0.0
        for cand in hits:
            eid = getattr(cand.item, "id", None) or str(id(cand.item))
            raw_by_id[eid] = float(cand.raw_score)
            max_raw = max(max_raw, float(cand.raw_score))
        return raw_by_id, (max_raw or 1.0)

    def _route_weights_for_query(self, query: str) -> tuple[float, float, str]:
        """Lightweight query-intent rules, mirroring livingmemory."""
        doc_w = float(self.cfg.document_route_weight)
        graph_w = float(self.cfg.graph_route_weight)
        if not self.cfg.dynamic_route_weighting:
            return doc_w, graph_w, "fixed"
        q = (query or "").casefold()
        intent = "default"
        relation_terms = ("谁", "和谁", "关系", "认识", "朋友", "同事", "同学", "家人",
                          "老师", "partner", "friend", "relationship", "with whom")
        temporal_terms = ("上次", "昨天", "前天", "刚才", "之前", "什么时候", "哪天",
                          "最近", "last time", "yesterday", "recently", "when")
        factual_terms = ("是什么", "什么是", "解释", "定义", "怎么", "如何",
                         "why", "what is", "explain", "define", "how to")
        rel = any(t in q for t in relation_terms)
        tmp = any(t in q for t in temporal_terms)
        fac = any(t in q for t in factual_terms)
        if rel:
            graph_w += 0.2; doc_w -= 0.2; intent = "relationship"
        if tmp:
            graph_w += 0.1; doc_w -= 0.1
            intent = "temporal" if intent == "default" else intent + "+temporal"
        if fac and not rel:
            doc_w += 0.15; graph_w -= 0.15
            intent = "factual" if intent == "default" else intent + "+factual"
        doc_w = max(0.15, min(0.9, doc_w))
        graph_w = max(0.1, min(0.85, graph_w))
        total = doc_w + graph_w
        if total <= 0:
            return float(self.cfg.document_route_weight), float(self.cfg.graph_route_weight), "fixed"
        return doc_w / total, graph_w / total, intent

    def _rank(self, cue: Cue) -> list[_ScoredCandidate]:
        import math as _math
        import time as _time
        doc_hits = self._document_route(cue)
        graph_hits = self._graph_route(cue)
        spread_hits = self._spread_route(cue)
        atom_hits = self._atom_route(cue)
        routes: list[tuple[str, list]] = [("document", doc_hits)]
        if graph_hits or not self.cfg.skip_empty_graph_route:
            routes.append(("graph", graph_hits))
        if spread_hits:
            routes.append(("spread", spread_hits))
        if atom_hits:
            routes.append(("atom", atom_hits))
        fusion = RRFFusion(k=self.cfg.rrf_k)
        fused = fusion.fuse(routes)
        if not fused:
            return []

        doc_map, doc_max = self._route_maps(doc_hits)
        graph_map, graph_max = self._route_maps(graph_hits)
        spread_map, spread_max = self._route_maps(spread_hits)
        atom_map, atom_max = self._route_maps(atom_hits)
        max_rrf = max(fc.rrf_score for fc in fused) or 1.0
        doc_w, graph_w, intent = self._route_weights_for_query(cue.text)
        alpha = float(self.cfg.score_alpha)
        beta = float(self.cfg.score_beta)
        gamma = float(self.cfg.score_gamma)
        decay = float(self.cfg.recency_decay_rate)
        bonus = float(self.cfg.cross_route_bonus)
        now = _time.time()

        ranked: list[_ScoredCandidate] = []
        for fc in fused:
            e = fc.item
            eid = getattr(e, "id", None) or str(id(e))
            doc_sig = (doc_map.get(eid, 0.0) / doc_max) if doc_hits else 0.0
            graph_sig = (graph_map.get(eid, 0.0) / graph_max) if graph_hits else 0.0
            spread_sig = (spread_map.get(eid, 0.0) / spread_max) if spread_hits else 0.0
            atom_sig = (atom_map.get(eid, 0.0) / atom_max) if atom_hits else 0.0
            if doc_hits or graph_hits:
                retrieval = doc_w * doc_sig + graph_w * graph_sig
            else:
                retrieval = spread_sig
            retrieval = max(retrieval, atom_sig * 0.5)
            if retrieval <= 0.0:
                retrieval = fc.rrf_score / max_rrf
            importance = min(1.0, max(0.0, float(getattr(e, "importance", 0.5) or 0.5)))
            ref = max(float(getattr(e, "last_accessed", 0.0) or 0.0),
                      float(getattr(e, "created_at", now) or now))
            days = max(0.0, (now - ref) / 86400.0)
            recency = _math.exp(-decay * days)
            cross = bonus if (doc_sig > 0.0 and graph_sig > 0.0) else 0.0
            final = min(1.0, alpha * retrieval + beta * importance + gamma * recency + cross)
            ranked.append(_ScoredCandidate(item=e, score=final, breakdown={
                "retrieval": round(retrieval, 4),
                "importance": round(importance, 4),
                "recency": round(recency, 4),
                "cross_route_bonus": round(cross, 4),
                "document_weight": round(doc_w, 4),
                "graph_weight": round(graph_w, 4),
                "query_intent": intent,
                "rrf_score": round(fc.rrf_score, 6),
                "final_score": round(final, 4),
            }))
        ranked.sort(key=lambda x: x.score, reverse=True)
        if self.cfg.mmr_enabled and len(ranked) > 1:
            ranked = self._mmr_rerank_ranked(ranked, max(1, cue.k))
        return ranked

    def search(self, cue: Cue) -> RecallResult:
        """Run all routes, apply weighted scoring + MMR, return top-k."""
        ranked = self._rank(cue)
        top = ranked[: max(1, cue.k)]
        engrams = [sc.item for sc in top]
        scores = [sc.score for sc in top]
        try:
            recon = getattr(self._service, "reconsolidator", None)
            if recon is not None:
                for e in engrams:
                    recon.touch(e)
        except Exception:
            pass
        return RecallResult(engrams=engrams, scores=scores, confidences=None)

    def _mmr_rerank_ranked(self, candidates: list[_ScoredCandidate], k: int) -> list[_ScoredCandidate]:
        """MMR over final weighted scores using similar_to as distance proxy."""
        if len(candidates) <= max(1, k):
            return list(candidates)
        lamb = float(self.cfg.mmr_lambda)
        selected: list[_ScoredCandidate] = []
        remaining = list(candidates)
        sim_index = {}
        for c in remaining:
            eid = getattr(c.item, "id", None) or str(id(c.item))
            sim_index[eid] = set(getattr(c.item, "similar_to", None) or [])
        for _ in range(max(1, k)):
            best = None; best_score = -999.0
            for c in remaining:
                div_penalty = 0.0
                cid = getattr(c.item, "id", "")
                for sel in selected:
                    sid = getattr(sel.item, "id", "")
                    if cid in sim_index.get(sid, set()):
                        div_penalty += 0.5
                    if sid in sim_index.get(cid, set()):
                        div_penalty += 0.5
                mmr = lamb * c.score - (1.0 - lamb) * div_penalty
                if mmr > best_score:
                    best_score = mmr; best = c
            if best is None:
                break
            selected.append(best); remaining.remove(best)
        return selected

    async def asearch(self, cue: Cue) -> RecallResult:
        """Async variant: delegate to search() so spread/atom/weighting/scope
        behaviour can never drift from the sync path."""
        return await asyncio.to_thread(self.search, cue)

    def explain(self, cue: Cue) -> list[RouteHit]:
        """Diagnostic: returns the per-route hits with rrf contribution
        broken out. Includes all routes that search() would consider
        (document + graph + spread), so the diagnostic matches the
        actual retrieval path. Useful for /mem debug (B14) and tests.

        Latent fix (v1.64 B14): prior to this, explain() only fused
        document + graph, while search() additionally fused spread.
        This caused the diagnostic to silently under-report hits when
        spread contributed. Now explain() mirrors search()'s routes
        tuple construction exactly.
        """
        doc_hits = self._document_route(cue)
        graph_hits = self._graph_route(cue)
        spread_hits = self._spread_route(cue)
        atom_hits = self._atom_route(cue)
        # Build the same routes tuple as search() so the diagnostic
        # attribution matches the live retrieval path.
        routes: list[tuple[str, list]] = [("document", doc_hits)]
        if graph_hits or not self.cfg.skip_empty_graph_route:
            routes.append(("graph", graph_hits))
        if spread_hits:
            routes.append(("spread", spread_hits))
        if atom_hits:
            routes.append(("atom", atom_hits))
        fusion = RRFFusion(k=self.cfg.rrf_k)
        fused = fusion.fuse(routes)
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
        for cand in spread_hits:
            item_id = getattr(cand.item, "id", None) or str(id(cand.item))
            fc = by_id.get(item_id)
            if fc is None:
                continue
            out.append(RouteHit(
                engram=cand.item, route=RouteKind.SPREAD,
                raw_score=cand.raw_score,
                rrf_contribution=fc.contributions.get("spread", 0.0),
            ))
        for cand in atom_hits:
            item_id = getattr(cand.item, "id", None) or str(id(cand.item))
            fc = by_id.get(item_id)
            if fc is None:
                continue
            out.append(RouteHit(
                engram=cand.item, route=RouteKind.ATOM,
                raw_score=cand.raw_score,
                rrf_contribution=fc.contributions.get("atom", 0.0),
            ))
        out.sort(key=lambda h: h.rrf_contribution, reverse=True)
        return out

    # --- diversity -------------------------------------------------
    def _mmr_rerank(self, candidates: list, k: int) -> list:
        """Maximal Marginal Relevance: iteratively select the best
        candidate that balances relevance (RRF score) with diversity
        (dissimilarity to already-selected items).

        Similarity is measured via the `similar_to` graph: if
        candidate X lists Y in its similar_to, they are considered
        similar (penalty weight = 0.5 per link). This is a lightweight
        proxy for full embedding cosine distance.
        """
        lamb = self.cfg.mmr_lambda
        if len(candidates) <= max(1, k):
            return list(candidates)
        selected: list = []
        remaining = list(candidates)
        # Pre-index similar_to for quick lookup
        sim_index: dict = {}
        for c in remaining:
            eid = getattr(c.item, 'id', str(id(c.item)))
            sims = set(getattr(c.item, 'similar_to', None) or [])
            sim_index[eid] = sims
        for _ in range(k):
            best = None
            best_score = -999.0
            for c in remaining:
                rel = c.rrf_score if hasattr(c, 'rrf_score') else c.raw_score
                div_penalty = 0.0
                for s in selected:
                    sid = getattr(s.item, 'id', '')
                    cid = getattr(c.item, 'id', '')
                    if cid in sim_index.get(sid, set()):
                        div_penalty += 0.5
                    if sid in sim_index.get(cid, set()):
                        div_penalty += 0.5
                mmr = lamb * rel - (1.0 - lamb) * div_penalty
                if mmr > best_score:
                    best_score = mmr
                    best = c
            if best is None:
                break
            selected.append(best)
            remaining.remove(best)
        return selected

    # --- route implementations -----------------------------------------
    def _atom_route(self, cue: Cue) -> list[RankedCandidate]:
        """v1.76.5: atom-level keyword route. Maps active, temporally
        fresh atoms back to their parent engrams."""
        svc = self._service
        if not getattr(svc.cfg, "enable_atom_extraction", False):
            return []
        try:
            svc._ensure_atom_layer()
        except Exception:
            return []
        atom_store = getattr(svc, "atom_store", None)
        if atom_store is None:
            return []
        q = (cue.text or "").strip().lower()
        if not q:
            return []
        atoms = atom_store.search_text(q, limit=40)
        tokens = [t for t in q.split() if t]
        by_engram: dict[str, float] = {}
        now = __import__("time").time()
        for atom in atoms:
            try:
                temporal = float(atom.temporal_score(now))
            except Exception:
                temporal = 0.0
            if temporal <= 0.0:
                continue
            text = " ".join([atom.subject, atom.predicate, atom.object]).lower()
            rel = sum(1 for t in tokens if t in text) / max(1, len(tokens)) if tokens else 0.5
            atom_score = rel * temporal * float(atom.confidence or 0.5) * float(atom.strength or 0.5)
            if atom_score <= 0.0:
                continue
            try:
                atom_store.touch(atom.atom_id)
            except Exception:
                pass
            for eid in (atom.source_engram_ids or []):
                parent = svc.store.get(eid)
                if cue.persona_id is not None:
                    if parent is None or (getattr(parent, "persona_id", "") or "") != cue.persona_id:
                        continue
                if (cue.scope_id is not None and parent is not None
                        and (getattr(parent, "scope_id", "") or "")
                        != cue.scope_id):
                    continue
                by_engram[eid] = max(by_engram.get(eid, 0.0), atom_score)
        items: list[tuple[Engram, float]] = []
        for eid, sc in by_engram.items():
            e = svc.store.get(eid)
            if e is None or float(getattr(e, "forgotten_at", 0.0) or 0.0) > 0.0:
                continue
            items.append((e, sc))
        items.sort(key=lambda x: x[1], reverse=True)
        return [RankedCandidate(item=e, raw_score=s, rank=i + 1)
                for i, (e, s) in enumerate(items[:self.cfg.graph_candidate_k])]

    def _spread_route(self, cue: Cue) -> list[RankedCandidate]:
        """v1.62: build RankedCandidate list from pre-computed
        spreading-activation map in cue.activation.

        The activation map is populated upstream by
        MemoryService.recall_with_activation() which seeds
        SpreadingActivation from matched entities, recent-access
        engrams, and high-importance priors. Here we translate that
        map into ranked candidates for RRF fusion.

        Returns empty list when spread_route_enabled=False or no
        activation map is available.
        """
        if not self.cfg.spread_route_enabled:
            return []
        act_map = getattr(cue, "activation", None) or {}
        if not act_map:
            return []
        items: list[tuple[Engram, float]] = []
        store = self._service.store
        for eid, act in act_map.items():
            engram = store.get(eid)
            if engram is None or engram.forgotten_at > 0:
                continue
            if cue.persona_id is not None and (
                    (getattr(engram, "persona_id", "") or "") != cue.persona_id):
                continue
            if cue.scope_id is not None and (
                    (getattr(engram, "scope_id", "") or "") != cue.scope_id):
                continue
            items.append((engram, float(act)))
        items.sort(key=lambda x: x[1], reverse=True)
        k = self.cfg.spread_candidate_k
        return [
            RankedCandidate(item=e, raw_score=s, rank=i + 1)
            for i, (e, s) in enumerate(items[:k])
        ]

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
                persona_id=cue.persona_id, scope_id=cue.scope_id,
                memory_types=cue.memory_types)
        except Exception:
            pass
        try:
            fts_pairs = store.fts_search(
                cue.text, k=k, actor_id=cue.actor_id, channel_id=cue.channel_id,
                persona_id=cue.persona_id, scope_id=cue.scope_id,
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