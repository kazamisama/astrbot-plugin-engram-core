from __future__ import annotations
import json, urllib.request, urllib.error
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    def chat(self, system: str, user: str, *,
             temperature: float = 0.2, max_tokens: int = 512) -> str: ...

class RuleLLMProvider(LLMProvider):
    """Pure-rules fallback. Returns "" so callers fall back to their rule path."""
    def __init__(self, identity: str = "rule") -> None:
        self._identity = identity
    def name(self) -> str: return self._identity
    def chat(self, system: str, user: str, **_) -> str: return ""

class OpenAILLMProvider(LLMProvider):
    """OpenAI Chat Completions via urllib. Zero external deps."""
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1",
                 timeout: float = 30.0) -> None:
        if not api_key: raise ValueError("OpenAILLMProvider: api_key required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
    def name(self) -> str: return f"openai:{self._model}"
    def chat(self, system: str, user: str, *,
             temperature: float = 0.2, max_tokens: int = 512) -> str:
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"openai chat http {e.code}: {e.read().decode('utf-8', errors='ignore')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"openai chat urlerror: {e}")
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            return ""


class ProxyLLMProvider(LLMProvider):
    """User injects a callable. Most flexible: bridge AstrBot current LLM or
    any custom model. fn signature: fn(system, user, **kw) -> str.

    fn may be sync or async; coroutine results are driven to completion on a
    background loop so chat() stays a plain sync call even when invoked from
    an async event handler."""
    def __init__(self, identity: str, fn) -> None:
        if not identity: raise ValueError("identity required")
        if not callable(fn): raise TypeError("fn must be callable")
        self._id = identity
        self._fn = fn
    def name(self) -> str: return self._id
    def chat(self, system: str, user: str, **kw) -> str:
        import inspect
        out = self._fn(system=system, user=user, **kw)
        if inspect.isawaitable(out):
            from ._async_bridge import run_sync
            out = run_sync(out)
        return out

class AstrBotLLMProvider(LLMProvider):
    """Bridge to AstrBot current LLM provider. The actual bridge is set per-process
    by the plugin (it knows the AstrBot Context object)."""
    def __init__(self, bridge=None) -> None:
        self._bridge = bridge
    def set_bridge(self, bridge) -> None: self._bridge = bridge
    def name(self) -> str: return "astrmock"
    def chat(self, system: str, user: str, **kw) -> str:
        if self._bridge is None: return ""
        try:
            import inspect
            out = self._bridge(system=system, user=user, **kw)
            if inspect.isawaitable(out):
                from ._async_bridge import run_sync
                out = run_sync(out)
            return out or ""
        except Exception:
            return ""