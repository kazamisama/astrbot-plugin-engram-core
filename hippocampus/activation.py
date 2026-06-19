"""v1.1 spreading activation over the entity-relation-engram graph.

Implements a depth-bounded Collins & Loftus (1975) style activation spread:
  - Seed a set of entity / engram nodes
  - Each iteration, propagate activation to neighbors with weight * decay
  - Stop at floor or max_depth
  - Returns a node-key -> activation map (keys are prefixed 'e:' or 'n:')

Two graph views are used:
  entity (e:<id>) --predicate--> entity: relations table, weight = relation.confidence
  entity (e:<id>) --> engram: engrams.entity_refs contains the entity id, weight 1.0
  engram (n:<id>) --> entity: same source, weight 1.0
  engram (n:<id>) --> engram: engrams.similar_to, weight 0.6
"""
from __future__ import annotations
from typing import Iterable

from .config import MemoryConfig
from .semantic import SemanticStore
from .storage import HippocampalStore


# Node key prefixes
E_PREFIX = "e:"
N_PREFIX = "n:"

# Default edge weights
W_RELATION = 1.0          # multiplier base; actual edge weight is relation.confidence
W_RELATION_REVERSE = 0.4  # walking object->subject is weaker
W_ENTITY_TO_ENGRAM = 1.0
W_ENGRAM_TO_ENTITY = 1.0
W_ENGRAM_SIMILAR = 0.6


class SpreadingActivation:
    def __init__(self, semantic_store: SemanticStore, store: HippocampalStore,
                 cfg: MemoryConfig) -> None:
        self._sem = semantic_store
        self._store = store
        self._cfg = cfg

    # ---------- public ----------
    def activate(self, seeds: Iterable[str], *, depth: int | None = None,
                 decay: float | None = None, floor: float | None = None
                ) -> dict[str, float]:
        """Spread activation from the seed nodes. Seeds can be entity names
        (matched case-insensitively) or engram ids (with or without 'n:' prefix).
        Returns {node_key: activation} where node_key is 'e:<id>' or 'n:<id>'.
        """
        d = int(depth if depth is not None else self._cfg.activation_max_depth)
        k = float(decay if decay is not None else self._cfg.activation_decay)
        fl = float(floor if floor is not None else self._cfg.activation_floor)
        if d <= 0 or k <= 0.0:
            return {}
        acts: dict[str, float] = {}
        frontier: dict[str, float] = {}
        for s in seeds:
            key = self._resolve_seed(s)
            if key is None:
                continue
            acts[key] = max(acts.get(key, 0.0), 1.0)
            frontier[key] = max(frontier.get(key, 0.0), 1.0)
        for _ in range(d):
            nxt: dict[str, float] = {}
            for node, act in frontier.items():
                if act < fl:
                    continue
                for nbr, w in self._neighbors(node):
                    contrib = act * w * k
                    if contrib < fl:
                        continue
                    new_total = acts.get(nbr, 0.0) + contrib
                    acts[nbr] = min(1.0, new_total)
                    nxt[nbr] = max(nxt.get(nbr, 0.0), contrib)
            frontier = nxt
            if not frontier:
                break
        return acts

    def surface(self, activations: dict[str, float], top_k: int = 10
                ) -> list[tuple[str, float]]:
        """Sort activation map by score desc; keep top_k. Each item is (node_key, act)."""
        items = [(k, v) for k, v in activations.items() if v > 0.0]
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:top_k]

    def engram_activation(self, activations: dict[str, float]
                         ) -> dict[str, float]:
        """Project the activation map to engram-id -> activation (drops entities)."""
        out: dict[str, float] = {}
        for k, v in activations.items():
            if k.startswith(N_PREFIX):
                eid = k[len(N_PREFIX):]
                out[eid] = max(out.get(eid, 0.0), v)
        return out

    def explain(self, activations: dict[str, float], top_k: int = 8) -> list[str]:
        """Render the top activated nodes for /mem activate output."""
        lines: list[str] = []
        for key, act in self.surface(activations, top_k=top_k):
            tag, name = self._label(key)
            lines.append("  " + tag + " " + name + "  act=" + str(round(act, 3)))
        return lines

    # ---------- internals ----------
    def _resolve_seed(self, s: str) -> str | None:
        if not s:
            return None
        s = s.strip()
        if not s:
            return None
        # Direct engram id?
        if s.startswith(N_PREFIX):
            eid = s[len(N_PREFIX):]
            return s if self._store.get(eid) is not None else None
        if self._store.get(s) is not None:
            return N_PREFIX + s
        # Try entity name lookup
        ent = self._sem.find_entity_by_name(s)
        if ent is not None:
            return E_PREFIX + ent.id
        # Case-insensitive entity search via search_entities
        matches = self._sem.search_entities(s, limit=1)
        if matches:
            return E_PREFIX + matches[0].id
        return None

    def _neighbors(self, node: str) -> list[tuple[str, float]]:
        if node.startswith(E_PREFIX):
            return self._neighbors_entity(node[len(E_PREFIX):])
        if node.startswith(N_PREFIX):
            return self._neighbors_engram(node[len(N_PREFIX):])
        return []

    def _neighbors_entity(self, eid: str) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        rels = self._sem.relations_of(eid)
        for r in rels:
            if r.subject_id == eid and r.object_id and r.object_id != eid:
                out.append((E_PREFIX + r.object_id, max(0.1, float(r.confidence)) * W_RELATION))
            elif r.object_id == eid and r.subject_id and r.subject_id != eid:
                out.append((E_PREFIX + r.subject_id, max(0.1, float(r.confidence)) * W_RELATION_REVERSE))
        # entities -> engrams they are referenced from
        for e in self._store.all(limit=10_000_000):
            if e.forgotten_at > 0:
                continue
            if eid in (e.entity_refs or []):
                out.append((N_PREFIX + e.id, W_ENTITY_TO_ENGRAM))
        return out

    def _neighbors_engram(self, eid: str) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        e = self._store.get(eid)
        if e is None or e.forgotten_at > 0:
            return out
        for ref in (e.entity_refs or []):
            out.append((E_PREFIX + ref, W_ENGRAM_TO_ENTITY))
        for sib in (e.similar_to or []):
            if sib and sib != eid:
                out.append((N_PREFIX + sib, W_ENGRAM_SIMILAR))
        return out

    def _label(self, key: str) -> tuple[str, str]:
        if key.startswith(E_PREFIX):
            ent = self._sem.get_entity(key[len(E_PREFIX):])
            return ("e:", ent.name if ent else key[len(E_PREFIX):][:8])
        if key.startswith(N_PREFIX):
            e = self._store.get(key[len(N_PREFIX):])
            if e is None:
                return ("n:", key[len(N_PREFIX):][:8])
            return ("n:", (e.summary or e.content)[:40])
        return ("?", key)
