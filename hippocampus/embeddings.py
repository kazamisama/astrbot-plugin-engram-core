from __future__ import annotations
import hashlib, math
from abc import ABC, abstractmethod

class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...
    @property
    @abstractmethod
    def dim(self) -> int: ...

class HashEmbeddingProvider(EmbeddingProvider):
    """Zero-dependency placeholder. Swap with real model in production."""
    def __init__(self, dim: int = 64, seed: bytes = b"hippocampus-v1") -> None:
        self._dim = dim
        self._seed = seed

    @property
    def dim(self) -> int: return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = list(_tokenize(text))
        if not tokens: return vec
        for token in tokens:
            h = hashlib.blake2b(self._seed + token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self._dim
            sign = 1.0 if (h[4] & 1) else -1.0
            vec[idx] += sign
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]

def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf.clear()
    if buf: out.append("".join(buf))
    return [t for t in out if t]