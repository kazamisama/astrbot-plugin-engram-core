"""v1.2 metamemory layer: feeling-of-knowing / recall confidence.

This is the "do I actually know this?" meta-layer on top of recall. A recalled
engram gets a confidence in [0, 1] derived from how consolidated it is
(strength), how often it has been retrieved (access_count, log-saturated), how
recently it was laid down / touched (recency), and how strongly the current cue
matched it (the recall score, normalized against the top hit).

Bjork & Bjork style: retrieval strength != storage strength. Here `strength` is
the storage trace and the recall score is the momentary retrieval strength; FOK
blends them.
"""
from __future__ import annotations
import math, time
from .config import MemoryConfig
from .types import Engram


def _recency_factor(e: Engram, now: float) -> float:
    ref = max(e.last_accessed or 0.0, e.created_at)
    age = max(0.0, now - ref)
    # half-ish life of a day; 0..1, 1 == just touched
    return 1.0 / (1.0 + age / 86400.0)


def _access_factor(e: Engram) -> float:
    # log saturation: 0 hits -> 0, ~10 hits -> ~0.7, asymptote 1
    return min(1.0, math.log1p(max(0, e.access_count)) / math.log(12.0))


def recall_confidence(e: Engram, score: float, top_score: float,
                      cfg: MemoryConfig, now: float | None = None) -> float:
    """Blend stored confidence + storage strength + retrieval evidence into a
    single feeling-of-knowing value in [0, 1]."""
    now = now if now is not None else time.time()
    rel = 0.0
    if top_score and top_score > 0:
        rel = max(0.0, min(1.0, score / top_score))
    recency = _recency_factor(e, now)
    access = _access_factor(e)
    base_conf = max(0.0, min(1.0, e.confidence))
    w = cfg.metamemory_weights
    fok = (w.get("stored", 0.20) * base_conf
           + w.get("strength", 0.30) * max(0.0, min(1.0, e.strength))
           + w.get("retrieval", 0.30) * rel
           + w.get("recency", 0.10) * recency
           + w.get("access", 0.10) * access)
    return max(0.0, min(1.0, fok))


def confidence_label(c: float, cfg: MemoryConfig) -> str:
    """Human-facing bucket for a confidence value."""
    if c >= cfg.metamemory_high_threshold:
        return "high"
    if c >= cfg.metamemory_low_threshold:
        return "medium"
    return "low"


def is_tip_of_tongue(c: float, score: float, cfg: MemoryConfig) -> bool:
    """Tip-of-the-tongue: the cue clearly matched something (non-trivial score)
    but our feeling-of-knowing stays under the low threshold -> 'I recall
    something but I'm not sure'."""
    return score > 0.0 and c < cfg.metamemory_low_threshold