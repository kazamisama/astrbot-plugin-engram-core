from __future__ import annotations
import time
import math
from .types import Cue, Engram, RecallResult
from .config import MemoryConfig
from .storage import HippocampalStore
from .embeddings import EmbeddingProvider

_RRF_K = 60  # classic constant from RRF paper

class Reconsolidator:
    def __init__(self, store: HippocampalStore, cfg: MemoryConfig) -> None:
        self._store = store; self._cfg = cfg
    def touch(self, e: Engram) -> None:
        now = time.time()
        e.access_count += 1
        e.last_accessed = now
        e.reconsolidation_lock_until = now + self._cfg.reconsolidation_lock_seconds
        e.strength = min(1.0, e.strength + 0.05)
        if getattr(self._cfg, "tiering_enabled", False):
            try:
                from .tiering import classify
                e.tier = classify(e, self._cfg, now)
            except Exception:
                pass
        self._store.upsert(e)


def _rrf_fuse(*ranked_lists: list[tuple[Engram, float]], k_const: int = _RRF_K) -> list[tuple[Engram, float]]:
    """Reciprocal Rank Fusion. Each ranked_list is a list of (engram, raw_score)
    sorted by raw_score desc. Returns merged list sorted by RRF score desc."""
    scores: dict[str, float] = {}
    last: dict[str, Engram] = {}
    for lst in ranked_lists:
        for rank, (e, _raw) in enumerate(lst):
            scores[e.id] = scores.get(e.id, 0.0) + 1.0 / (k_const + rank + 1)
            last[e.id] = e
    merged = [(last[eid], s) for eid, s in scores.items()]
    merged.sort(key=lambda x: x[1], reverse=True)
    return merged


