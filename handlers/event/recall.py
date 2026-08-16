"""RecallHandler: read-only query / recall / association commands.

Split from main.py at v1.4.x B6. Owns the 8 command handlers in
the "read" family: /recall, /mem search, /mem profile, /mem activate,
/mem cluster, /mem cluster-list, /mem confidence, /mem decaycurve,
/mem narrative. Decorators stay on HippocampusStar in main.py; this
class holds business logic only.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..format import _extract
if TYPE_CHECKING:
    from hippocampus import Cue, MemoryService


class RecallHandler:
    """Read-only commands that introspect / query the memory store."""

    def __init__(self, service: "MemoryService | None") -> None:
        self.service = service

    async def cmd_recall(self, event, query: str):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        meta = _extract(event)
        cfg = getattr(self.service, "cfg", None)
        iso_on = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True
        persona_scope = (meta.get("persona_id") or "") if iso_on else None
        scope_scope = (meta.get("scope_id") or "") if iso_on else None
        result = self.service.recall(Cue(
            text=meta["content"] or query or "(empty)",
            actor_id=meta["actor_id"],
            channel_id=meta["channel_id"],
            persona_id=persona_scope,
            scope_id=scope_scope,
            k=5))
        if not result.engrams:
            yield event.plain_result("No memories found.")
            return
        lines = [f"- {e.summary}" for e in result.engrams if e.summary]
        yield event.plain_result("Related memories:\n" + "\n".join(lines))

    async def cmd_mem_search(self, event, arg: str):
        # Imported lazily to keep import graph tight (parse_search_args
        # only needed for /mem search).
        from ..format import parse_search_args, format_dual_route
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        query, mode = parse_search_args(arg)
        if not query:
            yield event.plain_result(
                "usage: /mem search <query> [--mode=vector|fts|hybrid|dual]")
            return
        meta = _extract(event)
        cfg2 = getattr(self.service, "cfg", None)
        iso_on2 = bool(getattr(cfg2, "persona_isolation_enabled", True)) if cfg2 else True
        persona_scope2 = (meta.get("persona_id") or "") if iso_on2 else None
        scope_scope2 = (meta.get("scope_id") or "") if iso_on2 else None
        if mode == "dual":
            yield event.plain_result(format_dual_route(self.service, query, k=5, persona_id=persona_scope2, scope_id=scope_scope2))
            return
        result = self.service.recall(Cue(
            text=query, actor_id=meta["actor_id"],
            channel_id=meta["channel_id"], persona_id=persona_scope2,
            scope_id=scope_scope2, k=5, mode=mode))
        if not result.engrams:
            yield event.plain_result("[" + mode + "] no hit for: " + query)
            return
        lines = ["[" + mode + "] hits for: " + query]
        for e, s in zip(result.engrams, result.scores):
            lines.append("- " + str(round(s, 3)) + "  " + e.summary[:60])
        yield event.plain_result(chr(10).join(lines))

    async def cmd_mem_profile(self, event, actor: str = ""):
        from ..format import format_profile
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        meta = _extract(event)
        actor_id = (actor or "").strip() or meta["actor_id"]
        yield event.plain_result(format_profile(self.service, actor_id))

    async def cmd_mem_persona(self, event, actor: str = ""):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        if not getattr(self.service, "persona_store", None):
            yield event.plain_result(
                "用户画像未启用（请在配置中开启「启用用户画像」）。")
            return
        meta = _extract(event)
        actor_id = (actor or "").strip() or meta["actor_id"]
        persona = self.service.build_persona(actor_id)
        if persona is None or not (persona.summary or "").strip():
            existing = self.service.get_persona(actor_id)
            if existing is not None and (existing.summary or "").strip():
                etags = ("\n标签：" + " / ".join(existing.tags)) if getattr(existing, "tags", None) else ""
                yield event.plain_result(
                    "用户画像（" + actor_id + "，未更新）：\n" + existing.summary + etags)
            else:
                yield event.plain_result(
                    "无法生成画像：该用户暂无足够记忆，或当前 LLM 为规则兜底。")
            return
        tag_line = ""
        if getattr(persona, "tags", None):
            tag_line = "\n标签：" + " / ".join(persona.tags)
        yield event.plain_result(
            "用户画像（" + actor_id + "，基于 " + str(persona.source_count)
            + " 条记忆）：\n" + persona.summary + tag_line)

    async def cmd_mem_activate(self, event, seeds: str = ""):
        from ..format import format_activation
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        meta = _extract(event)
        cfg = getattr(self.service, "cfg", None)
        iso = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True
        persona_scope = (meta.get("persona_id") or "") if iso else None
        scope_scope = (meta.get("scope_id") or "") if iso else None
        yield event.plain_result(format_activation(self.service, seeds, persona_id=persona_scope, scope_id=scope_scope))

    async def cmd_mem_cluster(self, event, eid: str):
        from ..format import format_cluster
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(format_cluster(self.service, eid.strip()))

    async def cmd_mem_cluster_list(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        rows = self.service.store.list_cluster_summaries(limit=50)
        if not rows:
            yield event.plain_result("(no cluster summaries yet - try /mem replay)")
            return
        lines = ["## cluster summaries (" + str(len(rows)) + ")"]
        for r in rows:
            lines.append("- " + r["cluster_id"][:8]
                         + " (n=" + str(r["member_count"]) + ")  " + r["gist"])
        yield event.plain_result("\n".join(lines))

    async def cmd_mem_confidence(self, event, query: str = ""):
        from ..format import format_confidence
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        meta = _extract(event)
        cfg = getattr(self.service, "cfg", None)
        iso = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True
        persona_scope = (meta.get("persona_id") or "") if iso else None
        scope_scope = (meta.get("scope_id") or "") if iso else None
        yield event.plain_result(format_confidence(self.service, query, persona_id=persona_scope, scope_id=scope_scope))

    async def cmd_mem_decaycurve(self, event, arg: str = ""):
        from ..format import format_decaycurve
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(format_decaycurve(self.service, arg))

    async def cmd_mem_narrative(self, event, topic: str):
        from ..format import format_narrative
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        meta = _extract(event)
        cfg = getattr(self.service, "cfg", None)
        iso = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True
        persona_scope = (meta.get("persona_id") or "") if iso else None
        scope_scope = (meta.get("scope_id") or "") if iso else None
        yield event.plain_result(format_narrative(self.service, topic.strip(), persona_id=persona_scope, scope_id=scope_scope))

    async def cmd_mem_debug(self, event, query: str = ""):
        """v1.64 B14 /mem debug: diagnostic report on the dual-route
        retriever's per-route hit attribution, RRF fusion, MMR cut,
        and final top-k. Pairs with format_debug() in handlers/format.
        """
        from ..format import format_debug
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        meta = _extract(event)
        cfg = getattr(self.service, "cfg", None)
        iso = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True
        persona_scope = (meta.get("persona_id") or "") if iso else None
        yield event.plain_result(format_debug(self.service, query, persona_id=persona_scope))