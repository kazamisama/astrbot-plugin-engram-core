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
                persona_id=None,
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
        cut = store.find_idle_gap(channel_id, boundary_epoch, win_end, min_gap_seconds, persona_id=persona_id)
    except Exception:
        cut = None
    if cut is not None:
        return cut
    return fallback if fallback is not None else boundary_epoch


def target_length(total_chars: int, ratio_per_person: float,
                  participants_excl_self: int, *,
                  floor: int = 0, cap: int = 0) -> int:
    """Diary compression: total * (ratio / max(1, participants))."""
    n = max(1, int(participants_excl_self))
    t = int(round(max(0, total_chars) * (max(0.0, ratio_per_person) / n)))
    if floor > 0:
        t = max(t, floor)
    if cap > 0:
        t = min(t, cap)
    return t


_SYS_BASE = (
    "\u4f60\u662f\u8fd9\u4e2a\u804a\u5929\u673a\u5668\u4eba\u672c\u4eba\u3002"
    "\u8bf7\u4ee5\u7b2c\u4e00\u4eba\u79f0\uff08\u201c\u6211\u201d\uff09\u7684\u53e3\u543b\uff0c"
    "\u628a\u4eca\u5929\u53d1\u751f\u7684\u4e8b\u60c5\u5199\u6210\u4e00\u7bc7\u65e5\u8bb0\u3002"
    "\u6309\u65f6\u95f4\u987a\u5e8f\u53d9\u8ff0\uff0c\u660e\u786e\u4eba\u7269\u3001\u8bdd\u9898\u4e0e\u65f6\u95f4\uff0c"
    "\u53ef\u4ee5\u5e26\u4e3b\u89c2\u611f\u53d7\u3002\u4e25\u683c\u8f93\u51fa JSON\u3002"
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


def _build_prompt(lines: list, target_chars: int, day_label: str) -> str:
    head = (
        "\u4ee5\u4e0b\u662f " + day_label + " \u7684\u5168\u90e8\u5bf9\u8bdd"
        "\uff08\u542b\u4f60\u81ea\u5df1\u7684\u53d1\u8a00\uff09\u3002\u8bf7\u5199\u6210\u7ea6 "
        + str(target_chars) +
        " \u5b57\u7684\u7b2c\u4e00\u4eba\u79f0\u65e5\u8bb0\u3002\u8fd4\u56de JSON\uff0c\u952e\uff1a"
        "summary(\u65e5\u8bb0\u6b63\u6587), key_facts(\u8981\u70b9\u5217\u8868), "
        "topics(\u8bdd\u9898), participants(\u53c2\u4e0e\u4eba)\u3002\n\n"
    )
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
        base = _SYS_BASE
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
        npart = _participants_excl_self(lines)
        target = target_length(total, self._ratio(), npart,
                               floor=self._floor(), cap=self._cap())
        result = self._llm_compose(lines, target, day_label)
        if result is None:
            result = self._fallback(lines, target)
        result.setdefault("summary", "")
        result.setdefault("key_facts", [])
        result.setdefault("topics", [])
        result.setdefault("participants", [])
        result["_target_chars"] = target
        result["_source_total_chars"] = total
        result["_participants_excl_self"] = npart
        result["_first_ts"] = lines[0].ts
        result["_last_ts"] = lines[-1].ts
        return result

    def _llm_compose(self, lines, target: int, day_label: str) -> dict | None:
        if isinstance(self._llm, RuleLLMProvider):
            return None
        try:
            sys = self._system_prompt(lines)
            user = _build_prompt(lines, target, day_label)
            raw = self._llm.chat(sys, user, temperature=0.4,
                                 max_tokens=min(2048, max(256, target * 2)))
        except Exception as ex:
            print("[hippocampus] diary llm error: " + repr(ex))
            return None
        if not raw:
            return None
        data = _parse_json(raw)
        if not isinstance(data, dict) or not (data.get("summary") or "").strip():
            return None
        return _normalize(data)

    def _fallback(self, lines, target: int) -> dict:
        text = _transcript(lines).replace("\n", " ")
        if target > 0 and len(text) > target:
            text = text[:target].rstrip() + "\u2026"
        parts = []
        for ln in lines:
            if not ln.is_bot and ln.actor_id and ln.actor_id not in parts:
                parts.append(ln.actor_id)
        return {"summary": text, "key_facts": [], "topics": [],
                "participants": parts}


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
