"""page_api_modules.diary: read-only diary browsing for the WebUI.

Diaries are stored as engrams with memory_type=="diary", carrying
channel_id / persona_id columns plus tag stamps:
  day:<YYYY-MM-DD>  group:<id>  groupname:<name>  peer:<name>  chat:<type>

This handler offers a paginated, filterable view (by group/channel,
persona, date) parallel to the memory list, without touching writes.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class DiaryHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    @staticmethod
    def _tag_value(tags, prefix: str) -> str:
        for t in (tags or []):
            t = str(t)
            if t.startswith(prefix):
                return t[len(prefix):]
        return ""

    def _diaries(self, service) -> list:
        """All active diary engrams, newest first."""
        rows = service.store.list_active(limit=10_000_000)
        out = [r for r in rows if (getattr(r, "memory_type", "") or "") == "diary"]
        out.sort(key=lambda r: getattr(r, "created_at", 0.0) or 0.0, reverse=True)
        return out

    def _channel_label(self, r) -> str:
        tags = getattr(r, "tags", None) or []
        gid = self._tag_value(tags, "group:")
        gname = self._tag_value(tags, "groupname:")
        if gname and gid:
            return gname + " (" + gid + ")"
        if gname:
            return gname
        if gid:
            return gid
        peer = self._tag_value(tags, "peer:")
        if peer:
            return peer
        return getattr(r, "channel_id", "") or ""

    def _list_item(self, r) -> dict:
        tags = getattr(r, "tags", None) or []
        return {
            "id": getattr(r, "id", None),
            "summary": (getattr(r, "summary", "") or "")[:200],
            "day": self._tag_value(tags, "day:") or None,
            "chat_type": self._tag_value(tags, "chat:") or None,
            "channel_id": getattr(r, "channel_id", None),
            "persona_id": getattr(r, "persona_id", None),
            "group_id": self._tag_value(tags, "group:") or None,
            "group_name": self._tag_value(tags, "groupname:") or None,
            "peer_name": self._tag_value(tags, "peer:") or None,
            "channel_label": self._channel_label(r),
            "created_at": getattr(r, "created_at", None),
        }

    def options(self, service) -> dict[str, Any]:
        """Distinct filter options: channels (with label), personas, days."""
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            diaries = self._diaries(service)
        except Exception as e:
            return self.utils.error("list diaries failed: " + repr(e))
        channels: dict[str, str] = {}
        personas: set = set()
        days: set = set()
        for r in diaries:
            cid = getattr(r, "channel_id", "") or ""
            if cid and cid not in channels:
                channels[cid] = self._channel_label(r)
            pid = getattr(r, "persona_id", "") or ""
            personas.add(pid)
            day = self._tag_value(getattr(r, "tags", None) or [], "day:")
            if day:
                days.add(day)
        return self.utils.ok({
            "channels": [{"channel_id": k, "label": v} for k, v in
                         sorted(channels.items(), key=lambda kv: kv[1])],
            "personas": sorted(personas),
            "days": sorted(days, reverse=True),
            "total": len(diaries),
        })

    def list_diaries(self, service, channel_id: str = "", persona_id: str = "",
                     day: str = "", q: str = "",
                     k: int = 50, offset: int = 0) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            k_i = max(1, min(int(k), 500))
            offset_i = max(0, int(offset))
        except Exception:
            return self.utils.error("Invalid k or offset.")
        try:
            rows = self._diaries(service)
        except Exception as e:
            return self.utils.error("list diaries failed: " + repr(e))
        channel_id = (channel_id or "").strip()
        persona_id = (persona_id or "").strip()
        day = (day or "").strip()
        q = (q or "").strip()
        PERSONA_NONE = "__none__"
        if channel_id:
            rows = [r for r in rows if (getattr(r, "channel_id", "") or "") == channel_id]
        if persona_id:
            if persona_id == PERSONA_NONE:
                rows = [r for r in rows if not (getattr(r, "persona_id", "") or "")]
            else:
                rows = [r for r in rows if (getattr(r, "persona_id", "") or "") == persona_id]
        if day:
            rows = [r for r in rows
                    if self._tag_value(getattr(r, "tags", None) or [], "day:") == day]
        if q:
            ql = q.lower()
            def _hit(r):
                for fld in ("summary", "content"):
                    v = getattr(r, fld, "") or ""
                    if ql in str(v).lower():
                        return True
                return False
            rows = [r for r in rows if _hit(r)]
        total = len(rows)
        page = rows[offset_i:offset_i + k_i]
        return self.utils.ok({
            "items": [self._list_item(r) for r in page],
            "returned": len(page),
            "total": total,
            "offset": offset_i,
            "k": k_i,
        })

    def get_detail(self, service, eid: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        eid = (eid or "").strip()
        if not eid:
            return self.utils.error("Missing eid.")
        try:
            row = service.store.get(eid)
        except Exception as e:
            return self.utils.error("get failed: " + repr(e))
        if row is None or (getattr(row, "memory_type", "") or "") != "diary":
            return self.utils.error("Diary not found.")
        tags = getattr(row, "tags", None) or []
        item = self._list_item(row)
        item["content"] = getattr(row, "content", "") or ""
        item["summary_full"] = getattr(row, "summary", "") or ""
        item["topics"] = list(getattr(row, "topics", None) or [])
        item["participants"] = list(getattr(row, "entities", None) or [])
        item["importance"] = getattr(row, "importance", None)
        item["strength"] = getattr(row, "strength", None)
        item["session_id"] = getattr(row, "session_id", None)
        item["platform"] = getattr(row, "platform", None)
        return self.utils.ok(item)
