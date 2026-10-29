from __future__ import annotations
import math, time
from .types import Engram
from .config import MemoryConfig
from .storage import HippocampalStore, _cos


class DecayScheduler:
    def __init__(self, cfg: MemoryConfig) -> None: self._cfg = cfg

    def step(self, engrams: list[Engram]) -> list[Engram]:
        now = time.time()
        alive: list[Engram] = []
        for e in engrams:
            tau = self._cfg.decay_tau_base * (1.0 + 4.0 * e.importance)
            dt = max(0.0, now - max(e.last_accessed or 0.0, e.created_at))
            e.strength = max(0.0, e.strength * (math.exp(-dt / tau)))
            if e.strength >= self._cfg.decay_floor:
                alive.append(e)
        return alive


class ReplayConsolidator:
    """One pass: dedupe-merge + promote (episodic -> semantic) + SWR replay + decay.

    v1.78.0: the similar-to cluster chain (cluster gists and the
    episodic->semantic profile-fact abstraction that fed on it) was removed
    together with Engram.similar_to. Under the default summary pipeline
    nothing ever wrote those links, and no recall path consumed them.
    """

    def __init__(self, store: HippocampalStore, cfg: MemoryConfig) -> None:
        self._store = store
        self._cfg = cfg

    def step(self) -> dict:
        """Full consolidation pass -- the manual /mem consolidate path.

        Dedupe-merge is an O(n * consolidation_max_pairs) cosine scan, so it
        is NOT part of the background sweep; maintenance() is the cheap
        recurring subset (see service._run_maintenance).
        """
        engrams = self._store.all(limit=10_000)
        merged = self._merge_duplicates(engrams)
        promoted = self._promote()
        replayed = self._swr_replay()
        archived = self._decay()
        return {"merged": merged, "promoted": promoted, "archived": archived,
                "replayed": replayed}

    def maintenance(self) -> dict:
        """Cheap recurring subset, run by the background maintenance sweep.

        v1.77.0: the sweep used to call ONLY the LLM consolidation manager,
        which is disabled by default -- so promotion and SWR replay never ran
        at all and promoted_at stayed empty on the live store for months.
        Both are cheap; the pairwise dedupe merge stays on the manual/replay
        path, where its O(n*k) cosine scan is paid deliberately.
        """
        return {"promoted": self._promote(), "replayed": self._swr_replay()}

    def _merge_duplicates(self, engrams) -> int:
        merged = 0
        seen: set[str] = set()
        for i, a in enumerate(engrams):
            if a.id in seen: continue
            for b in engrams[i+1: i+1+self._cfg.consolidation_max_pairs]:
                if b.id in seen: continue
                if _cos(a.embedding, b.embedding) >= self._cfg.pattern_separation_threshold:
                    a.content = (a.content + "\n---\n" + b.content).strip()
                    a.summary = (a.summary or a.content)[:120]
                    a.supersedes = list(dict.fromkeys([*a.supersedes, b.id]))
                    a.strength = min(1.0, a.strength + b.strength)
                    a.importance = min(1.0, a.importance + 0.05)
                    self._store.delete(b.id); seen.add(b.id); merged += 1
            self._store.upsert(a)
        return merged

    def _promote(self) -> int:
        if not self._cfg.enable_promotion:
            return 0
        promoted = 0
        now = time.time()
        for e in self._store.all(limit=10_000):
            if (e.memory_type == "episodic"
                    and e.access_count >= self._cfg.promote_min_access
                    and e.importance >= self._cfg.promote_min_importance):
                e.memory_type = "semantic"
                e.promoted_at = now
                self._store.upsert(e)
                promoted += 1
        return promoted

    def _swr_replay(self) -> int:
        replayed = 0
        boost = self._cfg.replay_boost
        for e in self._store.iter_for_replay(k=64):
            e.strength = min(1.0, e.strength + boost)
            e.access_count = (e.access_count or 0) + 1
            self._store.upsert(e)
            replayed += 1
        return replayed

    def _decay(self) -> int:
        alive = DecayScheduler(self._cfg).step(self._store.all(limit=10_000))
        kept_ids = {e.id for e in alive}
        archived = 0
        for e in self._store.all(limit=10_000):
            if e.id not in kept_ids:
                self._store.delete(e.id)
                archived += 1
        return archived
