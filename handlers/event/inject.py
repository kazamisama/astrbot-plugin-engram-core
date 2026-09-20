"""InjectHandler: v1.5 auto memory injection on the on_llm_request hook.

When `auto_inject_enabled` is on, this runs before every LLM call:
it recalls the top-k relevant engrams for the current user message and
splices their summaries into `req.prompt` (before/after). Default off,
so the plugin's behaviour is unchanged unless the user opts in.

Errors here must never abort the LLM request, so the whole body is
guarded; on any failure we simply skip injection.
"""
from __future__ import annotations
import asyncio
import threading
from collections import deque
from typing import TYPE_CHECKING
from ..format import _extract
from hippocampus.reltime import relative_label
try:
    from astrbot.core.agent.message import TextPart
except ImportError:
    TextPart = None  # pre-v4 AstrBot: fallback to string concat
from engram_core_helpers import strip_injected_blocks  # v1.73: public re-injection defense

# v1.76.15: cap concurrent injection workers so timed-out injections cannot
# pile up threads on the default executor. When saturated, injection is
# skipped for that LLM request (injection is best-effort by contract).
_INJECT_MAX_CONCURRENCY = 4
_INJECT_SLOTS = threading.BoundedSemaphore(_INJECT_MAX_CONCURRENCY)
if TYPE_CHECKING:
    from hippocampus import MemoryService

# v1.76.21: block label for recalled episodic/semantic engrams. The old
# "[近期对话]" claimed the entries were recent conversation, while every
# entry carries its own real relative-time marker ("[3个月前]", "[2 天前]")
# on the very same line — the two contradicted each other. The block holds
# long-term memory recalled from any age, so name it that.
_MEMORY_BLOCK_LABEL = "[长期记忆]"
# Kept as a legacy label purely for the re-injection defence below: blocks
# injected by <= v1.76.20 carry it and still have to be stripped.
_LEGACY_MEMORY_BLOCK_LABEL = "[近期对话]"


def _engram_body(engram, *, use_content: bool, max_chars: int) -> str:
    """Body text to inject for one recalled engram.

    v1.76.21: prefer ``content`` over ``summary``. ``content`` is the
    summary followed by the summarizer's "- key fact" bullet lines (true
    for all 399 stored engrams when this was written), so injecting
    ``summary`` alone silently dropped every extracted key fact.

    ``max_chars`` is a SOFT cap applied at whole-line boundaries: lines are
    kept until the budget is spent, so a bullet is never cut mid-sentence.
    The first line (the summary) is always kept even when it alone exceeds
    the cap — a truncated summary is worth less than a slightly
    over-budget one. ``max_chars <= 0`` disables the cap.
    """
    summary = (getattr(engram, "summary", "") or "").strip()
    content = (getattr(engram, "content", "") or "").strip() if use_content else ""
    body = content or summary
    if not body or max_chars <= 0 or len(body) <= max_chars:
        return body
    kept: list[str] = []
    used = 0
    for line in body.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if kept and used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


# v1.76.22: the <engram-context> wrapper used to say only what the block is
# NOT ("injected background, not the user's message"). On the live bot that
# negative framing got promoted into a positive licence: the model reasoned
# "memories are background; the current conversation governs" and used it to
# justify not drawing on a correctly-recalled memory. Structurally the blocks
# are fine -- what was missing is the other half of the sentence: they are the
# assistant's OWN recall and are meant to be used.
#
# This is candidate 8.3 from docs/TODO.md §2.8, which was declined on
# 2026-07-03 for three reasons -- all of which this implementation avoids:
#   * "跨插件协调成本" -- this is NOT a global system-prompt template change;
#     it travels inside our own temp TextPart, so no other plugin is involved.
#   * "~50 token/请求" -- exactly one note per REQUEST, not one per block (see
#     the preamble flag below), so the budget is the documented ~50 tokens.
#   * the trigger did not exist yet -- §2.8's own "下次评估点" was "LLM 把
#     <engram-context> 标签当作文本复读 / 误解的首次真机报告", and that report
#     has now happened.
# Note the wording deliberately differs from the declined 8.3 draft, which
# repeated "这是自动注入的背景" -- that is the very reading that caused the
# trouble. This states the positive half instead.
_SELF_RECALL_NOTE = (
    "（以下内容是记忆系统自动附上的，不是你收到的用户消息，不要逐条回应；"
    "也不要当成可以忽略的背景——它是你自己的记忆，与当前话题相关时按事实使用。）"
)


