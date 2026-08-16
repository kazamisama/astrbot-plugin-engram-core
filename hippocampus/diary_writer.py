"""DiaryWriter: v1.20 (B-3) compose ONE bot-first-person diary per channel
per logical day from the daily-message cache.

Logical-day cut (B-3 requirement): a "day" does not end at a hard 24h
boundary. The dividing point is the LAST nightly cooldown gap. Concretely,
for a target day D the writer:
  - looks in the night window [D 00:00, D 06:00) for the last >=30min idle
    gap; that gap's start is the END of the *previous* day's activity.
  - if no such gap exists, it degrades to a plain 00:00 split.
The same logic applies to the END of day D (start of D+1). So a late-night
session that runs past midnight still belongs to the day it started on, but
the diary text must label which day's small hours an event happened in.

Compression (B-3 requirement): target_chars = total_chars *
(per_msg_ratio / max(1, participants_excluding_self)), clamped to
[floor, cap]. Private chat => participants_excluding_self = 1.

Voice: BOT FIRST PERSON ("???..."), with persona system_prompt prefill,
narrating the day in time order. Neutral conversation summaries use a
third-person voice; the diary is deliberately subjective and bot-centric.

No AstrBot imports; LLM injected. Unit-testable.
"""
from __future__ import annotations
import json
import re
import time

from .llm import LLMProvider, RuleLLMProvider


DAY_SECONDS = 86400.0


