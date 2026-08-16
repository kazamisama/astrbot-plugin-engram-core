from __future__ import annotations
import re, json
from .types import Engram
from .embeddings import EmbeddingProvider
from .llm import LLMProvider, RuleLLMProvider
from .config import MemoryConfig
from .valence import ValenceScorer
from .prompts import get_prompt

_TOPIC_KEYWORDS = {
    "preference": ["喜欢", "讨厌", "prefer", "like", "hate", "favorite", "love", "dislike"],
    "plan": ["计划", "打算", "明天", "下周", "plan", "tomorrow", "next", "下周"],
    "identity": ["我叫", "我是", "my name", "i am", "i'm"],
    "emotion": ["开心", "难过", "生气", "happy", "sad", "angry"],
    "tech": ["代码", "bug", "code", "python", "ai", "模型"],
}

_ENTITY_PATTERNS = [
    (r"@([\w\u4e00-\u9fff]{2,20})", 0),
    (r"(?:i am|i'\''m|my name is)\s+([A-Z][\w''-]+)", 0),
    (r"我(?:叫|是)\s*([\u4e00-\u9fff]{2,12})", 0),
    (r"(?:i )?live in\s+([A-Z][\w''-]+)", 0),
    (r"住在\s*([\u4e00-\u9fff]{2,12})", 0),
    (r"(?:i (?:love|like|hate|dislike))\s+([A-Za-z][\w''-]+)", 0),
    (r"我(?:喜欢|讨厌)\s*([\u4e00-\u9fff]{2,12})", 0),
    (r"\b([A-Z][a-z]{2,})\b", 0),
    (r"(上海|北京|广州|深圳|杭州|成都|武汉|西安|南京|天津|苏州|重庆|香港|澳门|台北|美式|拿铁|卡布奇诺|香菜|咖啡|茶|可乐|酒|烟)\b", 0),
]

# FIX (v1.56): rewrite per-message extract prompt in Chinese (was all
# English even though every other prompt in the codebase is Chinese),
# add channel context + persona prefill, and bump max_tokens from 300
# to 600 so longer messages don't get truncated mid-JSON.
_LLM_SYSTEM = (
    "你是一台聊天机器人的记忆提取器。"
    "从单条用户消息里抽取结构化记忆字段。"
    "严格输出 JSON，键："
    "summary (一句话中文摘要), "
    "topics (话题列表, 选自 preference/plan/identity/emotion/tech/misc), "
    "entities (实体列表, 抽出人名/地名/物品/概念), "
    "importance (重要度 0-1, 含强烈偏好/计划/身份/负面情绪的取 >= 0.7)。"
)


def _build_extract_prompt(text: str, channel_ctx: str = "") -> str:
    """FIX (v1.56): prepend optional channel context (群聊名 / 私聊对方)
    so the LLM can resolve ambiguous references like "我" / "那个群" /
    "上次说的人" in the user message."""
    head = (channel_ctx + "\n" if channel_ctx else "")
    return head + "用户消息：\n" + text


