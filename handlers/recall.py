from __future__ import annotations
import asyncio
async def emb_bridge_for_context(context, text: str) -> list[float]:
    """Try several common AstrBot embedding APIs; return [] if none works.

    Probed paths (in order):
      1) context.get_using_embedding_provider()  + .get_embedding/.embedding/.embed/.encode
      2) context.get_using_provider()           + same method names
    Both sync and coroutine methods are accepted.
    """
    method_names = ("get_embedding", "embedding", "embed", "encode")

    async def _try_call(obj, name: str):
        if obj is None or not hasattr(obj, name):
            return None
        fn = getattr(obj, name)
        try:
            out = fn(text)
            if asyncio.iscoroutine(out):
                out = await out
        except Exception as e:
            print("[hippocampus] emb bridge " + name + " raised: " + repr(e))
            return None
        if isinstance(out, list) and out and all(isinstance(x, (int, float)) for x in out):
            return [float(x) for x in out]
        return None

    # 1) 专用的 embedding provider
    try:
        getter = getattr(context, "get_using_embedding_provider", None)
        if getter is not None:
            prov = getter()
            if asyncio.iscoroutine(prov):
                prov = await prov
            for name in method_names:
                out = await _try_call(prov, name)
                if out is not None:
                    return out
    except Exception as e:
        print("[hippocampus] emb bridge get_using_embedding_provider failed: " + repr(e))

    # 2) 退化:用 LLM provider 试
    try:
        getter = getattr(context, "get_using_provider", None)
        if getter is not None:
            prov = getter()
            if asyncio.iscoroutine(prov):
                prov = await prov
            for name in method_names:
                out = await _try_call(prov, name)
                if out is not None:
                    return out
    except Exception as e:
        print("[hippocampus] emb bridge llm-fallback failed: " + repr(e))

    # 3) 都不可用
    print("[hippocampus] emb bridge: no AstrBot embedding API found, "
          "astrmock embedding will return empty (use openai / hash instead)")
    return []


