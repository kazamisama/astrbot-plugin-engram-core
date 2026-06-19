from __future__ import annotations
import json, urllib.request, urllib.error
from .embeddings import EmbeddingProvider
from .llm import LLMProvider, RuleLLMProvider, OpenAILLMProvider, AstrBotLLMProvider

class ProviderRegistry:
    """User-selectable provider pool. Names are stable string IDs."""
    def __init__(self) -> None:
        self._emb: dict[str, EmbeddingProvider] = {}
        self._llm: dict[str, LLMProvider] = {}

    # ---- embedding ----
    def register_embedding(self, name: str, provider: EmbeddingProvider) -> None:
        if not name: raise ValueError("embedding name required")
        if not isinstance(provider, EmbeddingProvider):
            raise TypeError("provider must be EmbeddingProvider")
        self._emb[name] = provider
    def get_embedding(self, name: str) -> EmbeddingProvider:
        if name not in self._emb: raise KeyError(f"unknown embedding: {name}")
        return self._emb[name]
    def has_embedding(self, name: str) -> bool: return name in self._emb
    def list_embeddings(self) -> list[str]: return sorted(self._emb)

    # ---- llm ----
    def register_llm(self, name: str, provider: LLMProvider) -> None:
        if not name: raise ValueError("llm name required")
        if not isinstance(provider, LLMProvider):
            raise TypeError("provider must be LLMProvider")
        self._llm[name] = provider
    def get_llm(self, name: str) -> LLMProvider:
        if name not in self._llm: raise KeyError(f"unknown llm: {name}")
        return self._llm[name]
    def has_llm(self, name: str) -> bool: return name in self._llm
    def list_llms(self) -> list[str]: return sorted(self._llm)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embeddings via urllib. Zero external deps."""
    _DIM_MAP = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    def __init__(self, api_key: str, model: str = "text-embedding-3-small",
                 base_url: str = "https://api.openai.com/v1",
                 timeout: float = 30.0) -> None:
        if not api_key: raise ValueError("OpenAIEmbeddingProvider: api_key required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        if model not in self._DIM_MAP:
            # Unknown model: still usable, dim fallback to 1536
            self._dim = 1536
        else:
            self._dim = self._DIM_MAP[model]
    @property
    def dim(self) -> int: return self._dim
    def name(self) -> str: return f"openai:{self._model}"
    def embed(self, text: str) -> list[float]:
        body = json.dumps({"model": self._model, "input": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"openai embed http {e.code}: {e.read().decode('utf-8', errors='ignore')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"openai embed urlerror: {e}")
        return data["data"][0]["embedding"]



class ProxyEmbeddingProvider(EmbeddingProvider):
    """User injects a callable. Most flexible: works with any embedding source
    (AstrBot internal API, BGE, Cohere, custom local model, ...).
    fn: (str) -> list[float]
    """
    def __init__(self, identity: str, fn) -> None:
        if not identity: raise ValueError("identity required")
        if not callable(fn): raise TypeError("fn must be callable")
        self._id = identity
        self._fn = fn
        # auto-detect dim
        sample = fn("dim-probe")
        if not isinstance(sample, list) or not all(isinstance(x, (int, float)) for x in sample):
            raise TypeError("fn must return list[float]")
        if len(sample) == 0: raise ValueError("fn returned empty vector")
        self._dim = len(sample)
    @property
    def dim(self) -> int: return self._dim
    def name(self) -> str: return self._id
    def embed(self, text: str) -> list[float]:
        return self._fn(text)

def default_registry() -> ProviderRegistry:
    """Returns a registry pre-populated with safe defaults. Does NOT call network."""
    from .embeddings import HashEmbeddingProvider
    r = ProviderRegistry()
    r.register_embedding("hash", HashEmbeddingProvider(dim=64))
    r.register_llm("rule", RuleLLMProvider("rule"))
    return r