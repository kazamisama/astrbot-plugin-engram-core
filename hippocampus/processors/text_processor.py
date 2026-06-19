"""TextProcessor (v1.4): tokenize / stopword removal / negation marking.

Single facade for all text normalization. Used by:
- vector_search / fts_search (smoke) - text fed into embedder
- dual_route graph route - entity matching
- future Atom extraction

Zero hard dependency on jieba. If jieba is installed, it is used for
Chinese segmentation. Otherwise we fall back to a char-level + ASCII-word
splitter that is still FTS5-friendly and adequate for short chat input.

The class is a singleton (module-level instance) plus a class-level interface
so callers can either:
    from hippocampus.processors import text_processor
    text_processor.tokenize("hello world")
or override the default lists at startup:
    TextProcessor.register_stopwords({"foo", "bar"})
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Iterable

from .stopwords import (
    ZH_STOPWORDS, EN_STOPWORDS, ZH_NEGATIONS, EN_NEGATIONS,
    all_zh, all_en, all_negations, merge,
)


_CJK_RANGES = (
    (0x3040, 0x30FF),   # Hiragana / Katakana
    (0x3400, 0x4DBF),   # CJK Ext A
    (0x4E00, 0x9FFF),   # CJK Unified
    (0x3000, 0x303F),   # CJK punctuation
    (0xFF00, 0xFFEF),   # fullwidth
)


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    # Supports both single-char and multi-char strings: True if ANY char is CJK.
    return any(lo <= ord(c) <= hi for c in ch for lo, hi in _CJK_RANGES)


# Latin word: starts with letter, can contain letters / digits / apostrophe / hyphen
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*")
# Single CJK char (jieba-less fallback)
_CJK_CHAR = re.compile(r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF]")


def _has_jieba() -> bool:
    try:
        import jieba  # noqa: F401
        return True
    except Exception:
        return False


def _char_level_tokenize(text: str) -> list[str]:
    """jieba-less fallback: ASCII words + per-CJK-char tokens, preserving
    the FTS5-friendly shape. Drop pure punctuation and whitespace."""
    if not text:
        return []
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        m = _LATIN_WORD.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if _is_cjk(ch):
            out.append(ch)
            i += 1
            continue
        # punctuation / misc - skip
        i += 1
    return out


def _jieba_tokenize(text: str) -> list[str]:
    import jieba
    # jieba.lcut returns list of tokens; it splits CJK but also keeps ASCII
    # runs together. We post-split ASCII runs to match the fallback shape.
    raw = [t.strip() for t in jieba.lcut(text) if t and t.strip()]
    out: list[str] = []
    for tok in raw:
        if any(_is_cjk(c) for c in tok) and any(c.isalpha() and ord(c) < 128 for c in tok):
            # mixed CJK + ASCII: split ASCII runs out
            for m in _LATIN_WORD.finditer(tok):
                out.append(m.group())
            for c in tok:
                if _is_cjk(c):
                    out.append(c)
        else:
            out.append(tok)
    return out



class TextProcessor:
    """Stateless-ish facade with mutable stopword/negation registries.

    All methods are classmethods so callers don't have to thread an
    instance through. The active stopword/negation sets live on the
    class and can be augmented at runtime.
    """
    # class-level state
    _extra_stopwords: set[str] = set()
    _extra_negations: set[str] = set()

    # ---- registry ------------------------------------------------
    @classmethod
    def register_stopwords(cls, words: Iterable[str]) -> int:
        before = len(cls._extra_stopwords)
        cls._extra_stopwords.update(w.strip() for w in words if w and w.strip())
        return len(cls._extra_stopwords) - before

    @classmethod
    def register_negations(cls, words: Iterable[str]) -> int:
        before = len(cls._extra_negations)
        cls._extra_negations.update(w.strip().lower() for w in words if w and w.strip())
        return len(cls._extra_negations) - before

    @classmethod
    def register_stopwords_from_file(cls, path: str) -> int:
        with open(path, encoding="utf8") as f:
            return cls.register_stopwords(line.strip() for line in f if line.strip())

    @classmethod
    def reset(cls) -> None:
        cls._extra_stopwords.clear()
        cls._extra_negations.clear()

    # ---- queries ------------------------------------------------
    @classmethod
    def stopwords(cls) -> frozenset[str]:
        return merge(all_zh(), all_en(), cls._extra_stopwords)

    @classmethod
    def negations(cls) -> frozenset[str]:
        return merge(all_negations(), cls._extra_negations)

    @classmethod
    def jieba_available(cls) -> bool:
        return _has_jieba()

    # ---- core operations ---------------------------------------
    @classmethod
    def tokenize(cls, text: str) -> list[str]:
        """Tokenize text. Uses jieba if available, else char-level fallback."""
        if not text:
            return []
        if _has_jieba():
            return _jieba_tokenize(text)
        return _char_level_tokenize(text)

    @classmethod
    def remove_stopwords(cls, tokens: Iterable[str]) -> list[str]:
        sw = cls.stopwords()
        # Case-insensitive match: 'I' (uppercase) and 'i' (lowercase) both stop
        sw_lower = {w.lower() for w in sw}
        return [t for t in tokens if t.lower() not in sw_lower]

    @classmethod
    def mark_negation(cls, tokens: Iterable[str]) -> list[tuple[str, bool]]:
        """Return [(token, is_negated)]. The flag is set for tokens that
        follow a negation word within NEGATION_WINDOW. Useful for
        sentiment-aware embedding / scoring.
        """
        negs = cls.negations()
        out: list[tuple[str, bool]] = []
        window = 3  # a negation flips the next 3 content tokens
        cooldown = 0
        for tok in tokens:
            t = tok.strip()
            low = t.lower()
            if low in negs:
                cooldown = window
                # also emit the negation itself as un-negated so it's
                # searchable by FTS ("not good" still matches "not")
                out.append((t, False))
                continue
            out.append((t, cooldown > 0))
            if cooldown > 0:
                cooldown -= 1
        return out

    # ---- high-level pipelines used by recall/fts/embed --------
    @classmethod
    def fts_preprocess(cls, text: str) -> str:
        """Prepare text for SQLite FTS5 (unicode61 tokenizer). Mimics
        the legacy `cjk_split` shape: ASCII words + per-CJK-char, with
        spaces between, so FTS5 indexes each char as a token.
        """
        toks = _char_level_tokenize(text or "")
        return " ".join(toks)

    @classmethod
    def embed_preprocess(cls, text: str) -> str:
        """Prepare text for embedding. Stopwords removed, negation prefix
        kept (so 'NOT_good' embeds differently from 'good').
        """
        toks = cls.tokenize(text or "")
        toks = cls.remove_stopwords(toks)
        marked = cls.mark_negation(toks)
        out: list[str] = []
        for tok, neg in marked:
            if neg:
                out.append("NOT_" + tok)
            else:
                out.append(tok)
        return " ".join(out)

    @classmethod
    def keyword_preprocess(cls, text: str) -> list[str]:
        """Tokens for keyword matching (graph route entity lookup).
        Strips stopwords, keeps everything else.
        """
        return cls.remove_stopwords(cls.tokenize(text or ""))


# module-level singleton for convenience
text_processor = TextProcessor