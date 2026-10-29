from __future__ import annotations
from .types import Engram
from .config import MemoryConfig
from .storage import _cos


class PatternSeparator:
    """DG (dentate gyrus) analog. Decides at observe time whether an incoming
    engram should MERGE into a recent one or be stored as a NEW engram.

    v1.78.0: the two-sided "link" action is gone. It only fed the
    similar_to/cluster chain (cluster gists, profile facts), which had no
    writer under the default summary pipeline and no recall consumer; keeping
    a distinct engram separate is exactly what pattern separation is for.
    """

    def __init__(self, cfg: MemoryConfig) -> None:
        self._cfg = cfg

    def resolve(self, incoming: Engram, candidates: list[Engram]):
        """Return (action, target) where action in {"merge", "new"}.
        Caller is responsible for persisting any mutation.
        """
        if not candidates:
            return "new", None
        scored = [(c, _cos(incoming.embedding, c.embedding)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        top, sim = scored[0]
        if sim >= self._cfg.pattern_separation_threshold:
            return "merge", top
        return "new", None
