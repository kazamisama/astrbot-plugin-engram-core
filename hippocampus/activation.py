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
                 cfg: MemoryConfig, graph_store=None) -> None:
        self._sem = semantic_store
        self._store = store
        self._cfg = cfg
        # v1.42: optional GraphStore for batch entity->engram lookups.
        # Falls back to the legacy O(N) scan when None so old callers
        # keep working. New code (service.recall path) should pass it
        # or assign it after lazy graph_store initialization.
        self._graph = graph_store

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

    # ---------- v1.42: context-aware activation ----------
    def build_context_seeds(self, *, matched_entity_ids=None, actor_id=None,
                            session_id: str = "",
                            persona_id: str | None = None,
                            scope_id: str | None = None,
                            high_importance_count: int = 5,
                            recent_count: int = 3,
                            recent_min_strength: float = 0.5,
                            session_count: int = 3) -> list:
        """Build a list of seed node keys for activate(), incorporating
        three signal sources:

        1. Explicit entity matches (from the graph retriever); primary
           signal; activation_seed = 1.0.
        2. The user's recently accessed high-strength engrams; context
           signal; pre-excites items the user has been thinking
           about recently. Maps to hippocampal pre-excitation bias
        3. Same-session engrams; context signal; recent engrams from
           this conversation bubble up naturally.
        4. The user's (or global) high-importance engrams; personal engrams; context
           signal; pre-excites items the user has been thinking about
           recently. Maps to hippocampal pre-excitation bias on engram
           cell allocation.
        3. The user's (or global) high-importance engrams; personal
           priors; surfaced after context seeds but still fire.

        Returns a list of 'e:<id>' and 'n:<id>' keys ready to feed
        activate(). Empty if nothing matches.
        """
        seeds: list = []
        def _pass(e):
            if e is None or float(getattr(e, "forgotten_at", 0.0) or 0.0) > 0.0:
                return False
            if persona_id is not None and (getattr(e, "persona_id", "") or "") != persona_id:
                return False
            if scope_id is not None and (getattr(e, "scope_id", "") or "") != scope_id:
                return False
            return True
        for eid in (matched_entity_ids or []):
            if eid:
                seeds.append(E_PREFIX + eid)
        if actor_id and recent_count > 0:
            try:
                recent = self._store.recent_for_actor(
                    actor_id, k=recent_count, min_strength=recent_min_strength)
            except Exception:
                recent = []
            for e in recent:
                if _pass(e):
                    seeds.append(N_PREFIX + e.id)
        if session_id and session_count > 0:
            try:
                sess = self._store.recent_for_session(
                    session_id, k=session_count)
            except Exception:
                sess = []
            for e in sess:
                if not _pass(e):
                    continue
                key = N_PREFIX + e.id
                if key not in seeds:
                    seeds.append(key)
        if high_importance_count > 0:
            try:
                top = self._store.top_by_importance(
                    min_importance=self._cfg.importance_floor_for_long_term,
                    k=high_importance_count,
                    actor_id=actor_id,
                )
            except Exception:
                top = []
            for e in top:
                if not _pass(e):
                    continue
                key = N_PREFIX + e.id
                if key not in seeds:
                    seeds.append(key)
        return seeds

    def activate_with_context(self, *, matched_entity_ids=None, actor_id=None,
                              depth=None, decay=None, floor=None,
                              high_importance_count: int = 5,
                              recent_count: int = 3,
                              persona_id: str | None = None,
                              scope_id: str | None = None) -> dict:
        """One-shot helper: build context seeds, run activation, return
        the engram-only activation map (id -> [0,1]).

        Designed to be called from the main recall path right before
        PatternCompleter.recall(); the result drops into cue.activation.
        """
        seeds = self.build_context_seeds(
            matched_entity_ids=matched_entity_ids,
            actor_id=actor_id,
            high_importance_count=high_importance_count,
            recent_count=recent_count,
            persona_id=persona_id,
            scope_id=scope_id,
        )
        if not seeds:
            return {}
        acts = self.activate(seeds, depth=depth, decay=decay, floor=floor)
        out = self.engram_activation(acts)
        if persona_id is None and scope_id is None:
            return out
        filtered: dict[str, float] = {}
        for eid, act in out.items():
            e = self._store.get(eid)
            if e is None or float(getattr(e, "forgotten_at", 0.0) or 0.0) > 0.0:
                continue
            if persona_id is not None and (getattr(e, "persona_id", "") or "") != persona_id:
                continue
            if scope_id is not None and (getattr(e, "scope_id", "") or "") != scope_id:
                continue
            filtered[eid] = act
        return filtered

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
        # v1.42: use GraphStore reverse index instead of full-table scan.
        # The legacy O(N) loop lived here and scanned every engram on
        # every neighbor lookup; on a 10k+ store this is the difference
        # between sub-millisecond and seconds per recall.
        if self._graph is not None:
            try:
                batch = self._graph.engrams_for_batch([eid], limit_per_entity=128)
                for eid_ref, w in batch.get(eid, []):
                    out.append((N_PREFIX + eid_ref, W_ENTITY_TO_ENGRAM * float(w)))
                return out
            except Exception:
                # Fall through to the legacy path if the index is missing.
                pass
        # Legacy fallback: full-table scan, filters out forgotten engrams.
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

    def _engram_neighbors_batch(self, eids):
        """Batch: for each engram id, return its (entity_ref, weight) and
        (similar_to, weight) neighbors. Avoids the per-engram
        `_store.get` round-trip in the BFS frontier when the caller has
        a whole layer to expand at once.

        Returns {engram_id: [(node_key, weight), ...]} where node_keys
        are 'e:<id>' or 'n:<id>'. Forgotten engrams map to an empty list.
        """
        out = {eid: [] for eid in eids}
        for eid in eids:
            e = self._store.get(eid)
            if e is None or e.forgotten_at > 0:
                continue
            for ref in (e.entity_refs or []):
                out[eid].append((E_PREFIX + ref, W_ENGRAM_TO_ENTITY))
            for sib in (e.similar_to or []):
                if sib and sib != eid:
                    out[eid].append((N_PREFIX + sib, W_ENGRAM_SIMILAR))
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
