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
        # B3 atom count: best-effort. AtomStore.list_active exists
        # post-B3; if the layer is not initialized we just return -1.
        n_atoms = -1
        try:
            atom_layer = getattr(service, "_atom_layer", None)
            if atom_layer is not None:
                store = getattr(atom_layer, "store", None)
                if store is not None and hasattr(store, "list_active"):
                    n_atoms = len(store.list_active(limit=10_000_000))
        except Exception:
            pass
        return self.utils.ok({
            "engrams": n_engram,
            "fts_count": n_fts,
            "entities": n_entity,
            "atoms": n_atoms,
            "pending_triggers": n_pending,
            "fired_triggers": n_fired,
        })
