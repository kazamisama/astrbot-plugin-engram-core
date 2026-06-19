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
    """One pass: dedupe-merge + promote (episodic -> semantic) + decay."""
    def __init__(self, store: HippocampalStore, cfg: MemoryConfig,
                 llm=None, semantic=None, profile=None) -> None:
        self._store = store; self._cfg = cfg
        self._llm = llm
        self._semantic = semantic
        self._profile = profile

    def step(self) -> dict:
        engrams = self._store.all(limit=10_000)
        # v1.1: cluster auto-summarization (REM-style dream synthesis)
        if self._cfg.enable_cluster_summarization:
            self._refresh_cluster_summaries(engrams)
        merged = 0
        promoted = 0
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
        # Promote: high-importance + frequently recalled -> semantic
        if self._cfg.enable_promotion:
            now = time.time()
            for e in self._store.all(limit=10_000):
                if (e.memory_type == "episodic"
                        and e.access_count >= self._cfg.promote_min_access
                        and e.importance >= self._cfg.promote_min_importance):
                    e.memory_type = "semantic"
                    e.promoted_at = now
                    self._store.upsert(e)
                    promoted += 1
        # v1.0: SWR replay — re-strengthen the top-K by a small amount,
        # simulating hippocampal-cortical replay during quiet wake / NREM.
        replayed = 0
        boost = self._cfg.replay_boost
        for e in self._store.iter_for_replay(k=64):
            e.strength = min(1.0, e.strength + boost)
            e.access_count = (e.access_count or 0) + 1
            self._store.upsert(e)
            replayed += 1
        # Decay
        alive = DecayScheduler(self._cfg).step(self._store.all(limit=10_000))
        kept_ids = {e.id for e in alive}
        archived = 0
        for e in self._store.all(limit=10_000):
            if e.id not in kept_ids:
                self._store.delete(e.id)
                archived += 1
        # v1.2: episodic -> semantic consolidation (abstract recurring clusters into profile facts)
        abstracted = 0
        if (self._cfg.enable_episodic_semantic
                and self._semantic is not None and self._profile is not None):
            try:
                abstracted = self._consolidate_episodic_semantic()
            except Exception as e:
                print("[hippocampus] episodic->semantic consolidation error: " + repr(e))
        return {"merged": merged, "promoted": promoted, "archived": archived,
                "replayed": replayed, "abstracted": abstracted}

    # ---------- v1.1: cluster auto-summarization ----------
    def _refresh_cluster_summaries(self, engrams) -> None:
        """Group engrams by similar_to clique; for groups with enough members,
        generate a one-line gist (LLM with deterministic fallback) and store it
        in the cluster_summaries table (managed by HippocampalStore)."""
        min_size = max(2, int(self._cfg.cluster_summary_min_size))
        max_members = max(min_size, int(self._cfg.cluster_summary_max_members))
        # Pass 1: build (cluster_id -> members) and remember the assignment on the engram
        groups: dict[str, list] = {}
        for e in engrams:
            if e.forgotten_at > 0:
                continue
            if not e.similar_to:
                continue
            members_ids = {e.id, *e.similar_to}
            cluster_id = min(members_ids)
            groups.setdefault(cluster_id, []).append(e)
        for cid, members in groups.items():
            if len(members) < min_size:
                continue
            # Persist cluster_id on every member so other modules can recover the clique
            for e in members:
                if e.cluster_id != cid:
                    e.cluster_id = cid
                    self._store.upsert(e)
            # Skip if we already have a fresh gist
            existing = self._store.get_cluster_summary(cid)
            if existing is not None:
                continue
            top = sorted(members, key=lambda x: x.importance, reverse=True)[:max_members]
            gist = self._llm_gist(top) or self._fallback_gist(top)
            if gist:
                self._store.upsert_cluster_summary(cid, gist, len(members), source="auto")

    def _llm_gist(self, members) -> str | None:
        if self._llm is None:
            return None
        try:
            from .llm import RuleLLMProvider
            if isinstance(self._llm, RuleLLMProvider):
                return None
        except Exception:
            return None
        texts = "\n".join("- " + (e.summary or e.content)[:120] for e in members)
        prompt = ("Given these related memories of the same user, produce a single-line gist"
                  " (max 80 chars, no quotes, English or Chinese OK):\n" + texts + "\n\nGist:")
        try:
            out = self._llm.chat("memory gist generator", prompt, temperature=0.2, max_tokens=80)
        except Exception:
            return None
        if not out:
            return None
        line = out.strip().splitlines()[0] if out.strip() else ""
        return line[:80] or None

    @staticmethod
    def _fallback_gist(members) -> str:
        if not members:
            return ""
        sorted_m = sorted(members, key=lambda x: x.importance, reverse=True)
        if len(sorted_m) == 1:
            return (sorted_m[0].summary or sorted_m[0].content)[:80]
        parts = [(e.summary or e.content)[:30] for e in sorted_m[:3]]
        return " | ".join(parts)[:80]

    # ---------- v1.2: episodic -> semantic consolidation ----------
    def _consolidate_episodic_semantic(self) -> int:
        """Abstract recurring clusters into stable profile facts.

        A cluster (engrams sharing a cluster_id) that has come back enough times
        (enough members AND enough total retrievals) is treated as something the
        system now "knows" rather than merely "experienced". We mine the most
        confident (predicate, object) relation shared across the cluster's
        entities and mint a profile fact from it, then back-link the engrams to
        that fact via profile_fact_id. This is the hippocampus -> neocortex
        systems-consolidation step.
        """
        from .profile import ProfileFact, _PROFILE_PREDICATES
        min_members = max(2, int(self._cfg.consolidation_cluster_min_members))
        min_access = max(0, int(self._cfg.consolidation_cluster_min_access))
        conf = float(self._cfg.consolidation_fact_confidence)

        # Group active engrams into clusters. Prefer the persisted cluster_id,
        # but fall back to connected components over the similar_to graph so we
        # do not depend on cluster summarization having run first.
        active = [e for e in self._store.all(limit=10_000) if e.forgotten_at <= 0]
        by_id = {e.id: e for e in active}
        parent: dict[str, str] = {e.id: e.id for e in active}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for e in active:
            for sib in (e.similar_to or []):
                if sib in parent:
                    union(e.id, sib)
        clusters: dict[str, list] = {}
        for e in active:
            root = e.cluster_id if (e.cluster_id and e.cluster_id in by_id) else find(e.id)
            clusters.setdefault(root, []).append(e)

        minted = 0
        for cid, members in clusters.items():
            if len(members) < min_members:
                continue
            if sum(int(m.access_count or 0) for m in members) < min_access:
                continue
            actor_id = members[0].actor_id or "anonymous"
            # collect candidate (predicate, object_name) -> [conf, eids, relids]
            best: dict[tuple[str, str], tuple[float, set, set]] = {}
            for e in members:
                for ref in (e.entity_refs or []):
                    ent = self._semantic.get_entity(ref)
                    if ent is None:
                        continue
                    for rel in self._semantic.relations_of(ent.id):
                        if rel.predicate not in _PROFILE_PREDICATES:
                            continue
                        if rel.subject_id != ent.id:
                            continue
                        obj = self._semantic.get_entity(rel.object_id)
                        if obj is None:
                            continue
                        key = (rel.predicate, obj.name)
                        c0, eids, relids = best.get(key, (0.0, set(), set()))
                        best[key] = (max(c0, float(rel.confidence)),
                                     eids | {e.id}, relids | {rel.id})
            if not best:
                continue
            # pick the single strongest, most-supported triple for this cluster
            (pred, objname), (relconf, eids, relids) = max(
                best.items(), key=lambda kv: (len(kv[1][1]), kv[1][0]))
            if len(eids) < 2:
                continue
            fact = ProfileFact(
                actor_id=actor_id, predicate=pred, value=objname,
                confidence=max(conf, relconf), evidence_count=len(eids),
                source_relation_ids=list(relids), source_engram_ids=list(eids),
            )
            stored = self._profile.upsert_fact(fact)
            minted += 1
            # back-link members to the minted fact
            for e in members:
                if e.id in eids and e.profile_fact_id != stored.id:
                    e.profile_fact_id = stored.id
                    self._store.upsert(e)
        return minted
