"""Reconsolidation: the write-back half of recall.

v1.77.0: this module used to also hold PatternCompleter -- a second
retrieval pipeline that service.recall() actually used, while the
documented dual-route retriever (hippocampus/retrieval/dual_route.py) was
reachable only from /mem search --mode=dual and /mem debug. The two
disagreed about which config knobs existed: document_route_weight,
graph_route_weight, cross_route_bonus, dynamic_route_weighting and
recency_decay_rate were read ONLY by the path production never called, so
tuning them changed nothing that fed the prompt.

They were merged into DualRouteRetriever (mode / tier / separation /
metamemory / mood / activation / frequency all live there now) and the
duplicate implementation was deleted. Reconsolidator stayed because both
pipelines, and the service, need it.
"""
from __future__ import annotations
import time

from .types import Engram
from .config import MemoryConfig
from .storage import HippocampalStore
from hippocampus.observability import note as _obs_note


class Reconsolidator:
    """Bump access stats + strength after a recall, and re-tier the engram."""

    def __init__(self, store: HippocampalStore, cfg: MemoryConfig) -> None:
        self._store = store
        self._cfg = cfg

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
                _obs_note("recall", "touch")
        self._store.upsert(e)
