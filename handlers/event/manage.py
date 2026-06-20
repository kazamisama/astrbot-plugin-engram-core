"""ManageHandler: write / admin / debug commands.

Split from main.py at v1.4.x B6. Owns the 9 command handlers in
the "manage" family: /mem model, /mem rebuild, /mem forget,
/mem export, /mem import, /mem graph, /mem prospective,
/mem replay, /mem consolidate, /mem valence, /mem streams,
/mem session, /mem remember. Decorators stay on HippocampusStar
in main.py; this class holds business logic only.
"""
from __future__ import annotations
import time
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hippocampus import MemoryService


class ManageHandler:
    """Write / admin / debug commands that mutate or introspect the service."""

    def __init__(self, service: "MemoryService | None") -> None:
        self.service = service

    async def cmd_mem_model(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        m = self.service
        yield event.plain_result(
            "embedding.current = " + m.current_embedding() + "\n"
            "embedding.available = " + ", ".join(m.registry.list_embeddings()) + "\n"
            "llm.current = " + m.current_llm() + "\n"
            "llm.available = " + ", ".join(m.registry.list_llms())
        )

    async def cmd_mem_use_emb(self, event, name: str):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        try:
            yield event.plain_result(self.service.set_embedding(name))
        except KeyError as e:
            yield event.plain_result("ERR: " + str(e))

    async def cmd_mem_use_llm(self, event, name: str):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        try:
            yield event.plain_result(self.service.set_llm(name))
        except KeyError as e:
            yield event.plain_result("ERR: " + str(e))

    async def cmd_mem_rebuild(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        n = self.service.rebuild_embeddings()
        yield event.plain_result(f"rebuilt {n} engrams")

    async def cmd_mem_prospective(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        triggers = self.service.list_prospective(status="pending")
        if not triggers:
            yield event.plain_result("No pending triggers.")
            return
        lines = []
        for t in triggers[:20]:
            fire_in = int(t.fire_at - time.time())
            lines.append("- id=" + t.id[:8] + " in " + str(fire_in) + "s  "
                         "payload=" + str(t.payload))
        yield event.plain_result("Pending triggers:\n" + "\n".join(lines))

    async def cmd_mem_session(self, event):
        from ..format import format_session
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(format_session(self.service))

    async def cmd_mem_forget(self, event, eid: str):
        from ..format import find_and_forget
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(find_and_forget(self.service, eid))

    async def cmd_mem_export(self, event, path: str):
        from ..format import export_engrams
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(export_engrams(self.service, path.strip()))

    async def cmd_mem_import(self, event, path: str):
        from ..format import import_engrams
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(import_engrams(self.service, path.strip()))

    async def cmd_mem_graph(self, event, query: str):
        from ..format import format_graph
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        yield event.plain_result(format_graph(self.service, query.strip()))

    async def cmd_mem_replay(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        try:
            from hippocampus.consolidator import ReplayConsolidator
            rc = ReplayConsolidator(self.service.store, self.service.cfg)
            res = rc.step()
            msg = ("replay: merged=" + str(res.get("merged", 0))
                   + " promoted=" + str(res.get("promoted", 0))
                   + " archived=" + str(res.get("archived", 0))
                   + " replayed=" + str(res.get("replayed", 0)))
            yield event.plain_result(msg)
        except Exception as e:
            yield event.plain_result("ERR: " + repr(e))

    async def cmd_mem_valence(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        h = self.service.store.valence_histogram()
        lines = ["## valence distribution"]
        for k in ("positive", "neutral", "negative", "unscored"):
            lines.append("  " + k + ": " + str(h.get(k, 0)))
        yield event.plain_result(chr(10).join(lines))

    async def cmd_mem_streams(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        b = self.service.store.stream_breakdown()
        lines = ["## stream breakdown",
                 "  what (ventral, identity/fact/preference): " + str(b.get("what", 0)),
                 "  where_when (dorsal, place/time/plan):  " + str(b.get("where_when", 0)),
                 "  untyped:                              " + str(b.get("untyped", 0))]
        yield event.plain_result(chr(10).join(lines))

    async def cmd_mem_tier(self, event, arg: str = ""):
        """/mem tier        -> show hot/warm/cold counts (live classify)
           /mem tier reclass -> recompute + persist tiers, then show counts
           /mem tier archive -> archive cold tier to a compressed file"""
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        cfg = getattr(self.service, "cfg", None)
        if not getattr(cfg, "tiering_enabled", False):
            yield event.plain_result("记忆分层未启用（tiering_enabled=false）。")
            return
        sub = (arg or "").strip().lower()
        from ..format import format_tier
        if sub == "reclass":
            counts = self.service.reclassify_tiers()
            yield event.plain_result("已重算分层。" + chr(10) + format_tier(self.service, counts))
            return
        if sub == "archive":
            from ..format import format_tier_archive
            res = self.service.archive_cold()
            yield event.plain_result(format_tier_archive(res))
            return
        yield event.plain_result(format_tier(self.service))

    async def cmd_mem_remember(self, event, arg: str = ""):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        from ..format import _extract
        meta = _extract(event)
        parts = (arg or "").strip().split()
        if len(parts) < 2:
            yield event.plain_result(
                "usage: /mem remember <predicate> <value> [actor]")
            return
        from hippocampus.profile import ProfileFact
        pred = parts[0]
        val = " ".join(parts[1:])
        actor_id = meta["actor_id"]
        try:
            f = self.service.remember_fact(ProfileFact(
                actor_id=actor_id, predicate=pred, value=val,
                confidence=1.0, evidence_count=1))
            yield event.plain_result(
                "remembered: " + pred + " = " + val + " (id=" + f.id[:8] + ")")
        except Exception as e:
            yield event.plain_result("ERR: " + repr(e))

    async def cmd_mem_consolidate(self, event):
        if self.service is None:
            yield event.plain_result("Memory service not initialized.")
            return
        res = self.service.force_consolidate()
        yield event.plain_result("consolidated: " + str(res))