"""Recall handler for the page API (B9).

Endpoints:
  test_recall(query, mode, k) -> run recall against the real service
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class RecallHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def test_recall(self, service, query: str = "",
                    mode: str = "hybrid", k: int = 5) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        query = (query or "").strip()
        if not query:
            return self.utils.error("Missing query.")
        try:
            from hippocampus import Cue
            cue = Cue(text=query, actor_id="", channel_id="", k=int(k), mode=mode)
            result = service.recall(cue)
        except Exception as e:
            return self.utils.error(f"recall failed: {e!r}")
        items = []
        for e, s in zip(result.engrams, result.scores):
            items.append({
                "id": e.id,
                "summary": (e.summary or "")[:200],
                "score": float(s),
                "actor_id": getattr(e, "actor_id", None),
            })
        return self.utils.ok({
            "query": query,
            "mode": mode,
            "k": int(k),
            "count": len(items),
            "items": items,
        })
