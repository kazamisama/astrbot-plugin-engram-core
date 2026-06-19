"""Memory management handler for the page API (B9).

Endpoints:
  list_memories(actor_id, k, offset) -> paginated engram list
  get_memory_detail(eid)             -> single engram detail
  delete_memory(eid, hard)           -> soft (default) or hard delete
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class MemoryHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def list_memories(self, service, actor_id: str = "",
                      k: int = 50, offset: int = 0) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            k_i = max(1, min(int(k), 500))
            offset_i = max(0, int(offset))
        except Exception:
            return self.utils.error("Invalid k or offset.")
        try:
            rows = service.store.list_active(limit=offset_i + k_i)
        except Exception as e:
            return self.utils.error(f"list_active failed: {e!r}")
        if actor_id:
            rows = [r for r in rows
                    if (getattr(r, "actor_id", "") or "") == actor_id]
        page = rows[offset_i:offset_i + k_i]
        return self.utils.ok({
            "items": [
                {
                    "id": getattr(r, "id", None),
                    "summary": (getattr(r, "summary", "") or "")[:200],
                    "actor_id": getattr(r, "actor_id", None),
                    "strength": getattr(r, "strength", None),
                    "created_at": getattr(r, "created_at", None),
                }
                for r in page
            ],
            "returned": len(page),
            "offset": offset_i,
            "k": k_i,
        })

    def get_memory_detail(self, service, eid: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        eid = (eid or "").strip()
        if not eid:
            return self.utils.error("Missing eid.")
        try:
            row = service.store.get(eid)
        except Exception as e:
            return self.utils.error(f"get failed: {e!r}")
        if row is None:
            # Try prefix match (legacy /mem forget behavior)
            try:
                rows = service.store.list_active(limit=10_000_000)
                matches = [r for r in rows
                           if (getattr(r, "id", "") or "").startswith(eid)]
                if len(matches) == 1:
                    row = matches[0]
                elif len(matches) > 1:
                    ids = [getattr(r, "id", "")[:8] for r in matches[:5]]
                    return self.utils.error(
                        f"ambiguous: {len(matches)} matches: {ids}; use full id")
                else:
                    return self.utils.error(f"unknown id: {eid}")
            except Exception as e:
                return self.utils.error(f"prefix lookup failed: {e!r}")
        return self.utils.ok({
            "id": getattr(row, "id", None),
            "summary": getattr(row, "summary", None),
            "content": getattr(row, "content", None),
            "actor_id": getattr(row, "actor_id", None),
            "stream": getattr(row, "stream", None),
            "memory_type": getattr(row, "memory_type", None),
            "strength": getattr(row, "strength", None),
            "importance": getattr(row, "importance", None),
            "confidence": getattr(row, "confidence", None),
            "created_at": getattr(row, "created_at", None),
            "entity_refs": getattr(row, "entity_refs", None) or [],
        })

    def delete_memory(self, service, eid: str,
                      hard: bool = False) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        eid = (eid or "").strip()
        if not eid:
            return self.utils.error("Missing eid.")
        try:
            row = service.store.get(eid)
        except Exception as e:
            return self.utils.error(f"get failed: {e!r}")
        if row is None:
            return self.utils.error(f"unknown id: {eid}")
        if hard:
            try:
                service.store.delete(eid)
                return self.utils.ok({"id": eid, "mode": "hard"})
            except Exception as e:
                return self.utils.error(f"hard delete failed: {e!r}")
        # soft: HippocampalStore.soft_forget sets forgotten_at + strength=0
        try:
            service.store.soft_forget(eid)
            return self.utils.ok({"id": eid, "mode": "soft"})
        except Exception as e:
            return self.utils.error(f"soft forget failed: {e!r}")
