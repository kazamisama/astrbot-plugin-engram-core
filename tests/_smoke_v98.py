"""Smoke v1.77.1: embedding-space fingerprint, empty-vector repair,
and bounded inject-lock wait.

Why this test exists
--------------------
Three live defects motivated this batch:
  1. 60 engrams on the live store carried an empty embedding with the
     correct 'astrmock' label. reembed_stale() could repair them, but its
     trigger only fired when rows carried a *different* label, which never
     happened -- so they were invisible to vector recall forever.
  2. Nothing recorded which embedding space the store was built in. A
     provider swap (or a provider quietly serving another model) left old
     vectors looking current, and _cos truncated the dimension mismatch.
  3. Two concurrent LLM requests serialized on the inject lock; the waiter
     burned its whole 10s budget and lost memory injection instead of
     skipping it.

Tests:
- first activation stores a fingerprint (model/dim/probe) in hippo_meta
- an empty-vector row triggers the stale re-embed repair
- a dimension or cosine mismatch triggers the full background re-embed
- the inject lock is only waited on for _LOCK_WAIT seconds; a busy lock
  skips the injection instead of blocking the request
"""
import asyncio
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from _smoke_common import install_stub

install_stub()

from hippocampus import MemoryConfig, MemoryService
from hippocampus.embeddings import EmbeddingProvider
from hippocampus.types import Engram
from handlers.event.inject import InjectHandler
from handlers.init import PluginInitializer


class _Embedder(EmbeddingProvider):
    def __init__(self, dim=64):
        self._dim = dim

    @property
    def dim(self):
        return self._dim

    def embed(self, text):
        v = [0.0] * self._dim
        v[0] = 1.0
        return v


def banner(t):
    print(chr(10) + "=== " + t + " ===")


def _svc(tmp):
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "h.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.enable_atom_extraction = False
    cfg.enable_graph_indexing = False
    cfg.dedup_enabled = False
    return MemoryService(cfg, embedder=_Embedder())


def _init(svc):
    init = PluginInitializer.__new__(PluginInitializer)
    init.service = svc
    init._full_reembed_thread = None
    calls = []
    init._start_stale_reembed = lambda n: calls.append(("stale", n))
    init._start_full_reembed = lambda probe: calls.append(("full", len(probe)))
    return init, calls


def test_fingerprint_saved_on_first_activation():
    banner("first activation stores the embedding fingerprint")
    svc = _svc(tempfile.mkdtemp())
    init, calls = _init(svc)
    init._check_embedding_space([1.0, 0.0, 0.0], legacy=0)
    raw = svc.store.get_meta(PluginInitializer._FP_KEY)
    fp = json.loads(raw)
    assert fp["dim"] == 3, fp
    assert fp["model"] == svc.current_embedding(), fp
    assert len(fp["probe"]) == 3, fp
    assert calls == [], calls
    svc.close()
    print("fingerprint: OK")


def test_empty_vector_triggers_repair():
    banner("an empty vector triggers the stale re-embed repair")
    svc = _svc(tempfile.mkdtemp())
    init, calls = _init(svc)
    svc.store.upsert(Engram(id="empty", content="no vector",
                            embedding_model=svc.current_embedding()))
    assert svc.store.count_missing_vectors() == 1
    init._check_embedding_space([1.0, 0.0, 0.0], legacy=0)
    assert calls == [("stale", 1)], calls
    svc.close()
    print("empty-vector repair: OK")


def test_space_mismatch_triggers_full_reembed():
    banner("dimension / cosine mismatch triggers the full re-embed")
    svc = _svc(tempfile.mkdtemp())
    init, calls = _init(svc)
    init._check_embedding_space([1.0, 0.0, 0.0], legacy=0)
    assert calls == [], calls
    # provider now answers with a different dimension
    init._check_embedding_space([1.0, 0.0, 0.0, 0.0], legacy=0)
    assert calls == [("full", 4)], calls
    # same dimension, unrelated direction: a different model, same size
    calls.clear()
    init._check_embedding_space([0.0, 1.0, 0.0], legacy=0)
    assert calls == [("full", 3)], calls
    svc.close()
    print("space mismatch: OK")


def test_inject_lock_wait_is_bounded():
    banner("inject lock: bounded wait, then skip")
    h = InjectHandler(None)
    hit = []

    def _fake_sync(event, req, stop):
        hit.append(1)

    h._handle_inject_sync = _fake_sync

    async def _scenario():
        await h._run_inject("event", "req")
        assert hit == [1], hit
        # Hold the lock from another task, then try to inject.
        lock = h._get_inject_lock()
        await lock.acquire()
        try:
            t0 = time.monotonic()
            await h._run_inject("event", "req")
            elapsed = time.monotonic() - t0
        finally:
            lock.release()
        return elapsed

    elapsed = asyncio.run(_scenario())
    assert hit == [1], hit
    assert InjectHandler._LOCK_WAIT <= elapsed < InjectHandler._LOCK_WAIT + 1.0, elapsed
    print("inject lock: OK")


if __name__ == "__main__":
    test_fingerprint_saved_on_first_activation()
    test_empty_vector_triggers_repair()
    test_space_mismatch_triggers_full_reembed()
    test_inject_lock_wait_is_bounded()
    print(chr(10) + "ALL OK")