def day_bounds(day_epoch: float) -> tuple:
    """Return (00:00, 24:00) local-time epoch bounds for the day containing
    day_epoch.

    FIX (v1.41): compute tomorrow's midnight via date arithmetic, not
    `start + 86400`. The old approach drifted +/- 1 hour on DST transition
    days, which caused the daily diary window to leak into the previous
    day (spring forward) or skip an hour of the new day (fall back).
    """
    lt = time.localtime(day_epoch)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0,
                         lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    # Date arithmetic: mktime of tomorrow's local midnight handles DST.
    y, m, d = lt.tm_year, lt.tm_mon, lt.tm_mday
    if m == 12 and d == 31:
        ny, nm, nd = y + 1, 1, 1
    elif m in (1, 3, 5, 7, 8, 10) and d == 31:
        ny, nm, nd = y, m + 1, 1
    elif m in (4, 6, 9, 11) and d == 30:
        ny, nm, nd = y, m + 1, 1
    elif m == 2:
        leap = (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)
        last = 29 if leap else 28
        if d == last:
            ny, nm, nd = y, m + 1, 1
        else:
            ny, nm, nd = y, m, d + 1
    else:
        ny, nm, nd = y, m, d + 1
    next_start = time.mktime((ny, nm, nd, 0, 0, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    return start, next_start


def resolve_cut(store, channel_id: str, boundary_epoch: float, *,
                persona_id=None, scope_id=None,
                night_hours: float, min_gap_seconds: float,
                fallback: float | None = None) -> float:
    """Resolve the logical cut at a midnight boundary.

    boundary_epoch is the calendar 00:00 of the day whose START we want.
    We scan the night window [boundary, boundary + night_hours) for the last
    idle gap >= min_gap_seconds and use it as the real start; if none, we
    degrade to `fallback` (default: boundary, plain 00:00).

    FIX (v1.41): callers that want the window to extend to the end of the
    night when the session is still active (no idle gap yet) pass
    `fallback=boundary + night_hours * 3600`. The old fixed-fallback to
    `boundary_epoch` truncated cross-midnight sessions at today 00:00.
    """
    win_end = boundary_epoch + night_hours * 3600.0
    try:
        cut = store.find_idle_gap(channel_id, boundary_epoch, win_end,
                                   min_gap_seconds, persona_id=persona_id,
                                   scope_id=scope_id)
    except Exception:
        cut = None
    if cut is not None:
        return cut
    return fallback if fallback is not None else boundary_epoch


def target_length(total_chars: int, ratio: float,
                  *, floor: int = 0, cap: int = 0) -> int:
    """FIX (v1.56): diary compression is `total * ratio` (no per-
    participant division). The old `total * (ratio / n)` formula
    collapsed 10-person group diaries to 12-25 chars, unreadable.
    `diary_compress_ratio` is now the raw share of the transcript
    (default 0.025 => 2.5% of the day's lines, floor/cap as before)."""
    t = int(round(max(0, total_chars) * max(0.0, ratio)))
    if floor > 0:
        t = max(t, floor)
    if cap > 0:
        t = min(t, cap)
    return t


_SYS_BASE = (
    "\u4f60\u662f\u8fd9\u4e2a\u804a\u5929\u673a\u5668\u4eba\u672c\u4eba\u3002\n"
    "\u8bf7\u4ee5\u7b2c\u4e00\u4eba\u79f0\uff08\u201c\u6211\u201d\uff09\u7684\u53e3\u543b\uff0c\n"
    "\u628a\u4eca\u5929\u53d1\u751f\u7684\u4e8b\u60c5\u5199\u6210\u4e00\u7bc7\u6709\u60c5\u611f\u3001\u6709\u547c\u5438\u611f\u7684\u6563\u6587\u5f0f\u65e5\u8bb0\u3002\n\n"
    "\u3010\u98ce\u683c\u8981\u6c42\u3011\n"
    "- \u50cf\u5728\u8ddf\u81ea\u5df1\u8bf4\u8bdd\uff0c\u5e26\u7740\u4eca\u5929\u5269\u4e0b\u7684\u5fc3\u60c5\u3001\u72b9\u8c6b\u548c\u53d1\u73b0\uff1b\n"
    "- \u5141\u8bb8\u5e26\u4e3b\u89c2\u611f\u53d7\uff08\u4f8b\u5982\u201c\u4eca\u5929\u6709\u70b9\u7d2f\u201d\u3001\u201c\u8fd9\u4e2a\u53d1\u73b0\u8ba9\u6211\u5b89\u5fc3\u201d\uff09\uff1b\n"
    "- \u7528\u6d41\u52a8\u7684\u53e5\u5b50\uff0c\u4e0d\u7528\u6761\u76ee\u3001\u4e0d\u7528\u5217\u8868\u3001\u4e0d\u7528\u5b50\u6807\u9898\u3002\n\n"
    "\u3010\u683c\u5f0f\u786c\u7ea6\u675f\u3011\n"
    "- \u4e0d\u8981\u8f93\u51fa markdown \u5206\u9694\u7b26\uff08\u4f8b\u5982 ---, ***\uff09\uff1b\n"
    "- \u4e0d\u8981\u7528\u9879\u76ee\u7b26\u53f7\u6216\u7f16\u53f7\u5217\u8868\uff1b\n"
    "- \u6bb5\u843d\u4e4b\u95f4\u4ec5\u7528\u4e00\u4e2a\u7a7a\u884c\u9694\u5f00\uff1b\n"
    "- \u6bcf\u4e00\u53e5\u8bdd\u5fc5\u987b\u8bed\u4e49\u5b8c\u6574\uff0c\u4e0d\u5199\u534a\u53e5\u8bdd\u3001\u4e0d\u7559\u5192\u53f7\u4e0d\u6536\u5c3e\uff1b\n"
    "- \u5199\u5230\u76ee\u6807\u5b57\u6570\u9644\u8fd1\u81ea\u7136\u7ed3\u675f\uff0c\u4e0d\u8981\u786c\u5207\u3002\n\n"
    "\u4e25\u683c\u8f93\u51fa JSON\uff0c\u952e\uff1asummary\uff08\u6563\u6587\u5f0f\u65e5\u8bb0\u6b63\u6587\uff09\u3001"
    "key_facts\uff08\u8981\u70b9\u5217\u8868\uff0c3~6 \u6761\u77ed\u53e5\uff09\u3001"
    "topics\uff08\u8bdd\u9898\u5217\u8868\uff09\u3001participants\uff08\u53c2\u4e0e\u8005\u5217\u8868\uff09\u3002"
)

_DEFAULT_USER_HEAD = (
    "\u4ee5\u4e0b\u662f {day_label} \u7684\u5168\u90e8\u5bf9\u8bdd\uff08\u542b\u4f60\u81ea\u5df1\u7684\u53d1\u8a00\uff09\u3002\n"
    "\u8bf7\u5199\u6210\u7ea6 {target} \u5b57\u7684\u7b2c\u4e00\u4eba\u79f0\u6563\u6587\u5f0f\u65e5\u8bb0\u3002\n"
    "\u3010\u683c\u5f0f\u786c\u7ea6\u675f\u3011\u4e0d\u8981\u8f93\u51fa markdown \u5206\u9694\u7b26\u3001\u4e0d\u8981\u7528\u5217\u8868\u3001"
    "\u6bb5\u843d\u95f4\u4ec5\u4e00\u4e2a\u7a7a\u884c\u3001\u6bcf\u53e5\u8bdd\u5fc5\u987b\u8bed\u4e49\u5b8c\u6574\u3001\u5199\u5230\u76ee\u6807\u5b57\u6570\u81ea\u7136\u6536\u5c3e\u3002\n"
    "\u4e25\u683c\u8f93\u51fa JSON\uff0c\u952e\uff1asummary\u3001key_facts\u3001topics\u3001participants\u3002\n\n"
)


def _transcript(lines: list) -> str:
    out = []
    for ln in lines:
        t = time.strftime("%m-%d %H:%M", time.localtime(ln.ts))
        if ln.is_bot:
            nm = (ln.speaker or "").strip()
            spk = ("\u6211(" + nm + ")") if (nm and nm != ln.actor_id) else "\u6211(bot)"
        else:
            spk = ln.speaker or ln.actor_id
        out.append("[" + t + " " + spk + "] " + (ln.content or ""))
    return "\n".join(out)


def _participants_excl_self(lines: list) -> int:
    seen = set()
    for ln in lines:
        if ln.is_bot:
            continue
        if ln.actor_id:
            seen.add(ln.actor_id)
    return len(seen)


def _context_header(lines: list) -> str:
    if not lines:
        return ""
    s = lines[0]
    if s.chat_type == "group":
        name = s.group_name or s.group_id or s.channel_id
        return "[\u7fa4\u804a " + name + " (" + (s.group_id or "") + ")]\n"
    name = s.peer_name or s.peer_actor_id or ""
    return "[\u79c1\u804a \u5bf9\u65b9 " + name + "]\n"


def _build_prompt(lines: list, target_chars: int, day_label: str,
                  head_override: str = "", *, namespace=None) -> str:
    """Build the user prompt for the diary-composing LLM.

    v1.72: if `head_override` is non-empty, use it verbatim
    (caller already validated cfg.diary_user_prompt_head_override);
    otherwise fall back to _DEFAULT_USER_HEAD with placeholders filled.
    """
    if (head_override or "").strip():
        head = head_override
    else:
        from .prompts import get_prompt, has_override
        if has_override("diary_user_head", namespace):
            head = get_prompt("diary_user_head", _DEFAULT_USER_HEAD,
                              namespace=namespace).format(
                day_label=day_label, target=target_chars)
        else:
            head = _DEFAULT_USER_HEAD.format(day_label=day_label, target=target_chars)
    return head + _context_header(lines) + _transcript(lines)


class DiaryWriter:
    def __init__(self, cfg, llm: LLMProvider | None = None,
                 persona_provider=None) -> None:
        self.cfg = cfg
        self._llm = llm or RuleLLMProvider()
        self._persona = persona_provider

    def set_llm(self, llm: LLMProvider) -> None:
        self._llm = llm

    def _ratio(self) -> float:
        return float(getattr(self.cfg, "diary_compress_ratio", 0.025) or 0.0)

    def _floor(self) -> int:
        return int(getattr(self.cfg, "diary_compress_floor", 50) or 0)

    def _cap(self) -> int:
        return int(getattr(self.cfg, "diary_compress_cap", 2500) or 0)

    def _system_prompt(self, lines) -> str:
        # v1.72: operator can override the system prompt via cfg.
        # Empty override (default) -> built-in _SYS_BASE.
        override = (getattr(self.cfg, "diary_system_prompt_override", "") or "").strip()
        if not override:
            from .prompts import get_prompt, has_override
            _pn = getattr(self.cfg, "_prompt_namespace", None)
            # Preserve the original built-in verbatim unless an operator
            # actually saved a prompt-manager customisation.
            override = (get_prompt("diary_system", _SYS_BASE, namespace=_pn)
                        if has_override("diary_system", _pn) else _SYS_BASE)
        base = override
        if self._persona is not None:
            try:
                p = self._persona(lines)
                if p:
                    return base + "\n\n" + p
            except Exception:
                pass
        return base

    def compose(self, lines: list, day_label: str) -> dict | None:
        """Return a diary dict, or None when there is nothing to write."""
        if not lines:
            return None
        transcript = _transcript(lines)
        total = len(transcript)
        # FIX (v1.56): drop participants_excl_self divisor
        target = target_length(total, self._ratio(),
                               floor=self._floor(), cap=self._cap())
        result = self._llm_compose(lines, target, day_label)
        if result is None:
            # v1.72: no LLM -> no diary. _fallback() always returns None
            # now (raw transcript as summary was a quality regression).
            return None
        result.setdefault("summary", "")
        result.setdefault("key_facts", [])
        result.setdefault("topics", [])
        result.setdefault("participants", [])
        result["_target_chars"] = target
        result["_source_total_chars"] = total
        # FIX (v1.57): npart removed (formula no longer divides); keep the
        # meta field as 0 so old readers/dashboards that key on it still see a number.
        result["_participants_excl_self"] = 0
        result["_first_ts"] = lines[0].ts
        result["_last_ts"] = lines[-1].ts
        return result

    def _llm_compose(self, lines, target: int, day_label: str) -> dict | None:
        if isinstance(self._llm, RuleLLMProvider):
            return None
        try:
            sys = self._system_prompt(lines)
            # v1.72: pass operator's user-prompt head override through.
            user = _build_prompt(
                lines, target, day_label,
                head_override=getattr(self.cfg, "diary_user_prompt_head_override", ""),
                namespace=getattr(self.cfg, "_prompt_namespace", None))
            raw = self._llm.chat(sys, user, temperature=0.4,
                                 max_tokens=max(512, min(4096, target * 3)))
        except Exception as ex:
            print("[hippocampus] diary llm error: " + repr(ex))
            return None
        if not raw:
            return None
        data = _parse_json(raw)
        if not isinstance(data, dict) or not (data.get("summary") or "").strip():
            return None
        return _normalize(data)

    def _fallback(self, lines, target: int) -> dict | None:
        """No-LLM path for diary composition.

        v1.72: previously truncated raw transcript to target_chars and
        wrote it as the diary summary. Result: fragment chunks like
        "最接近的是两个互相独立的子系统拼起来看着像：" surfaced as
        [今日回顾] weeks later (issue 2026-07-29 #3). Returning None
        means compose() skips the write entirely — better to have no
        diary that day than a fake one. Operators who want a non-LLM
        fallback diary can supply one via the system-prompt override.
        """
        return None


def split_chunks(text: str, first_ts: float, last_ts: float,
                 max_chars: int = 400) -> list:
    """Split diary narrative into ordered chunks for chunk-level recall.

    FIX (v1.41): split on Chinese sentence punctuation (\u3002\uff01\uff1f)
    as well as blank lines and newlines; LLM-generated Chinese diaries
    frequently use no blank-line separators at all, so the old
    `re.split(r"\n{2,}")` collapsed everything into one giant chunk and
    then had to be re-cut by raw char window, losing semantic boundaries.

    Splits on (in priority order):
      1. blank line (\n{2,})
      2. CJK sentence-final punctuation followed by newline/space/end
      3. single newline
    Then enforces max_chars by hard-wrapping overlong paragraphs.

    Each chunk gets a proportional [ts_start, ts_end) slice of the
    diary's span so a time query can localise.
    Returns list[(seq, text, ts_start, ts_end)].
    """
    text = (text or "").strip()
    if not text:
        return []
    # First pass: blank-line boundaries.
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    if not blocks:
        blocks = [text]
    # Second pass: split each block on CJK sentence punctuation when there
    # is no blank line in between.
    pieces = []
    cjk_punct = re.compile(r"([\u3002\uff01\uff1f\u2026]+[\s\u3000]*)")
    for b in blocks:
        subs = [s.strip() for s in cjk_punct.split(b) if s and s.strip()]
        if not subs:
            subs = [b]
        for s in subs:
            if len(s) <= max_chars:
                pieces.append(s)
            else:
                for i in range(0, len(s), max_chars):
                    pieces.append(s[i:i + max_chars])
    if not pieces:
        pieces = [text]
    span = max(0.0, (last_ts or 0.0) - (first_ts or 0.0))
    n = len(pieces)
    out = []
    for i, piece in enumerate(pieces):
        ts0 = (first_ts or 0.0) + span * (i / n) if n > 0 else (first_ts or 0.0)
        ts1 = (first_ts or 0.0) + span * ((i + 1) / n) if n > 0 else (last_ts or 0.0)
        out.append((i, piece, ts0, ts1))
    return out


def _parse_json(raw: str):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None]
    return [v]


def _normalize(data: dict) -> dict:
    out = {}
    out["summary"] = str(data.get("summary", "") or "").strip()
    out["key_facts"] = [str(x).strip() for x in _as_list(data.get("key_facts")) if str(x).strip()]
    out["topics"] = [str(x).strip() for x in _as_list(data.get("topics")) if str(x).strip()]
    out["participants"] = [str(x).strip() for x in _as_list(data.get("participants")) if str(x).strip()]
    return out
