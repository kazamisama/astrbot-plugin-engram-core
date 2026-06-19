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
        result = self.service.recall(Cue(
            text=meta["content"] or query or "(empty)",
            actor_id=meta["actor_id"],
            channel_id=meta["channel_id"],
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
        if mode == "dual":
            yield event.plain_result(format_dual_route(self.service, query, k=5))
            return
        meta = _extract(event)
        result = self.service.recall(Cue(
            text=query, actor_id=meta["actor_id"],
            channel_id=meta["channel_id"], k=5, mode=mode))
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
                yield event.plain_result(
                    "用户画像（" + actor_id + "，未更新）：\n" + existing.summary)
            else:
                yield event.plain_result(
                    "无法生成画像：该用户暂无足够记忆，或当前 LLM 为规则兜底。")
            return
        yield event.plain_result(
            "用户画像（" + actor_id + "，基于 " + str(persona.source_count)
            + " 条记忆）：\n" + persona.summary)

    async def cmd_mem_activate(self, event, seeds: str = ""):
        from ..format import format_activation
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(format_activation(self.service, seeds))

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
        yield event.plain_result(format_confidence(self.service, query))

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
        yield event.plain_result(format_narrative(self.service, topic.strip()))