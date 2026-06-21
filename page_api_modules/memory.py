"""Memory management handler for the page API (B9).

Endpoints:
  list_memories(q, k, offset) -> paginated engram list (text search)
  get_memory_detail(eid)             -> single engram detail
  delete_memory(eid, hard)           -> soft (default) or hard delete
  update_memory(eid, fields)         -> edit fields; re-embed on text change
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class MemoryHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def list_memories(self, service, q: str = "",
                      k: int = 50, offset: int = 0) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            k_i = max(1, min(int(k), 500))
            offset_i = max(0, int(offset))
        except Exception:
            return self.utils.error("Invalid k or offset.")
        q = (q or "").strip()
        # ???????????????????????????
        scan_limit = 10_000_000 if q else (offset_i + k_i)
        try:
            rows = service.store.list_active(limit=scan_limit)
        except Exception as e:
            return self.utils.error(f"list_active failed: {e!r}")
        if q:
            ql = q.lower()
            def _hit(r):
                for fld in ("summary", "content", "actor_id"):
                    v = getattr(r, fld, "") or ""
                    if ql in str(v).lower():
                        return True
                return False
            rows = [r for r in rows if _hit(r)]
        page = rows[offset_i:offset_i + k_i]
        return self.utils.ok({
            "items": [self._list_item(r) for r in page],
            "returned": len(page),
            "offset": offset_i,
            "k": k_i,
        })

    @staticmethod
    def _tag_value(tags, prefix: str) -> str:
        """Pull the value of a `prefix:value` stamp from an engram's tags."""
        for t in (tags or []):
            t = str(t)
            if t.startswith(prefix):
                return t[len(prefix):]
        return ""

    def _list_item(self, r) -> dict:
        tags = getattr(r, "tags", None) or []
        group_id = self._tag_value(tags, "group:")
        group_name = self._tag_value(tags, "groupname:")
        return {
            "id": getattr(r, "id", None),
            "summary": (getattr(r, "summary", "") or "")[:200],
            "actor_id": getattr(r, "actor_id", None),
            "strength": getattr(r, "strength", None),
            "created_at": getattr(r, "created_at", None),
            "channel_id": getattr(r, "channel_id", None),
            "persona_id": getattr(r, "persona_id", None),
            "group_id": group_id or None,
            "group_name": group_name or None,
        }

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
            "topics": getattr(row, "topics", None) or [],
            "tags": getattr(row, "tags", None) or [],
            "tier": getattr(row, "tier", None),
            "persona_id": getattr(row, "persona_id", None),
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

    # v1.21 B-4: edit an engram from the WebUI. Text changes (content)
    # trigger an embedding recompute so recall stays consistent.
    _STR_FIELDS = ("summary", "content", "memory_type", "tier", "persona_id")
    _FLOAT_FIELDS = ("importance", "strength")
    _LIST_FIELDS = ("topics", "tags")

    def update_memory(self, service, eid: str,
                      fields: dict) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        eid = (eid or "").strip()
        if not eid:
            return self.utils.error("Missing eid.")
        if not isinstance(fields, dict) or not fields:
            return self.utils.error("No fields to update.")
        try:
            row = service.store.get(eid)
        except Exception as e:
            return self.utils.error(f"get failed: {e!r}")
        if row is None:
            return self.utils.error(f"unknown id: {eid}")
        changed = []
        text_changed = False
        for key in self._STR_FIELDS:
            if key in fields and fields[key] is not None:
                val = str(fields[key])
                if getattr(row, key, "") != val:
                    if key == "content":
                        text_changed = True
                    setattr(row, key, val)
                    changed.append(key)
        for key in self._FLOAT_FIELDS:
            if key in fields and fields[key] is not None:
                try:
                    val = float(fields[key])
                except (TypeError, ValueError):
                    return self.utils.error(f"{key} must be a number")
                val = max(0.0, min(1.0, val))
                if getattr(row, key, 0.0) != val:
                    setattr(row, key, val)
                    changed.append(key)
        for key in self._LIST_FIELDS:
            if key in fields and fields[key] is not None:
                val = self._coerce_list(fields[key])
                if list(getattr(row, key, []) or []) != val:
                    setattr(row, key, val)
                    changed.append(key)
        if not changed:
            return self.utils.ok({"id": eid, "changed": [], "reembedded": False})
        reembedded = False
        if text_changed:
            try:
                row.embedding = service.embedder.embed(row.content or "")
                row.embedding_model = getattr(
                    service, "_current_embedding_name", "") or row.embedding_model
                reembedded = True
            except Exception as e:
                return self.utils.error(f"re-embed failed: {e!r}")
        try:
            service.store.upsert(row)
        except Exception as e:
            return self.utils.error(f"upsert failed: {e!r}")
        return self.utils.ok({"id": eid, "changed": changed,
                              "reembedded": reembedded})

    @staticmethod
    def _coerce_list(val) -> list:
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        text = str(val or "").strip()
        if not text:
            return []
        parts = [p.strip() for p in text.replace("\u3001", ",").split(",")]
        return [p for p in parts if p]
