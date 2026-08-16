"""Stats handler for the page API (B9).

Single endpoint: get_stats() returns aggregate counts so the
Dashboard "system" page can show engrams / entities / fts / atoms
in a single panel.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class StatsHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def get_stats(self, service) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            store = service.store
            n_engram = len(store.list_active(limit=10_000_000))
        except Exception as e:
            n_engram = -1
        try:
            n_fts = store.fts_count()
        except Exception:
            n_fts = -1
        n_entity = 0
        if service.semantic is not None:
            try:
                n_entity = len(service.semantic.all_entities(limit=10_000_000))
            except Exception:
                n_entity = -1
        n_pending = 0
        try:
            n_pending = len(service.list_prospective("pending"))
        except Exception:
            pass
        n_fired = 0
        try:
            n_fired = len(service.list_prospective("fired"))
        except Exception:
            pass
        # B3 atom count: best-effort. AtomStore.count/count_by_type
        # avoid materializing every atom in Python.
        n_atoms = -1
        atom_breakdown: dict[str, int] = {}
        try:
            service._ensure_atom_layer()
            atom_store = getattr(service, "atom_store", None)
            if atom_store is not None:
                n_atoms = atom_store.count()
                atom_breakdown = atom_store.count_by_type()
        except Exception:
            pass

        # v1.76.5 visualization: status / importance / tier / valence /
        # stream distributions derived from one full scan.
        total_engrams = 0
        active_engrams = 0
        archived_engrams = 0
        importance_distribution = {f"{i/10:.1f}": 0 for i in range(1, 11)}
        tier_breakdown: dict[str, int] = {}
        try:
            for e in store.all(limit=10_000_000):
                total_engrams += 1
                if float(getattr(e, "forgotten_at", 0.0) or 0.0) > 0.0:
                    archived_engrams += 1
                else:
                    active_engrams += 1
                imp = max(0.0, min(1.0, float(getattr(e, "importance", 0.0) or 0.0)))
                bucket = f"{min(0.9, int(imp * 10) / 10):.1f}"
                if bucket == "0.0":
                    bucket = "0.1"
                importance_distribution[bucket] = importance_distribution.get(bucket, 0) + 1
                tier = str(getattr(e, "tier", "") or "unset")
                tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
        except Exception:
            pass
        valence_breakdown: dict[str, int] = {}
        stream_breakdown: dict[str, int] = {}
        try:
            valence_breakdown = store.valence_histogram()
            stream_breakdown = store.stream_breakdown()
        except Exception:
            pass

        n_relations = 0
        try:
            if getattr(service, "relation_store", None) is not None:
                n_relations = service.relation_store.count_active()
        except Exception:
            n_relations = -1

        return self.utils.ok({
            "engrams": n_engram,
            "fts_count": n_fts,
            "entities": n_entity,
            "relations": n_relations,
            "atoms": n_atoms,
            "pending_triggers": n_pending,
            "fired_triggers": n_fired,
            "status_breakdown": {
                "total": total_engrams,
                "active": active_engrams,
                "archived": archived_engrams,
            },
            "importance_distribution": importance_distribution,
            "tier_breakdown": tier_breakdown,
            "valence_breakdown": valence_breakdown,
            "stream_breakdown": stream_breakdown,
            "atom_breakdown": atom_breakdown,
        })
