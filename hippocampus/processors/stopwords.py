"""Built-in stopword / negation lists for hippocampus TextProcessor.

These are intentionally small and self-contained so the plugin has zero hard
dependency on jieba or nltk. They cover the most common cases for chat-style
input. To extend, use TextProcessor.register_stopwords() / register_negations()
at runtime.

For production / domain-specific corpora, load a real stopword file via:
    TextProcessor.register_stopwords_from_file("path/to/zh_stopwords.txt")
"""
from __future__ import annotations
from typing import Iterable

# ------------------------------------------------------------------
# Chinese stopwords (function words, particles, common verbs).
# Curated subset, not exhaustive. 100ish entries is enough to cut noise
# in chat input without removing meaningful content words.
# ------------------------------------------------------------------
ZH_STOPWORDS: frozenset[str] = frozenset({
    # pronouns + demonstratives
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
    "这", "那", "这个", "那个", "这些", "那些", "此", "其", "某",
    "自己", "本人", "咱们", "大家",
    # copula / common verbs (very low info)
    "是", "有", "没", "没", "的", "了", "着", "过", "在", "和", "与", "及",
    "或", "而", "但", "也", "都", "还", "就", "才", "又", "再", "已", "将",
    "会", "能", "可以", "可", "要", "想", "得", "应该", "必须",
    "做", "做", "去", "来", "到", "给", "让", "使", "叫", "把", "被", "对",
    "从", "向", "往", "由", "为", "因为", "所以", "因此", "虽然", "但是",
    # function / measure words
    "个", "些", "点", "次", "回", "种", "样", "下", "上", "里", "外", "中",
    "前", "后", "左", "右", "间", "旁边",
    # discourse particles
    "啊", "呢", "吧", "吗", "嘛", "哦", "噢", "嗯", "呀", "哎", "哈",
    "啦", "咯", "哇", "嘿", "哼", "呸", "呵",
    # adverbs that carry little retrieval signal
    "很", "非常", "特别", "真", "挺", "蛮", "十分", "格外", "更", "最", "极",
    "太", "比较", "稍", "略", "几乎", "差不多", "大约",
    # common conjunctions
    "如果", "假如", "虽说", "即使", "不管", "只要", "除非", "既然",
    "然后", "接着", "于是", "之后", "之后", "以前", "以后", "现在", "今天", "昨天", "明天",
})

# ------------------------------------------------------------------
# English stopwords (compact subset).
# ------------------------------------------------------------------
EN_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "for",
    "of", "to", "in", "on", "at", "by", "with", "as", "from", "into",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did", "doing",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those",
    "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
    # not/no/nor/etc. are NEGATIONS, kept in EN_NEGATIONS
    "so", "too", "very", "can", "will", "just",
    "should", "would", "could", "may", "might", "must", "shall",
    "about", "above", "below", "under", "over", "between",
    "than", "such", "also", "only", "even", "any", "all", "some",
})

# ------------------------------------------------------------------
# Negation words. These flip the sentiment of the *next* content word
# in the token stream. Apply via mark_negation() in text_processor.
# ------------------------------------------------------------------
ZH_NEGATIONS: frozenset[str] = frozenset({
    "不", "没", "没有", "无", "非", "未", "别", "莫", "勿", "甭",
    "无法", "不能", "不会", "不要", "不想", "不可", "不得", "不该",
    "从不", "永不", "绝不", "毫不", "毫无",
})
EN_NEGATIONS: frozenset[str] = frozenset({
    "not", "no", "nor", "never", "neither", "none", "nothing",
    "cannot", "cant", "wont", "dont", "doesnt", "didnt",
    "isnt", "arent", "wasnt", "werent", "shouldnt", "wouldnt",
    "couldnt", "mustnt", "havent", "hasnt", "hadnt",
})


def all_zh() -> frozenset[str]:
    return ZH_STOPWORDS


def all_en() -> frozenset[str]:
    return EN_STOPWORDS


def all_negations() -> frozenset[str]:
    return ZH_NEGATIONS | EN_NEGATIONS


def merge(*sources: Iterable[str]) -> frozenset[str]:
    """Helper to combine extra stopword sources into one frozenset."""
    out: set[str] = set()
    for s in sources:
        out.update(s)
    return frozenset(out)