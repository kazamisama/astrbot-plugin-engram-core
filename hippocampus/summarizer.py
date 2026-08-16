"""ConversationSummarizer: v1.17 (B-1) turn a per-channel transcript into
one structured summary via the configured LLM, with persona prefill.

Design (mirrors livingmemory's MemoryProcessor where it fits, but triggering
is owned by ConversationBuffer = idle + scheduled, not round-count):
  - proportional compression: target_chars = total_chars * ratio, capped.
  - persona prefill: the summary LLM call gets the speaker/channel persona
    system_prompt so the summary carries the right voice/mood.
  - structured output: the prompt asks for JSON {summary, key_facts, topics,
    participants, relations}. relations carry per-item confidence (used later
    by B-2). Parsing is defensive; on any failure we fall back to a plain
    concatenated transcript excerpt so a summary is always produced.
  - neutral narration (this is the conversation summary; the diary uses a
    bot first-person prompt instead - see B-3).

No AstrBot imports; the LLM is injected (LLMProvider.chat). Unit testable.
"""
from __future__ import annotations
import json
import re

from .llm import LLMProvider, RuleLLMProvider
from .prompts import get_prompt


def target_length(total_chars: int, ratio: float, *,
                  floor: int = 0, cap: int = 0) -> int:
    """Proportional compression target. floor/cap of 0 means unbounded."""
    t = int(round(max(0, total_chars) * max(0.0, ratio)))
    if floor > 0:
        t = max(t, floor)
    if cap > 0:
        t = min(t, cap)
    return t


_SYS_BASE = """你是聊天机器人本人，正在回忆刚才发生的对话。
summary 必须是你自己的主观回忆：以第一人称叙述，体现你的语气和关注点，不要写成第三人称会议纪要。
消息中 [时间 我] 是你自己的发言，必须体现你说了什么。
对话中的相对时间必须转换为具体日期。
summary/key_facts 中必须使用具体昵称，禁止使用“用户/某人/对方”代替具体人名。
严格输出 JSON。"""


def _conversation_date(rec) -> str:
    """Prefer the transcript's first timestamp; fall back to today."""
    import datetime as _dt
    ts = getattr(rec, "first_ts", 0.0) or 0.0
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return _dt.datetime.now().strftime("%Y-%m-%d")


def _build_prompt(rec, target_chars: int, *, namespace=None) -> str:
    date_label = _conversation_date(rec)
    template = get_prompt("summary_user_head", namespace=namespace)
    head = (template.replace("{date_label}", date_label)
            .replace("{target_chars}", str(target_chars)))
    ctx = ""
    if rec.chat_type == "group":
        ctx = f"""群聊 {(rec.group_name or rec.group_id or rec.channel_id)} ({(rec.group_id or '')})
"""
    elif rec.chat_type == "private":
        bot_nm = ""
        try:
            bot_nm = rec.bot_name()
        except Exception:
            bot_nm = ""
        peer = rec.peer_name or rec.peer_actor_id or "对方"
        if bot_nm:
            ctx = f"""私聊 我方 {bot_nm} 对方 {peer}
"""
        else:
            ctx = f"""私聊 对方 {peer}
"""
    return head + ctx + rec.transcript()


