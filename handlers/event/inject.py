"""InjectHandler: v1.5 auto memory injection on the on_llm_request hook.

When `auto_inject_enabled` is on, this runs before every LLM call:
it recalls the top-k relevant engrams for the current user message and
splices their summaries into `req.prompt` (before/after). Default off,
so the plugin's behaviour is unchanged unless the user opts in.

Errors here must never abort the LLM request, so the whole body is
guarded; on any failure we simply skip injection.
"""
from __future__ import annotations
from collections import deque
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
        # v1.72 (issue 2026-07-29 #3): cross-turn dedup of diary chunks
        # to break the same-chunk-reappears-every-on_llm_request loop.
        # Content-hash set bounded by a deque so long sessions do not leak.
        self._seen_diary: "deque[str]" = deque(maxlen=64)
        # v1.72b: loud version banner. Operator should see this in
        # AstrBot logs after plugin reload. If absent, Python is still
        # serving cached module - hard-kill AstrBot process + restart,
        # not just /plugin reload.
        try:
            print("[hippocampus] v1.72b loaded: diary-label=\xe6\x9c\x80\xe8\xbf\x91\xe6\x97\xa5\xe8\xae\xb0, "
                  "LRU dedup=enabled, recent-dialog engram.id dedup=enabled, "
                  "_fallback=return None", flush=True)
        except Exception:
            pass

    @staticmethod
    def _wrap_engram(body: str) -> str:
        """Wrap an injected block in <engram-context> for LLM-side distinction.

        v1.67.1 (issue #8): the XML tag signals to the LLM that the
        content is injected background, not part of the user's actual
        message. Without it, the LLM was treating each TextPart block
        as a parallel user question and answering them all.
        """
        return f"<engram-context>\n{body}\n</engram-context>"

    # v1.67.2 (re-injection defense): triple match used to identify
    # our own previously-injected blocks. All three must be present
    # to count as one of ours — open tag, close tag, and at least
    # one of the four known inner labels. This makes false-positive
    # removal of another plugin's TextPart extremely unlikely.
    _ENGRAM_OPEN = "<engram-context>"
    _ENGRAM_CLOSE = "</engram-context>"
    _ENGRAM_INNER_LABELS = (
        "[用户画像]", "[人物关系]", "[近期对话]",
        "[今日回顾]",  # legacy v1.72 keep-strip label
        "[最近日记]",   # v1.72+: truthful label (diary is for prev day)
    )

    @classmethod
    def _strip_prior_engram_blocks(cls, parts_list) -> int:
        """Remove any prior engram TextParts from the parts list in place.

        Returns the number of parts removed.  Mirrors
        emotion_state_machine's find-and-replace pattern (HTML comment
        sentinels) but uses the XML tag we already emit (v1.67.1).  The
        goal is the same: prevent unbounded accumulation of our own
        blocks across multiple ``on_llm_request`` firings within the
        same conversation (e.g. retries, multi-turn sessions where
        ``extra_user_content_parts`` is not reset between turns).
        """
        if not parts_list:
            return 0
        kept = []
        removed = 0
        for p in parts_list:
            text = getattr(p, "text", None)
            if not isinstance(text, str):
                kept.append(p)
                continue
            stripped = text.strip()
            if (stripped.startswith(cls._ENGRAM_OPEN)
                    and stripped.endswith(cls._ENGRAM_CLOSE)
                    and any(lab in stripped for lab in cls._ENGRAM_INNER_LABELS)):
                removed += 1
                continue
            kept.append(p)
        if removed:
            parts_list[:] = kept
        return removed

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
                        # v1.72 (issue 2026-07-29 #3): skip chunks whose text
                        # we already injected recently (LRU via
                        # self._seen_diary). Diary recall has no time filter
                        # (deeper root of #3), so the same chunk can
                        # resurface every on_llm_request firing.
                        # Label also renamed (jinri huigu -> zuijin riji)
                        # since the diary generator runs at noon for the
                        # PREVIOUS day (diary_trigger_hour=12); the old
                        # label was structurally a misnomer.
                        seen = self._seen_diary
                        fresh = [(t, sc) for t, sc in hits
                                 if (t or "").strip() and t not in seen]
                        # Update LRU (deque(maxlen=64) auto-evicts oldest).
                        for t, _sc in fresh:
                            seen.append(t)
                        dlines = ["- " + t for t, _sc in fresh]
                        if dlines:
                            diary_block = "[最近日记]\n" + "\n".join(dlines)
                except Exception as dex:
                    print("[hippocampus] diary inject skipped: " + repr(dex))

            # Persona (background) -> relations -> recent conversation -> diary.
            # v1.67.1 (issue #8): each block is wrapped in
            # <engram-context>...</engram-context> so the LLM can
            # pattern-match injected background separately from the
            # real user message; the inner [xxx] label is preserved
            # for backward compatibility with anyone pattern-matching
            # on it.
            blocks: list[tuple[str, str]] = []
            if persona_block:
                blocks.append(("persona", self._wrap_engram(persona_block)))
            if relation_block:
                blocks.append(("relation", self._wrap_engram(relation_block)))
            if memory_block:
                blocks.append(("memory", self._wrap_engram(memory_block)))
            if diary_block:
                blocks.append(("diary", self._wrap_engram(diary_block)))
            if not blocks:
                return
            # v1.66: use structured TextPart instead of raw prompt concatenation.
            # Each block becomes its own TextPart (marked temp so it never
            # enters conversation history). This follows the social_context /
            # ESM v0.9.x pattern: static rules in prompt=, dynamic data in
            # extra_user_content_parts as independent TextPart blocks.
            #
            # v1.67.1 (issue #8): the previous loop used
            # `parts_list.insert(0, part)` in the "before" branch,
            # which is LIFO and reversed the declared order
            # persona->relation->memory->diary. Build the new parts
            # first, then splice them in one shot so the order is
            # preserved.
            if TextPart is not None and hasattr(req, "extra_user_content_parts"):
                position = (getattr(cfg, "auto_inject_position", "before") or "before").lower()
                parts_list = getattr(req, "extra_user_content_parts", None)
                if parts_list is not None:
                    # v1.67.2 (re-injection defense): strip any of our
                    # own engram blocks that were left over from a
                    # prior on_llm_request firing (retries, multi-turn
                    # sessions where parts_list is not reset). Without
                    # this, the list grows by 4 per turn and eventually
                    # dominates the LLM context window.
                    self._strip_prior_engram_blocks(parts_list)
                    new_parts = [TextPart(text=text, type="text").mark_as_temp()
                                 for _kind, text in blocks]
                    if position == "after":
                        parts_list.extend(new_parts)
                    else:
                        parts_list[0:0] = new_parts
                    return
            # Fallback: pre-v4 AstrBot without TextPart support — raw concat.
            # Each block is already wrapped by _wrap_engram above, so the
            # joined string carries the <engram-context> tags directly.
            block = "\n\n".join(b for _, b in blocks)
            position = (getattr(cfg, "auto_inject_position", "before") or "before").lower()
            prompt = getattr(req, "prompt", "") or ""
            if position == "after":
                req.prompt = (prompt + "\n\n" + block) if prompt else block
            else:
                req.prompt = (block + "\n\n" + prompt) if prompt else block
        except Exception as ex:
            print("[hippocampus] auto inject skipped: " + repr(ex))