"""InjectHandler: v1.5 auto memory injection on the on_llm_request hook.

When `auto_inject_enabled` is on, this runs before every LLM call:
it recalls the top-k relevant engrams for the current user message and
splices their summaries into `req.prompt` (before/after). Default off,
so the plugin's behaviour is unchanged unless the user opts in.

Errors here must never abort the LLM request, so the whole body is
guarded; on any failure we simply skip injection.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..format import _extract
if TYPE_CHECKING:
    from hippocampus import MemoryService


class InjectHandler:
    """Auto-inject recalled memories into the outgoing LLM request."""

    def __init__(self, service: "MemoryService | None") -> None:
        self.service = service

    async def handle_inject(self, event, req) -> None:
        svc = self.service
        if svc is None or req is None:
            return
        cfg = getattr(svc, "cfg", None)
        if cfg is None or not getattr(cfg, "auto_inject_enabled", False):
            return
        try:
            from hippocampus import Cue
        except Exception:
            return
        try:
            top_k = int(getattr(cfg, "auto_inject_top_k", 3) or 0)
            if top_k <= 0:
                return
            meta = _extract(event)
            query = (meta.get("content") or "").strip()
            if not query:
                return
            actor_id = meta.get("actor_id")

            # Optional stable-background persona (v1.8). Independent of recall
            # hits: if enabled and present, it is injected as background even
            # when no episodic memory matches.
            persona_block = ""
            if getattr(cfg, "persona_inject_enabled", False):
                try:
                    persona = svc.get_persona(actor_id) if hasattr(svc, "get_persona") else None
                    summary = (getattr(persona, "summary", "") or "").strip() if persona else ""
                    if summary:
                        persona_block = "[用户画像]\n" + summary
                except Exception as pex:
                    print("[hippocampus] persona fetch skipped: " + repr(pex))

            result = svc.recall(Cue(
                text=query,
                actor_id=actor_id,
                channel_id=meta.get("channel_id"),
                k=top_k))
            engrams = getattr(result, "engrams", None) or []
            lines = []
            for e in engrams[:top_k]:
                summ = (getattr(e, "summary", "") or "").strip()
                if summ:
                    lines.append("- " + summ)
            memory_block = ("[相关长期记忆]\n" + "\n".join(lines)) if lines else ""

            # Persona goes first (stable background), then memories.
            parts = [b for b in (persona_block, memory_block) if b]
            if not parts:
                return
            block = "\n\n".join(parts)
            position = (getattr(cfg, "auto_inject_position", "before") or "before").lower()
            prompt = getattr(req, "prompt", "") or ""
            if position == "after":
                req.prompt = (prompt + "\n\n" + block) if prompt else block
            else:
                req.prompt = (block + "\n\n" + prompt) if prompt else block
        except Exception as ex:
            print("[hippocampus] auto inject skipped: " + repr(ex))