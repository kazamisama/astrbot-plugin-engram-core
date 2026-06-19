"""Rule-based emotional valence scorer. No LLM, no extra deps."""
from __future__ import annotations
import re

# (keyword, valence_delta, intensity_delta)
# valence_delta in [-1, 1], negative = unpleasant, positive = pleasant
# intensity_delta in [0, 1], how arousing the word is
_LEXICON: list[tuple[str, float, float]] = [
    # strong positive
    ("love", 0.6, 0.7), ("loves", 0.6, 0.7), ("loved", 0.5, 0.6),
    ("amazing", 0.7, 0.8), ("awesome", 0.7, 0.8), ("fantastic", 0.7, 0.8),
    ("wonderful", 0.7, 0.7), ("excellent", 0.6, 0.6), ("perfect", 0.6, 0.5),
    ("happy", 0.6, 0.6), ("glad", 0.5, 0.4), ("delighted", 0.7, 0.7),
    ("thrilled", 0.8, 0.9), ("grateful", 0.5, 0.4), ("proud", 0.5, 0.5),
    ("excited", 0.6, 0.8), ("joy", 0.7, 0.7), ("enjoy", 0.4, 0.4),
    ("enjoys", 0.4, 0.4), ("favorite", 0.4, 0.3), ("best", 0.5, 0.5),
    ("喜欢", 0.5, 0.5), ("爱", 0.7, 0.8), ("爱了", 0.7, 0.8),
    ("开心", 0.6, 0.6), ("高兴", 0.5, 0.5), ("棒", 0.5, 0.5),
    ("太好了", 0.7, 0.7), ("完美", 0.6, 0.5), ("幸福", 0.7, 0.6),

    # mild positive
    ("good", 0.3, 0.2), ("nice", 0.3, 0.2), ("ok", 0.1, 0.1), ("okay", 0.1, 0.1),
    ("fine", 0.1, 0.1), ("thanks", 0.2, 0.2), ("thank", 0.2, 0.2), ("cool", 0.3, 0.3),
    ("不错", 0.3, 0.2), ("挺好", 0.3, 0.2), ("可以", 0.1, 0.1),

    # strong negative
    ("hate", -0.7, 0.8), ("hates", -0.7, 0.8), ("hated", -0.6, 0.7),
    ("disgusting", -0.8, 0.9), ("awful", -0.7, 0.8), ("terrible", -0.7, 0.8),
    ("horrible", -0.8, 0.9), ("worst", -0.7, 0.7), ("angry", -0.6, 0.8),
    ("furious", -0.8, 0.9), ("sad", -0.5, 0.5), ("depressed", -0.7, 0.6),
    ("devastated", -0.8, 0.9), ("heartbroken", -0.8, 0.8),
    ("恐惧", -0.7, 0.8), ("害怕", -0.6, 0.7), ("讨厌", -0.6, 0.6),
    ("恶心", -0.7, 0.7), ("生气", -0.6, 0.8), ("难过", -0.5, 0.5),
    ("悲伤", -0.6, 0.6), ("糟透了", -0.8, 0.9), ("绝望", -0.8, 0.8),

    # mild negative
    ("bad", -0.4, 0.3), ("sad", -0.4, 0.4), ("tired", -0.3, 0.3),
    ("annoyed", -0.4, 0.5), ("disappointed", -0.5, 0.5), ("frustrated", -0.5, 0.6),
    ("不好", -0.4, 0.3), ("累", -0.3, 0.3), ("失望", -0.5, 0.5),

    # intensifiers (multiply next match)
    ("very", 0.0, 0.3), ("really", 0.0, 0.3), ("extremely", 0.0, 0.5), ("so", 0.0, 0.2),
    ("超级", 0.0, 0.4), ("非常", 0.0, 0.3), ("特别", 0.0, 0.3), ("太", 0.0, 0.3),

    # negators (flip sign of next match)
    ("not", 0.0, 0.0), ("no", 0.0, 0.0), ("never", 0.0, 0.0), ("not really", 0.0, 0.0),
    ("不", 0.0, 0.0), ("没", 0.0, 0.0), ("别", 0.0, 0.0), ("绝不", 0.0, 0.0),
]

# Stream detection keywords
# where_when: spatial / temporal / sequential / plan
_WHERE_WHEN_HINTS = [
    "tomorrow", "yesterday", "today", "tonight",
    "next week", "last week", "next month", "last month",
    "at", "on", "in", "when", "where", "during",
    "will", "plan", "going to", "scheduled",
    "明天", "昨天", "今天", "今晚",
    "下周", "上周", "下个月", "上个月",
    "在", "计划", "打算", "准备",
]
# what: identity, preference, fact, definition
_WHAT_HINTS = [
    "i am", "i'm", "my name", "i like", "i love", "i hate", "i dislike",
    "is a", "means", "is the",
    "我叫", "我是", "我喜欢", "我爱", "我讨厌", "我住在",
]


class ValenceScorer:
    """Compute (valence, intensity) for a piece of text."""
    def __init__(self) -> None:
        # build lowercase lookup
        self._lex = {w.lower(): (v, i) for w, v, i in _LEXICON}

    def score(self, text: str) -> tuple[float, float]:
        """Return (valence, intensity) both in [-1, 1] / [0, 1]."""
        if not text:
            return 0.0, 0.0
        t = text.lower()
        v_total = 0.0
        i_total = 0.0
        n_hits = 0
        next_flip = False
        next_boost = 0.0
        tokens = re.findall(r'[\w' + chr(39) + r'!?]+', t)
        for tok in tokens:
            entry = self._lex.get(tok)
            if entry is None:
                continue
            v, i = entry
            if v == 0.0 and i == 0.0:
                # negator
                next_flip = True
                continue
            if v == 0.0 and i > 0.0:
                # intensifier
                next_boost = max(next_boost, i)
                continue
            if v != 0.0:
                if next_flip:
                    v = -v
                    next_flip = False
                v_total += v
                i_total += i + next_boost
                n_hits += 1
                next_boost = 0.0
        if n_hits == 0:
            return 0.0, 0.0
        # average valence, sum intensity, clamp
        v_avg = max(-1.0, min(1.0, v_total / max(1, n_hits)))
        i_sum = max(0.0, min(1.0, i_total * 0.5))
        return v_avg, i_sum

    def detect_stream(self, text: str) -> str:
        """Return "where_when" / "what" / ""."""
        if not text:
            return ""
        t = text.lower()
        ww = sum(1 for h in _WHERE_WHEN_HINTS if h in t)
        wh = sum(1 for h in _WHAT_HINTS if h in t)
        if ww > wh and ww > 0:
            return "where_when"
        if wh > ww and wh > 0:
            return "what"
        return ""

    def temporal_bucket(self, ts: float, scale_seconds: int = 3600) -> int:
        """Discrete time-cell bucket id. Default = 1-hour buckets."""
        return int(ts // max(1, scale_seconds))
