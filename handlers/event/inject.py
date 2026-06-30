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
from hippocampus.reltime import relative_label
try:
    from astrbot.core.agent.message import TextPart
except ImportError:
    TextPart = None  # pre-v4 AstrBot: fallback to string concat
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
            iso_on = bool(getattr(cfg, "persona_isolation_enabled", True))
            persona_scope = (meta.get("persona_id") or "") if iso_on else None

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
                        ptags = getattr(persona, "tags", None) if persona else None
                        if ptags:
                            persona_block += "\n标签：" + " / ".join(ptags)
                except Exception as pex:
                    print("[hippocampus] persona fetch skipped: " + repr(pex))

            # v1.20 B-3: layered recall - conversation summaries only
            # (episodic/semantic), diary is recalled separately below with
            # its own quota so the two layers do not crowd each other out.
            result = svc.recall(Cue(
                text=query,
                actor_id=actor_id,
                channel_id=meta.get("channel_id"),
                persona_id=persona_scope,
                memory_types=["episodic", "semantic", "prospective"],
                k=top_k))
            engrams = getattr(result, "engrams", None) or []
            show_time = bool(getattr(cfg, "auto_inject_relative_time", True))
            lines = []
            for e in engrams[:top_k]:
                summ = (getattr(e, "summary", "") or "").strip()
                if not summ:
                    continue
                label = relative_label(getattr(e, "created_at", 0.0)) if show_time else ""
                if label:
                    lines.append("- [" + label + "] " + summ)
                else:
                    lines.append("- " + summ)
            memory_block = ("[近期对话]\n" + "\n".join(lines)) if lines else ""

            # v1.19 B-2: relation injection (option-4 pipeline filter).
            relation_block = ""
            if hasattr(svc, "recall_relations"):
                try:
                    rtop = int(getattr(cfg, "relation_inject_top_n", 3) or 0)
                    if rtop > 0:
                        rmin = float(getattr(cfg, "relation_inject_min_confidence", 0.0) or 0.0)
                        rels = svc.recall_relations(query, top_n=rtop, min_confidence=rmin)
                        rlines = []
                        for r in rels:
                            subj = (getattr(r, "subject", "") or "").strip()
                            pred = (getattr(r, "predicate", "") or "").strip()
                            obj = (getattr(r, "object", "") or "").strip()
                            if subj and pred:
                                rlines.append("- " + subj + " " + pred + (" " + obj if obj else ""))
                        if rlines:
                            relation_block = "[人物关系]\n" + "\n".join(rlines)
                except Exception as rex:
                    print("[hippocampus] relation inject skipped: " + repr(rex))

            # v1.20 B-3: diary recall with its own quota + source label.
            diary_block = ""
            if hasattr(svc, "recall_diary_chunks"):
                try:
                    dtop = int(getattr(cfg, "diary_inject_top_n", 1) or 0)
                    if dtop > 0:
                        dmin = float(getattr(cfg, "diary_inject_min_score", 0.0) or 0.0)
                        hits = svc.recall_diary_chunks(query, top_n=dtop, min_score=dmin, persona_id=persona_scope)
                        dlines = ["- " + t for t, _sc in hits if (t or "").strip()]
                        if dlines:
                            diary_block = "[\u4eca\u65e5\u56de\u987e]\n" + "\n".join(dlines)
                except Exception as dex:
                    print("[hippocampus] diary inject skipped: " + repr(dex))

            # Persona (background) -> relations -> recent conversation -> diary.
            blocks: list[tuple[str, str]] = []
            if persona_block:
                blocks.append(("persona", persona_block))
            if relation_block:
                blocks.append(("relation", relation_block))
            if memory_block:
                blocks.append(("memory", memory_block))
            if diary_block:
                blocks.append(("diary", diary_block))
            if not blocks:
                return
            # v1.66: use structured TextPart instead of raw prompt concatenation.
            # Each block becomes its own TextPart (marked temp so it never
            # enters conversation history). This follows the social_context /
            # ESM v0.9.x pattern: static rules in prompt=, dynamic data in
            # extra_user_content_parts as independent TextPart blocks.
            if TextPart is not None and hasattr(req, "extra_user_content_parts"):
                position = (getattr(cfg, "auto_inject_position", "before") or "before").lower()
                parts_list = getattr(req, "extra_user_content_parts", None)
                if parts_list is not None:
                    for _kind, text in blocks:
                        part = TextPart(text=text, type="text").mark_as_temp()
                        if position == "after":
                            parts_list.append(part)
                        else:
                            parts_list.insert(0, part)
                    return
            # Fallback: pre-v4 AstrBot without TextPart support — raw concat.
            block = "\n\n".join(b for _, b in blocks)
            position = (getattr(cfg, "auto_inject_position", "before") or "before").lower()
            prompt = getattr(req, "prompt", "") or ""
            if position == "after":
                req.prompt = (prompt + "\n\n" + block) if prompt else block
            else:
                req.prompt = (block + "\n\n" + prompt) if prompt else block
        except Exception as ex:
            print("[hippocampus] auto inject skipped: " + repr(ex))