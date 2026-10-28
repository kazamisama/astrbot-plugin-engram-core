from __future__ import annotations
import asyncio

# v1.76.14 (2026-09-07 recurrence): AstrBot's embedding provider may have
# no HTTP timeout of its own, and its coroutine may be bound to the
# AstrBot event loop while we await it here on the bridge worker loop.
# Either way the await can hang indefinitely; run_sync's caller-side cap
# then releases the caller, but the coroutine stays stuck ON THE WORKER
# LOOP (fut.cancel() only lands at an await point and, cross-loop, may
# never even be delivered). Bounding the await INSIDE the bridge is what
# actually keeps the worker loop healthy.
#
# v1.76.16: this MUST stay comfortably below InjectHandler._INJECT_HARD_TIMEOUT
# (10.0s), otherwise the whole-injection cap fires first and the hook drops
# the injection entirely (logged 59x as "auto inject timed out ... proceeding
# WITHOUT injection") instead of taking the designed degradation path of
# continuing with the FTS/keyword route only. At 10.0 the two were equal, so
# the inner cap could never win.
EMB_BRIDGE_TIMEOUT: float = 5.0


async def emb_bridge_for_context(context, text: str,
                                 provider_id: str = "") -> list[float]:
    """Embed `text` via an AstrBot embedding provider; return [] if none works.

    Resolution order (matches the real AstrBot Context API,
    core/star/context.py):
      1) provider_id given -> context.get_provider_by_id(provider_id)
      2) first provider from context.get_all_embedding_providers()
      3) legacy fallbacks (older AstrBot builds): probe a couple of
         method names on get_using_provider().

    The official EmbeddingProvider exposes `async get_embedding(text)`;
    we still probe a few aliases so this keeps working across versions.
    The whole resolution is hard-bounded by EMB_BRIDGE_TIMEOUT: one shared
    budget, not one cap per attempt (see the v1.77.0 note below).
    """
    method_names = ("get_embedding", "embedding", "embed", "encode")
    # v1.77.0: EMB_BRIDGE_TIMEOUT is a budget for the WHOLE resolution. The
    # old per-attempt cap could spend it twice on the same hung provider --
    # the configured id is reachable through two resolution steps, so a
    # wedged provider cost 5s + 5s == _INJECT_HARD_TIMEOUT and the designed
    # "degrade to the FTS/keyword route" path was unreachable: the whole
    # injection was dropped instead. Observed live on 2026-10-28 14:25
    # (one 10.0s inject drop followed by the second 5.0s timeout line).
    # Providers are now deduplicated by identity; every await is bounded by
    # the time LEFT, and an exhausted budget aborts the rest of the chain.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(EMB_BRIDGE_TIMEOUT)
    seen: set = set()

    def _left() -> float:
        return deadline - loop.time()

    async def _try_call(obj, name: str):
        if obj is None or not hasattr(obj, name):
            return None
        left = _left()
        if left <= 0.0:
            return None
        fn = getattr(obj, name)
        try:
            out = fn(text)
            if asyncio.iscoroutine(out):
                out = await asyncio.wait_for(out, timeout=left)
        except asyncio.TimeoutError:
            print("[hippocampus] emb bridge " + name + " timed out after "
                  + str(EMB_BRIDGE_TIMEOUT) + "s; skipping provider")
            return None
        except Exception as e:
            print("[hippocampus] emb bridge " + name + " raised: " + repr(e))
            return None
        if isinstance(out, list) and out and all(
                isinstance(x, (int, float)) for x in out):
            return [float(x) for x in out]
        return None

    async def _embed_with(prov):
        if asyncio.iscoroutine(prov):
            prov = await prov
        if prov is None:
            return None
        key = id(prov)
        if key in seen:          # same provider via two resolution steps
            return None
        seen.add(key)
        for name in method_names:
            if _left() <= 0.0:
                return None
            out = await _try_call(prov, name)
            if out is not None:
                return out
        return None

    # 1) explicit provider id (configured by the user)
    if provider_id:
        try:
            getter = getattr(context, "get_provider_by_id", None)
            if getter is not None:
                prov = getter(provider_id)
                out = await _embed_with(prov)
                if out is not None:
                    return out
        except Exception as e:
            print("[hippocampus] emb bridge get_provider_by_id("
                  + provider_id + ") failed: " + repr(e))

    # 2) AstrBot's configured embedding providers (first usable one)
    try:
        getter = getattr(context, "get_all_embedding_providers", None)
        if getter is not None:
            provs = getter()
            if asyncio.iscoroutine(provs):
                provs = await provs
            for prov in (provs or []):
                out = await _embed_with(prov)
                if out is not None:
                    return out
    except Exception as e:
        print("[hippocampus] emb bridge get_all_embedding_providers failed: "
              + repr(e))

    # 2b) legacy: a dedicated embedding-provider getter (older AstrBot
    # builds and the smoke mocks expose get_using_embedding_provider).
    try:
        getter = getattr(context, "get_using_embedding_provider", None)
        if getter is not None:
            out = await _embed_with(getter())
            if out is not None:
                return out
    except Exception as e:
        print("[hippocampus] emb bridge get_using_embedding_provider failed: "
              + repr(e))
    # 3) legacy fallback: probe the chat provider (older AstrBot builds)
    try:
        getter = getattr(context, "get_using_provider", None)
        if getter is not None:
            out = await _embed_with(getter())
            if out is not None:
                return out
    except Exception as e:
        print("[hippocampus] emb bridge llm-fallback failed: " + repr(e))

    print("[hippocampus] emb bridge: no AstrBot embedding provider found; "
          "astrmock embedding returns empty (configure an embedding "
          "provider in AstrBot, or use the openai / hash provider)")
    return []