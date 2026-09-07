"""tiering: hot / warm / cold memory tiers (v1.13, memori-inspired).

memori marks each atom ACTIVE / DORMANT / ARCHIVED and only retrieves
ACTIVE rows; decay + TTL drive the transitions. We bring the same idea to
Engram, but keep it non-destructive and derived from data engram already
tracks (strength / last_accessed / created_at), so no engram is ever lost:

  - hot : recently accessed AND still strong -> always recalled first.
  - warm: not hot but not stale -> recalled normally.
  - cold: stale / weak -> excluded from normal recall, kept in the DB and
          only pulled in as a fallback when hot+warm under-deliver.

`classify()` is a pure function of an engram + config + now, so the tier
can be recomputed any time (recall, ingest, background sweep) without
trusting a possibly-stale stored value. The stored `tier` field is just a
cache/index for fast filtering and observability.
"""
from __future__ import annotations
import time

HOT = "hot"
WARM = "warm"
COLD = "cold"
TIERS = (HOT, WARM, COLD)

_DAY = 86400.0


def classify(e, cfg, now: float | None = None) -> str:
    """Return the tier for engram `e` under `cfg`. Pure / side-effect free.

    A soft-forgotten engram (forgotten_at > 0) is always cold. Otherwise:
    recent + strong -> hot; within the warm age window -> warm; else cold.
    Age is measured from last_accessed when available, else created_at, so
    a freshly created (never-recalled) engram still counts as recent.
    """
    n = time.time() if now is None else now
    if getattr(e, "forgotten_at", 0.0):
        return COLD
    last = float(getattr(e, "last_accessed", 0.0) or 0.0)
    created = float(getattr(e, "created_at", 0.0) or 0.0)
    ref = last if last > 0 else created
    # No usable timestamp -> treat as fresh (age 0). Otherwise measure from
    # ref, clamped to >= 0 so future/skewed timestamps just read as fresh.
    age_days = 0.0 if ref <= 0 else max(0.0, (n - ref) / _DAY)
    strength = float(getattr(e, "strength", 0.0) or 0.0)

    hot_age = float(getattr(cfg, "tier_hot_max_age_days", 3.0))
    hot_str = float(getattr(cfg, "tier_hot_min_strength", 0.5))
    warm_age = float(getattr(cfg, "tier_warm_max_age_days", 30.0))
    cold_floor = float(getattr(cfg, "tier_cold_strength_floor", 0.1))

    if strength < cold_floor:
        return COLD
    if age_days <= hot_age and strength >= hot_str:
        return HOT
    if age_days <= warm_age:
        return WARM
    return COLD


class TieringEngine:
    """Recall-side tier routing + background reclassification over the store."""

    def __init__(self, store, cfg) -> None:
        self._store = store
        self._cfg = cfg

    # ---- recall-side routing ----
    def split_candidates(self, scored, now: float | None = None):
        """Split [(engram, score), ...] into (hot_warm, cold) by live tier."""
        n = time.time() if now is None else now
        hot_warm = []
        cold = []
        for e, sc in scored:
            t = classify(e, self._cfg, n)
            (cold if t == COLD else hot_warm).append((e, sc))
        return hot_warm, cold

    # ---- background sweep ----
    def reclassify_all(self, limit: int = 1_000_000) -> dict:
        """Recompute + persist the cached tier for every engram. Returns a
        {hot, warm, cold, changed} count dict. Never deletes anything.

        v1.76.15: one SQL UPDATE instead of materializing the engrams
        table and per-row upserting (which also re-ran FTS triggers).
        """
        now = time.time()
        counts = {HOT: 0, WARM: 0, COLD: 0, "changed": 0}
        hot_age = float(getattr(self._cfg, "tier_hot_max_age_days", 3.0))
        warm_age = float(getattr(self._cfg, "tier_warm_max_age_days", 30.0))
        hot_str = float(getattr(self._cfg, "tier_hot_min_strength", 0.5))
        cold_floor = float(getattr(self._cfg, "tier_cold_strength_floor", 0.1))
        # age expression mirrors classify(): ref = last_accessed if >0 else
        # created_at; a zero/absent ref is treated as age 0.
        age_expr = (
            "CASE WHEN COALESCE(last_accessed, 0.0) > 0.0 "
            "OR COALESCE(created_at, 0.0) > 0.0 "
            "THEN (? - CASE WHEN COALESCE(last_accessed, 0.0) > 0.0 "
            "THEN last_accessed ELSE created_at END) / 86400.0 "
            "ELSE 0.0 END"
        )
        tier_expr = (
            "CASE "
            "WHEN COALESCE(forgotten_at, 0.0) > 0.0 THEN ? "
            "WHEN COALESCE(strength, 0.0) < ? THEN ? "
            f"WHEN {age_expr} <= ? AND COALESCE(strength, 0.0) >= ? THEN ? "
            f"WHEN {age_expr} <= ? THEN ? "
            "ELSE ? END"
        )
        try:
            with self._store._lock, self._store._conn:
                before = self._store._conn.total_changes
                self._store._conn.execute(
                    "UPDATE engrams SET tier = " + tier_expr,
                    (now, cold_floor, COLD, now, hot_age, hot_str, HOT,
                     now, warm_age, WARM, COLD))
                changed = int(self._store._conn.total_changes) - int(before)
                rows = self._store._conn.execute(
                    "SELECT tier, COUNT(*) AS c FROM engrams GROUP BY tier"
                ).fetchall()
        except Exception as ex:
            print("[hippocampus] tier reclassify sql error: " + repr(ex))
            return counts
        for r in rows:
            key = str(r["tier"] or "")
            if key in counts:
                counts[key] = int(r["c"])
        counts["changed"] = max(0, changed)
        return counts
