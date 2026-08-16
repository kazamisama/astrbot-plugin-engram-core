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
                    mode: str = "dual", k: int = 5,
                    persona_id: str | None = None,
                    scope_id: str | None = None) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        query = (query or "").strip()
        if not query:
            return self.utils.error("Missing query.")
        try:
            k_i = max(1, min(int(k), 50))
        except Exception:
            return self.utils.error("Invalid k.")
        mode = (mode or "dual").strip().lower()
        if mode not in ("dual", "hybrid", "vector", "fts"):
            mode = "dual"
        import time as _time
        try:
            from hippocampus import Cue
            cue = Cue(text=query, actor_id="", channel_id="", k=k_i, mode=mode,
                      persona_id=persona_id, scope_id=scope_id)
            start = _time.time()
            if mode == "dual" and hasattr(service, "explain_dual_route"):
                data = service.explain_dual_route(cue)
                items = data.get("items", [])
                routes_used = data.get("routes_used", [])
                for it in items:
                    it["score"] = it.get("final_score", 0.0)
            else:
                result = service.recall(cue)
                routes_used = []
                items = []
                for e, s in zip(result.engrams, result.scores):
                    items.append({
                        "id": e.id,
                        "summary": (e.summary or "")[:200],
                        "score": float(s),
                        "actor_id": getattr(e, "actor_id", None),
                        "memory_type": getattr(e, "memory_type", None),
                        "importance": getattr(e, "importance", None),
                        "strength": getattr(e, "strength", None),
                        "routes": {},
                    })
            elapsed_ms = round((_time.time() - start) * 1000.0, 2)
        except Exception as e:
            return self.utils.error(f"recall failed: {e!r}")
        return self.utils.ok({
            "query": query,
            "mode": mode,
            "k": k_i,
            "count": len(items),
            "items": items,
            "results": items,
            "routes_used": routes_used,
            "elapsed_time_ms": elapsed_ms,
        })
