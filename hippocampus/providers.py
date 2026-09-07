from __future__ import annotations
import json, urllib.request, urllib.error
from .embeddings import EmbeddingProvider
from .llm import LLMProvider, RuleLLMProvider, OpenAILLMProvider, AstrBotLLMProvider
from ._async_bridge import run_sync, DEFAULT_SYNC_TIMEOUT

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

    fn may be sync (str -> list[float]) or async (str -> Awaitable[list[float]]);
    coroutine results are driven to completion on a background loop so embed()
    stays a plain sync call even when invoked from an async event handler.
    """
    def __init__(self, identity: str, fn, dim: int = 0) -> None:
        if not identity: raise ValueError("identity required")
        if not callable(fn): raise TypeError("fn must be callable")
        self._id = identity
        self._fn = fn
        # Lazy dim detection: the backing host provider may not be ready at
        # plugin-init time (a known startup race: the engram bridge installs
        # before AstrBot finishes loading its embedding provider). Probing
        # here and failing would permanently drop this provider. Instead we
        # try once, but tolerate an empty/failed probe and resolve dim on the
        # first successful embed().
        self._dim = int(dim) if dim and dim > 0 else 0
        if self._dim == 0:
            try:
                sample = self._call("dim-probe")
                if (isinstance(sample, list) and sample
                        and all(isinstance(x, (int, float)) for x in sample)):
                    self._dim = len(sample)
            except Exception:
                pass
    @property
    def dim(self) -> int: return self._dim
    def name(self) -> str: return self._id
    def _call(self, text: str) -> list[float]:
        import inspect
        out = self._fn(text)
        if inspect.isawaitable(out):
            # v1.76.13: explicit hard timeout -- a hung embedding
            # provider (no HTTP timeout of its own) must raise here
            # instead of blocking the astrbot hook chain forever.
            out = run_sync(out, timeout=DEFAULT_SYNC_TIMEOUT)
        return out
    def embed(self, text: str) -> list[float]:
        out = self._call(text)
        # resolve dim lazily once the host provider becomes available
        if (not self._dim and isinstance(out, list) and out
                and all(isinstance(x, (int, float)) for x in out)):
            self._dim = len(out)
        return out

def default_registry() -> ProviderRegistry:
    """Returns a registry pre-populated with safe defaults. Does NOT call network."""
    from .embeddings import HashEmbeddingProvider
    r = ProviderRegistry()
    r.register_embedding("hash", HashEmbeddingProvider(dim=64))
    r.register_llm("rule", RuleLLMProvider("rule"))
    return r