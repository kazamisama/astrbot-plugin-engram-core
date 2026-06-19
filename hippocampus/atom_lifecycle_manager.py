# v1.4 B3: AtomLifecycleManager -- extract / promote / merge / decay.
# Single-file layout. No I/O of its own; delegates persistence to AtomStore.
from __future__ import annotations

import time
from typing import Iterable

from .types import (
    MemoryAtom,
    AtomStatus,
    AtomType,
    DecayType,
)
from .memory_atom_models import make_fact_atom, make_preference_atom


# Default decay multipliers per DecayType. preference is the slowest to
# forget, episodic is the fastest. Kept here (not in types.py) so callers
# can override per environment without monkey-patching the enum.
DEFAULT_DECAY_MULTIPLIERS: dict[str, float] = {
    DecayType.EPISODIC.value: 1.0,
    DecayType.SEMANTIC.value: 4.0,
    DecayType.PREFERENCE.value: 8.0,
}


class AtomLifecycleManager:
    """Lifecycle operations on MemoryAtom instances.

    Persistence is delegated to a caller-supplied `AtomStore`; this
    manager is intentionally store-agnostic so it can be unit-tested
    without a real database.
    """

    def __init__(self, store) -> None:  # store: AtomStore, kept untyped to avoid import cycle
        self._store = store

    # -- extract -------------------------------------------------------

    def extract_atoms_from_engram(
        self,
        engram,
        semantic,
    ) -> list[MemoryAtom]:
        """Pull atoms out of a fresh engram using an `EntityExtractor`-like
        `semantic` object. Best-effort: a missing `extract_atoms` method on
        the extractor is treated as "no atoms", and unknown payload keys
        are skipped silently.

        Expected extractor API (v1.4):
            semantic.extract_atoms(engram) -> list[dict]
        where each dict has at least: subject, predicate, object. Optional:
            kind, confidence, importance, decay_type, attributes.
        """
        extractor = getattr(semantic, "extract_atoms", None)
        if not callable(extractor):
            return []
        try:
            raw = extractor(engram) or []
        except Exception:
            return []
        if not isinstance(raw, list):
            return []

        out: list[MemoryAtom] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject") or "").strip()
            predicate = str(item.get("predicate") or "").strip()
            obj = str(item.get("object") or "").strip()
            if not (subject and predicate and obj):
                continue
            kind = item.get("kind") or AtomType.FACT.value
            confidence = float(item.get("confidence", 0.7))
            importance = float(item.get("importance", 0.5))
            decay_type = item.get("decay_type") or DecayType.SEMANTIC.value
            attributes = item.get("attributes") or {}

            if kind == AtomType.PREFERENCE.value:
                atom = make_preference_atom(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    source_engram_id=engram.id,
                    confidence=confidence,
                    actor_id=engram.actor_id or "",
                    platform=engram.platform or "",
                    channel_id=engram.channel_id or "",
                    importance=importance,
                    attributes=attributes,
                )
            else:
                atom = make_fact_atom(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    source_engram_id=engram.id,
                    confidence=confidence,
                    actor_id=engram.actor_id or "",
                    platform=engram.platform or "",
                    channel_id=engram.channel_id or "",
                    importance=importance,
                    decay_type=decay_type,
                    attributes=attributes,
                )
            out.append(atom)
        return out

    # -- promote -------------------------------------------------------

    def promote(self, atom: MemoryAtom, importance_delta: float = 0.0, strength_delta: float = 0.0) -> MemoryAtom:
        """Bump importance / strength on a hit. Clamps to [0, 1] for importance."""
        atom.importance = min(1.0, max(0.0, float(atom.importance) + float(importance_delta)))
        atom.strength = max(0.0, float(atom.strength) + float(strength_delta))
        atom.last_seen = time.time()
        if self._store is not None:
            self._store.upsert(atom)
        return atom

    # -- merge evidence ------------------------------------------------

    def merge_evidence(self, a: MemoryAtom, b: MemoryAtom) -> MemoryAtom:
        """Merge `b` into `a` (in place on `a`) using MemoryAtom.merge, then
        persist. Returns `a`. Caller should not reuse `b` afterwards.
        """
        a.merge(b)
        if self._store is not None:
            self._store.upsert(a)
        return a

    # -- decay ---------------------------------------------------------

    def decay_pass(
        self,
        tau_base: float = 86400.0,
        floor: float = 0.05,
        decay_type_multiplier: dict[str, float] | None = None,
    ) -> int:
        """One sweep of forgetting: exponential decay on every active atom.

        `strength_new = strength * exp(-dt / (tau_base * multiplier))`

        Atoms whose strength drops below `floor` are soft-deleted (status
        moves to `soft_forgotten`). Returns the number of atoms touched
        (persisted) by this pass.

        `tau_base` units are seconds. With the default multipliers:
          - episodic:   tau = 1 day
          - semantic:   tau = 4 days
          - preference: tau = 8 days
        """
        mult = dict(DEFAULT_DECAY_MULTIPLIERS)
        if decay_type_multiplier:
            mult.update(decay_type_multiplier)
        now = time.time()
        active = self._store.all(status=AtomStatus.ACTIVE.value) if self._store is not None else []
        touched = 0
        for atom in active:
            mult_for_type = mult.get(atom.decay_type, 1.0)
            if mult_for_type <= 0:
                # Treat a 0 / negative multiplier as "no decay" for safety.
                continue
            tau = float(tau_base) * float(mult_for_type)
            dt = max(0.0, now - float(atom.last_seen))
            try:
                import math
                factor = math.exp(-dt / tau)
            except OverflowError:
                factor = 0.0
            atom.strength = max(0.0, float(atom.strength) * factor)
            if self._store is not None:
                # Narrow writes: upsert() merges by max(existing, caller) which
                # would keep the pre-decay strength alive. The decay pass is the
                # one path that owns strength mutations, so we go through the
                # dedicated write_strength / set_status channels.
                self._store.write_strength(atom.id, atom.strength)
                if atom.strength < float(floor):
                    atom.status = AtomStatus.SOFT_FORGOTTEN.value
                    self._store.set_status(atom.id, atom.status)
            else:
                if atom.strength < float(floor):
                    atom.status = AtomStatus.SOFT_FORGOTTEN.value
            touched += 1
        return touched

    # ---- v1.4.x: async maintenance loop (参考 livingmemory) ----

    async def _maintenance_loop(self, decay_interval: float, gc_interval: float) -> None:
        # Sleep the smaller of the two intervals between passes. The
        # _stop_event is set by stop() to break out cleanly.
        import asyncio
        if not hasattr(self, "_stop_event") or self._stop_event is None:
            self._stop_event = asyncio.Event()
        intervals = [x for x in (decay_interval, gc_interval) if x > 0]
        if not intervals:
            return
        sleep = min(intervals)
        # Track when each pass last ran so we don't double-fire on every tick.
        import time
        last_decay = time.monotonic()
        last_gc = time.monotonic()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep)
                # Event was set -> exit.
                return
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if decay_interval > 0 and (now - last_decay) >= decay_interval:
                try:
                    self.decay_pass()
                except Exception:
                    pass
                last_decay = now
            if gc_interval > 0 and (now - last_gc) >= gc_interval:
                try:
                    self._store.gc_pass() if self._store is not None else 0
                except Exception:
                    pass
                last_gc = now

    def start(self, decay_interval: float = 0.0, gc_interval: float = 0.0) -> None:
        # Start the background maintenance loop. Idempotent: calling
        # start() twice does not spawn a second task.
        import asyncio
        if not hasattr(self, "_stop_event"):
            self._stop_event = asyncio.Event()
            self._task = None
        if self._task is not None and not self._task.done():
            return
        if decay_interval <= 0 and gc_interval <= 0:
            # Nothing to do; skip task creation.
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._maintenance_loop(decay_interval, gc_interval)
        )

    async def stop(self) -> None:
        # Signal the loop to exit and wait for the task to finish.
        if not hasattr(self, "_stop_event"):
            return
        self._stop_event.set()
        t = getattr(self, "_task", None)
        if t is not None:
            try:
                await t
            except Exception:
                pass
            self._task = None

    def run_decay(self) -> int:
        # Synchronous, on-demand decay pass. Useful for tests and for
        # callers that don't want a background loop.
        return self.decay_pass()

    def run_gc(self, floor: float = 0.05, min_age_seconds: float = 0.0) -> int:
        # Synchronous GC sweep.
        if self._store is None:
            return 0
        return self._store.gc_pass(floor=floor, min_age_seconds=min_age_seconds)


__all__ = ["AtomLifecycleManager", "DEFAULT_DECAY_MULTIPLIERS"]