class EngramEncoder:
    """Rule-based by default. If llm is set and chat() returns valid JSON, LLM path wins."""
    def __init__(self, embedder: EmbeddingProvider, llm: LLMProvider | None = None,
                 cfg: MemoryConfig | None = None,
                 persona_provider=None) -> None:
        self._embed = embedder
        self._llm = llm or RuleLLMProvider()
        self._cfg = cfg
        self._valence = ValenceScorer()
        # FIX (v1.56): persona_provider(actor_id, channel_id) -> system_prompt
        # (or None). When set, the per-message LLM extract gets the same
        # persona prefill that summary / diary already use.
        self._persona = persona_provider

    def set_embedder(self, embedder: EmbeddingProvider) -> None: self._embed = embedder
    def set_llm(self, llm: LLMProvider) -> None: self._llm = llm

    def encode(self, *, session_id: str, actor_id: str, platform: str,
               channel_id: str, content: str, persona_id: str = "",
               scope_id: str = "",
               channel_label: str = "", chat_type: str = "") -> Engram:
        text = content.strip()
        # FIX (v1.56): build channel context so the LLM extractor can
        # resolve "我" / "那个群" / "上次" type references.
        ctx = ""
        if chat_type == "group" and (channel_label or channel_id):
            label = channel_label or channel_id
            ctx = "[群聊 " + label + " (" + channel_id + ")]"
        elif chat_type == "private":
            ctx = "[私聊 对方 " + (channel_label or actor_id) + "]"
        # 尝试 LLM 抽取;失败回退规则
        llm_out = self._try_llm_extract(text, channel_ctx=ctx, actor_id=actor_id)
        if llm_out is not None:
            summary = llm_out.get("summary") or self._summarize(text)
            topics = llm_out.get("topics") or self._topics(text)
            entities = llm_out.get("entities") or self._entities(text)
            importance = float(llm_out.get("importance") or self._importance(text, topics))
        else:
            summary = self._summarize(text)
            topics = self._topics(text)
            entities = self._entities(text)
            importance = self._importance(text, topics)
        emb = self._embed.embed(text)
        # v1.0: valence + intensity + stream + temporal bucket
        v, inten = self._valence.score(text)
        stream = self._valence.detect_stream(text)
        import time
        tb_scale = getattr(self._cfg, "temporal_bucket_seconds", 3600)
        tbucket = self._valence.temporal_bucket(time.time(), tb_scale)
        # v1.0: negativity bias — negative memories get a small importance bump
        if v < -0.2:
            importance = min(1.0, importance + 0.08 * abs(v))
        # v1.0: emotional intensity also bumps importance
        if inten > 0.3:
            importance = min(1.0, importance + 0.05 * inten)
        return Engram(
            session_id=session_id, actor_id=actor_id, platform=platform,
            channel_id=channel_id, persona_id=persona_id, scope_id=scope_id,
            content=text, summary=summary,
            topics=topics, entities=entities, importance=importance,
            embedding=emb, strength=max(importance, 0.4),
            valence=v, intensity=inten, stream=stream, temporal_bucket=tbucket,
        )

    def _try_llm_extract(self, text: str, channel_ctx: str = "",
                        actor_id: str = "") -> dict | None:
        if isinstance(self._llm, RuleLLMProvider): return None
        # FIX (v1.56): Chinese system prompt + persona prefill + channel
        # context + raised max_tokens (300 -> 600) to leave headroom for
        # longer messages.
        try:
            _pn = getattr(self._cfg, "_prompt_namespace", None)
            sys = get_prompt("encoder_extract_system", _LLM_SYSTEM, namespace=_pn)
            if self._persona is not None and actor_id:
                try:
                    p = self._persona(actor_id, channel_ctx)
                    if p:
                        sys = sys + "\n\n" + p
                except Exception:
                    pass
            user = get_prompt("encoder_extract_user_head", namespace=_pn).format(
                channel_ctx=(channel_ctx + chr(10)) if channel_ctx else "", text=text)
            raw = self._llm.chat(sys, user, temperature=0.1, max_tokens=600)
        except Exception:
            return None
        if not raw: return None
        # 尝试直接 json 解析;也兼容 ```json ... ``` 包裹
        s = raw.strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
            s = re.sub(r"\n?```$", "", s)
        try:
            obj = json.loads(s)
        except Exception:
            return None
        if not isinstance(obj, dict): return None
        return obj

    @staticmethod
    def _summarize(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= 80: return text
        cut = text[:80]
        for sep in ["。", ".", "!", "?", "!", "?"]:
            i = text.find(sep, 0, 80)
            if i > 0: cut = text[:i+1]; break
        return cut + ("..." if len(text) > len(cut) else "")

    @staticmethod
    def _topics(text: str) -> list[str]:
        t = text.lower()
        hit = [k for k, kws in _TOPIC_KEYWORDS.items() if any(kw in t for kw in kws)]
        return hit or ["misc"]

    @staticmethod
    def _entities(text: str) -> list[str]:
        out: list[str] = []
        for pat, _ in _ENTITY_PATTERNS:
            for m in re.finditer(pat, text):
                v = m.group(1).strip()
                if v and v not in out:
                    out.append(v)
        return out[:12]

    @staticmethod
    def _importance(text: str, topics: list[str]) -> float:
        s = 0.4
        if any(t in topics for t in ("identity", "preference", "plan")): s += 0.3
        if len(text) > 60: s += 0.1
        if re.search(r"[!?\uff01\uff1f]", text): s += 0.1
        return min(1.0, s)