class PatternCompleter:
    def __init__(self, store: HippocampalStore, embedder: EmbeddingProvider,
                 cfg: MemoryConfig, reconsolidator: Reconsolidator) -> None:
        self._store = store; self._embed = embedder
        self._cfg = cfg; self._recon = reconsolidator

    def recall(self, cue: Cue, *, embedding_model: str | None = None) -> RecallResult:
        mode = (cue.mode or "hybrid").lower()
        if mode not in ("vector", "fts", "hybrid"):
            mode = "hybrid"

        vec: list[tuple[Engram, float]] = []
        fts: list[tuple[Engram, float]] = []
        candidate_k = self._cfg.recall_candidate_k

        if mode in ("vector", "hybrid"):
            qvec = self._embed.embed(cue.text)
            vec = self._store.vector_search(
                qvec, k=candidate_k,
                actor_id=cue.actor_id, channel_id=cue.channel_id,
                persona_id=cue.persona_id, scope_id=cue.scope_id,
                memory_types=cue.memory_types,
                embedding_model=embedding_model)

        if mode in ("fts", "hybrid"):
            fts = self._store.fts_search(
                cue.text, k=candidate_k,
                actor_id=cue.actor_id, channel_id=cue.channel_id,
                persona_id=cue.persona_id, scope_id=cue.scope_id,
                memory_types=cue.memory_types,
                embedding_model=embedding_model)

        if mode == "vector":
            fused = vec
        elif mode == "fts":
            fused = fts
        else:
            fused = _rrf_fuse(vec, fts)

        # v1.76.4: defense-in-depth exclusion of soft-forgotten engrams.
        # vector_search/fts_search already filter them, but any future
        # retrieval source feeding this method must never resurrect a
        # forgotten row (soft_forget keeps embedding_json + fts_text for
        # audit/recovery, so a missing filter here is silent data leakage).
        fused = [(e, sc) for e, sc in fused if float(getattr(e, "forgotten_at", 0.0) or 0.0) <= 0.0]

        # v1.13: hot/warm/cold tier routing. Normal recall uses hot+warm;
        # cold is held back and only merged in as a fallback when hot+warm
        # under-deliver (or when the operator opts to always include cold).
        if getattr(self._cfg, "tiering_enabled", False):
            from .tiering import TieringEngine
            eng = TieringEngine(self._store, self._cfg)
            hot_warm, cold = eng.split_candidates(fused)
            include_cold = bool(getattr(self._cfg, "tier_recall_include_cold", False))
            min_hits = int(getattr(self._cfg, "tier_cold_fallback_min_hits", 1) or 0)
            if include_cold:
                fused = hot_warm + cold
            elif len(hot_warm) < max(0, min_hits):
                fused = hot_warm + cold
            else:
                fused = hot_warm

        # 时序/强度/topic 重排
        #
        # v1.76.18: honour the configured weights from the config.
        #
        # This path used to hardcode `0.55*base + 0.25*strength + 0.15*recency`
        # while the config advertised `score_alpha` / `score_beta` /
        # `score_gamma` ("检索相关性权重 / 重要性权重 / 时间新鲜度权重"). Those
        # knobs were only read by a different recall method, so tuning them had
        # no effect on the injection path -- the one that actually feeds the
        # prompt.
        #
        # They could not simply be plugged in either: `base` is an RRF score
        # whose magnitude is ~1/(60+rank+1), about 0.016 at best, so with
        # alpha=0.5 relevance would contribute ~0.008 against a 0.25 strength
        # term. Relevance is therefore scaled to 0..1 first, putting it on the
        # same footing as strength and recency; the weights are normalised to
        # sum to 1 so the base score stays within 0..1.
        #
        # NOTE (measured, not assumed): on the live store this is
        # behaviour-neutral -- six real probes hit the expected memory under
        # both the old constants and these weights (5/6 either way). It is a
        # consistency fix so the documented knobs work, NOT a recall-quality
        # fix. An earlier suspicion that ranking was burying correct memories
        # turned out to be a test artifact: the probes queried
        # persona_id="mortis" for memories owned by the "sherri" persona, which
        # persona isolation correctly excludes.
        now = time.time()
        scored = []
        mood_on = self._cfg.mood_congruence_enabled and cue.valence_hint is not None
        act_on = bool(cue.activation)
        freq_w = float(getattr(self._cfg, "frequency_recall_weight", 0.0) or 0.0)
        alpha = float(getattr(self._cfg, "score_alpha", 0.5) or 0.0)
        beta = float(getattr(self._cfg, "score_beta", 0.25) or 0.0)
        gamma = float(getattr(self._cfg, "score_gamma", 0.25) or 0.0)
        if alpha <= 0.0 and beta <= 0.0 and gamma <= 0.0:
            alpha, beta, gamma = 0.5, 0.25, 0.25
        _tot = alpha + beta + gamma
        if _tot > 0.0:
            alpha, beta, gamma = alpha / _tot, beta / _tot, gamma / _tot
        base_max = max((b for _, b in fused), default=0.0)
        for e, base in fused:
            age = max(0.0, now - e.created_at)
            recency = 1.0 / (1.0 + age / 3600.0)
            relevance = (base / base_max) if base_max > 0.0 else 0.0
            score = alpha * relevance + beta * e.strength + gamma * recency
            if cue.topics and any(t in (e.topics or []) for t in cue.topics):
                score += 0.05
            # v1.1: mood-congruent recall (Bower 1981) - same-valence engrams get a small boost
            if mood_on:
                mood_match = 1.0 - min(1.0, abs(float(cue.valence_hint) - float(e.valence)) / 2.0)
                score += self._cfg.mood_congruence_weight * mood_match
            # v1.1: spreading-activation boost - caller passed in a precomputed activation map
            if act_on:
                score += self._cfg.activation_score_weight * float(cue.activation.get(e.id, 0.0))
            if freq_w:
                score += freq_w * (math.log1p(max(0, e.access_count or 0)) / 4.0)
            scored.append((e, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:cue.k]
        top_map = {e.id: s for e, s in top}
        for e, _ in top:
            self._recon.touch(e)
        # DG cluster expansion: pull in 1-hop similar_to siblings
        engrams_out: list[Engram] = []
        scores_out: list[float] = []
        if self._cfg.enable_separation and top:
            from .separation import PatternSeparator
            expanded = PatternSeparator.expand_cluster(
                [e for e, _ in top],
                fetch=self._store.get,
                max_total=max(cue.k * 2, cue.k + 4))
            for e, factor, origin in expanded:
                engrams_out.append(e)
                base = top_map.get(e.id) or top_map.get(origin, 0.0)
                scores_out.append(base * factor)
        else:
            engrams_out = [e for e, _ in top]
            scores_out = [s for _, s in top]
        confidences_out = None
        if self._cfg.metamemory_enabled:
            from .metamemory import recall_confidence
            top_score = max(scores_out) if scores_out else 0.0
            confidences_out = [
                recall_confidence(e, s, top_score, self._cfg, now=now)
                for e, s in zip(engrams_out, scores_out)
            ]
        return RecallResult(engrams=engrams_out, scores=scores_out,
                            confidences=confidences_out)