class InjectHandler:
    """Auto-inject recalled memories into the outgoing LLM request."""

    def __init__(self, service: "MemoryService | None") -> None:
        self.service = service
        # v1.72 (issue 2026-07-29 #3): cross-turn dedup of diary chunks
        # to break the same-chunk-reappears-every-on_llm_request loop.
        # Content-hash set bounded by a deque so long sessions do not leak.
        self._seen_diary: "deque[str]" = deque(maxlen=64)
        self._inject_lock = None
        # v1.72b: loud version banner. Operator should see this in
        # AstrBot logs after plugin reload. If absent, Python is still
        # serving cached module - hard-kill AstrBot process + restart,
        # not just /plugin reload.
        try:
            print("[hippocampus] v1.73 loaded: diary-label=\xe6\x9c\x80\xe8\xbf\x91\xe6\x97\xa5\xe8\xae\xb0, "
                  "LRU dedup=enabled, recent-dialog engram.id dedup=enabled, "
                  "_fallback=return None, strip_injected_blocks=public", flush=True)
        except Exception:
            pass

    @staticmethod
    def _wrap_engram(body: str, *, preamble: bool = False) -> str:
        """Wrap an injected block in <engram-context> for LLM-side distinction.

        v1.67.1 (issue #8): the XML tag signals to the LLM that the
        content is injected background, not part of the user's actual
        message. Without it, the LLM was treating each TextPart block
        as a parallel user question and answering them all.

        v1.76.22: the structural signal above was necessary but not
        sufficient — on its own it reads as "this is background", which the
        live model turned into "background is ignorable". ``preamble=True``
        prepends ``_SELF_RECALL_NOTE`` to supply the missing half: these are
        the assistant's own memories and are meant to be used. The caller sets
        it on the FIRST block of a request only, so the note costs its ~50
        tokens once per request rather than once per block.
        """
        note = (_SELF_RECALL_NOTE + "\n") if preamble else ""
        return f"<engram-context>\n{note}{body}\n</engram-context>"

    # v1.67.2 (re-injection defense): triple match used to identify
    # our own previously-injected blocks. All three must be present
    # to count as one of ours — open tag, close tag, and at least
    # one of the four known inner labels. This makes false-positive
    # removal of another plugin's TextPart extremely unlikely.
    # v1.73: matching logic extracted to the public
    # engram_core_helpers.strip_injected_blocks; this class only keeps
    # its own root tag + inner labels and delegates.
    _ENGRAM_ROOT_TAG = "engram-context"
    _ENGRAM_INNER_LABELS = (
        "[用户画像]", "[人物关系]", _MEMORY_BLOCK_LABEL,
        _LEGACY_MEMORY_BLOCK_LABEL,  # v1.76.21: strips pre-rename blocks
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

        v1.73: delegates to the public
        ``engram_core_helpers.strip_injected_blocks`` so external
        plugins writing to ``extra_user_content_parts`` can run the
        same re-injection defense under their own root tag.
        """
        return strip_injected_blocks(
            parts_list,
            root_tag=cls._ENGRAM_ROOT_TAG,
            inner_labels=cls._ENGRAM_INNER_LABELS,
        )

    def _get_inject_lock(self):
        """Serialize injection runs; recall + cache writes are not
        re-entrant across event-loop and worker-thread callers."""
        if self._inject_lock is None:
            self._inject_lock = asyncio.Lock()
        return self._inject_lock

    # v1.76.13 (2026-09-07 astrbot freeze): hard cap on the whole
    # injection. If recall hangs (embedding HTTP without a provider
    # timeout, SQLite lock, dead fs, ...) the hook must give up and
    # release the LLM request WITHOUT injection instead of stalling
    # the on_llm_request chain forever. 10s default; configurable via
    # auto_inject_timeout (1-120s, clamped).
    _INJECT_HARD_TIMEOUT: float = 10.0
    _INJECT_TIMEOUT_MIN: float = 1.0
    _INJECT_TIMEOUT_MAX: float = 120.0

    async def handle_inject(self, event, req) -> None:
        svc = self.service
        if svc is None or req is None:
            return
        cfg = getattr(svc, "cfg", None)
        if cfg is None or not getattr(cfg, "auto_inject_enabled", False):
            return
        timeout = float(getattr(cfg, "auto_inject_timeout", 0.0) or 0.0)
        if timeout <= 0.0:
            timeout = self._INJECT_HARD_TIMEOUT
        timeout = max(self._INJECT_TIMEOUT_MIN,
                      min(timeout, self._INJECT_TIMEOUT_MAX))
        try:
            await asyncio.wait_for(self._run_inject(event, req), timeout=timeout)
        except asyncio.TimeoutError:
            # Circuit breaker tripped: log once and let the LLM request
            # proceed. The worker thread keeps running detached; the
            # per-call run_sync timeout inside it also unblocks it.
            print(f"[hippocampus] auto inject timed out after {timeout:.1f}s "
                  "- proceeding WITHOUT injection (LLM request released)")
        except Exception as ex:
            print("[hippocampus] auto inject error: " + repr(ex))

    async def _run_inject(self, event, req) -> None:
        # v1.76.4 (M5): recall / persona / diary lookups are synchronous
        # SQLite + embedding work. Run them off the event loop while
        # serializing with this handler's lock (req mutation + _seen_diary
        # and service.recall cache are shared state). v1.76.13: bounded
        # by handle_inject's asyncio.wait_for.
        async with self._get_inject_lock():
            if not _INJECT_SLOTS.acquire(blocking=False):
                print("[hippocampus] inject worker saturated; skipping injection")
                return

            def _worker():
                try:
                    self._handle_inject_sync(event, req)
                finally:
                    _INJECT_SLOTS.release()

            await asyncio.to_thread(_worker)

    def _handle_inject_sync(self, event, req) -> None:
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
            scope_scope = (meta.get("scope_id") or "") if iso_on else None

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
                scope_id=scope_scope,
                memory_types=["episodic", "semantic", "prospective"],
                k=top_k))
            engrams = getattr(result, "engrams", None) or []
            show_time = bool(getattr(cfg, "auto_inject_relative_time", True))
            use_content = bool(getattr(cfg, "auto_inject_use_content", True))
            # Never let a malformed cap raise: this whole body sits in a
            # try/except that would abandon injection entirely, so an
            # unusable value must degrade to the default, not to nothing.
            try:
                body_cap = int(getattr(cfg, "auto_inject_content_max_chars", 800))
            except (TypeError, ValueError):
                body_cap = 800
            if body_cap < 0:
                body_cap = 0  # negative reads as "no cap", not as "cap at 0"
            lines = []
            for e in engrams[:top_k]:
                body = _engram_body(e, use_content=use_content, max_chars=body_cap)
                if not body:
                    continue
                label = relative_label(getattr(e, "created_at", 0.0)) if show_time else ""
                # v1.76.21: body may span several lines (summary + key-fact
                # bullets). Indent the continuation lines so a bullet cannot
                # be misread as a separate memory entry, since only the
                # first line carries the "[3个月前]" style time marker.
                rendered = body.replace("\n", "\n  ")
                if label:
                    lines.append("- [" + label + "] " + rendered)
                else:
                    lines.append("- " + rendered)
            memory_block = ((_MEMORY_BLOCK_LABEL + "\n" + "\n".join(lines)) if lines else "")

            # v1.19 B-2: relation injection (option-4 pipeline filter).
            relation_block = ""
            if hasattr(svc, "recall_relations"):
                try:
                    rtop = int(getattr(cfg, "relation_inject_top_n", 3) or 0)
                    if rtop > 0:
                        rmin = float(getattr(cfg, "relation_inject_min_confidence", 0.0) or 0.0)
                        rels = svc.recall_relations(query, top_n=rtop, min_confidence=rmin,
                                                   persona_id=persona_scope,
                                                   scope_id=scope_scope)
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
                        hits = svc.recall_diary_chunks(query, top_n=dtop, min_score=dmin,
                                                      persona_id=persona_scope, scope_id=scope_scope)
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
                        dlines = [t if t.startswith("- ") else "- " + t for t, _sc in fresh]
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
                blocks.append(("persona", persona_block))
            if relation_block:
                blocks.append(("relation", relation_block))
            if memory_block:
                blocks.append(("memory", memory_block))
            if diary_block:
                blocks.append(("diary", diary_block))
            if not blocks:
                return
            # v1.76.22: attach the self-recall note to the first block of this
            # request only — one note per request, not one per block. Block
            # order is preserved, and _strip_prior_engram_blocks still matches
            # every block because the note sits after the opening tag and
            # before the inner [xxx] label.
            blocks = [
                (kind, self._wrap_engram(body, preamble=(idx == 0)))
                for idx, (kind, body) in enumerate(blocks)
            ]
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