class ConversationSummarizer:
    def __init__(self, cfg, llm: LLMProvider | None = None,
                 persona_provider=None) -> None:
        self.cfg = cfg
        self._llm = llm or RuleLLMProvider()
        # persona_provider: callable(rec) -> system_prompt str (or None)
        self._persona = persona_provider

    def set_llm(self, llm: LLMProvider) -> None:
        self._llm = llm

    def _ratio(self) -> float:
        return float(getattr(self.cfg, "summary_compress_ratio", 0.15) or 0.0)

    def _cap(self, chat_type: str = "") -> int:
        if chat_type == "group":
            g = int(getattr(self.cfg, "summary_compress_cap_group", 0) or 0)
            if g > 0:
                return g
        return int(getattr(self.cfg, "summary_compress_cap", 1200) or 0)

    def _floor(self) -> int:
        return int(getattr(self.cfg, "summary_compress_floor", 0) or 0)

    def _system_prompt(self, rec) -> str:
        _pn = getattr(self.cfg, "_prompt_namespace", None)
        base = get_prompt("summary_system", _SYS_BASE, namespace=_pn)
        if self._persona is not None:
            try:
                p = self._persona(rec)
                if p:
                    return base + "\n\n" + p
            except Exception:
                pass
        return base

    def summarize(self, rec) -> dict:
        """Return a structured dict; never raises. Always yields a summary."""
        transcript = rec.transcript()
        total = len(transcript)
        chat_type = getattr(rec, "chat_type", "") or ""
        target = target_length(total, self._ratio(),
                               floor=self._floor(), cap=self._cap(chat_type))
        result = self._llm_summarize(rec, target)
        if result is None:
            if bool(getattr(self.cfg, "summary_fallback_enabled", False)):
                result = self._fallback(rec, target)
            else:
                # 不回退：返回空摘要，下游 store_summary 会因 text 为空跳过写入
                print("[hippocampus] summary skipped: LLM unavailable and fallback disabled")
                result = {"summary": "", "key_facts": [], "topics": [],
                          "participants": [], "relations": []}
        result.setdefault("summary", "")
        result.setdefault("key_facts", [])
        result.setdefault("topics", [])
        result.setdefault("participants", rec.participants(include_bot=False))
        result.setdefault("relations", [])
        try:
            result["participant_names"] = rec.actor_names(include_bot=False)
        except Exception:
            result["participant_names"] = {}
        result["_target_chars"] = target
        result["_source_total_chars"] = total
        return result

    def _llm_summarize(self, rec, target: int) -> dict | None:
        if isinstance(self._llm, RuleLLMProvider):
            return None
        try:
            sys = self._system_prompt(rec)
            user = _build_prompt(rec, target, namespace=getattr(self.cfg, "_prompt_namespace", None))
            raw = self._llm.chat(sys, user, temperature=0.2,
                                 max_tokens=max(512, min(4096, target * 3)))
        except Exception as ex:
            print("[hippocampus] summarizer llm error: " + repr(ex))
            return None
        if not raw:
            return None
        data = _parse_json(raw)
        if not isinstance(data, dict) or not (data.get("summary") or "").strip():
            return None
        return _normalize(data)

    def _fallback(self, rec, target: int) -> dict:
        """No-LLM path: excerpt the transcript up to target length."""
        text = rec.transcript().replace("\n", " ")
        if target > 0 and len(text) > target:
            text = text[:target].rstrip() + "\u2026"
        return {
            "summary": text,
            "key_facts": [],
            "topics": [],
            "participants": rec.participants(include_bot=False),
            "relations": [],
        }


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


_VALID_TYPES = {"person", "place", "object", "org", "unknown"}


def _norm_type(v) -> str:
    t = str(v or "").strip().lower()
    return t if t in _VALID_TYPES else ""


def _normalize(data: dict) -> dict:
    out = {}
    out["summary"] = str(data.get("summary", "") or "").strip()
    out["key_facts"] = [str(x).strip() for x in _as_list(data.get("key_facts")) if str(x).strip()]
    out["topics"] = [str(x).strip() for x in _as_list(data.get("topics")) if str(x).strip()]
    out["participants"] = [str(x).strip() for x in _as_list(data.get("participants")) if str(x).strip()]
    rels = []
    for r in _as_list(data.get("relations")):
        if isinstance(r, dict):
            subj = str(r.get("subject", "") or "").strip()
            rel = str(r.get("relation", "") or "").strip()
            obj = str(r.get("object", "") or "").strip()
            styp = _norm_type(r.get("subject_type"))
            otyp = _norm_type(r.get("object_type"))
            try:
                conf = float(r.get("confidence", 0.5))
            except Exception:
                conf = 0.5
            conf = min(1.0, max(0.0, conf))
            if subj and rel:
                rels.append({"subject": subj, "relation": rel, "object": obj,
                             "confidence": conf, "subject_type": styp,
                             "object_type": otyp})
    out["relations"] = rels
    return out
