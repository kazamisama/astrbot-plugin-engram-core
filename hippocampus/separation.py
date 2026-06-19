from __future__ import annotations
from .types import Engram
from .config import MemoryConfig
from .storage import _cos


class PatternSeparator:
    """DG (dentate gyrus) analog. Decides at observe time whether an incoming
    engram should MERGE into an existing one, LINK to it, or be NEW.

    Recall-side cluster expansion lives in PatternCompleter (recall.py) so
    this class stays a pure encode-time gate.
    """

    def __init__(self, cfg: MemoryConfig) -> None:
        self._cfg = cfg

    def resolve(self, incoming: Engram, candidates: list[Engram]):
        """Return (action, target) where action in {"merge", "link", "new"}.
        Caller is responsible for persisting any mutation.
        """
        if not candidates:
            return "new", None
        scored = [(c, _cos(incoming.embedding, c.embedding)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        top, sim = scored[0]
        if sim >= self._cfg.pattern_separation_threshold:
            return "merge", top
        if sim >= self._cfg.pattern_similar_threshold:
            return "link", top
        return "new", None

    @staticmethod
    def apply_link(link: Engram, target: Engram, max_links: int) -> None:
        """Mutate both engrams in-place: bidirectional similar_to link,
        deduped, capped to max_links per side. Most recent neighbor first."""
        for src, dst in ((link, target), (target, link)):
            chain = list(src.similar_to or [])
            # dedupe, keep order
            seen = set(); out = []
            for x in [dst.id] + chain:
                if x and x not in seen:
                    seen.add(x); out.append(x)
            src.similar_to = out[:max_links]

    @staticmethod
    def expand_cluster(roots: list[Engram], fetch, max_total: int) -> list[tuple[Engram, float, str]]:
        """BFS depth=1 over similar_to. Returns list of (engram, score, origin)
        where origin is the root id that pulled it in ("" for the roots).
        Capped at max_total items; cycles broken by id-set.
        """
        out: list[tuple[Engram, float, str]] = []
        seen_ids: set[str] = set()
        for r in roots:
            if len(out) >= max_total:
                break
            if r.id in seen_ids:
                continue
            seen_ids.add(r.id)
            out.append((r, 1.0, ""))
        # one hop from each root
        for r in roots:
            if not r.similar_to:
                continue
            for sib_id in r.similar_to:
                if len(out) >= max_total:
                    break
                if sib_id in seen_ids:
                    continue
                sib = fetch(sib_id)
                if sib is None:
                    continue
                seen_ids.add(sib.id)
                out.append((sib, 0.95, r.id))
        return out
