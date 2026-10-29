"""Smoke v1.79.3: truthful embedding labels and dimension repair.

Why this test exists
--------------------
The live store recorded embedding_model="astrmock" for every vector while
the host Qwen embedding provider (4096-dim) produced them: the label was the
bridge registry key, not the model name. Fingerprints detect a space change,
but a label that never changes also hides the space in every diagnostic and
makes a future provider switch look like a no-op.

This test pins the split between the registry key and the data label:
  1. current_embedding() is the registry key; embedding_label() is the
     model recorded with rows; set_embedding(name, label=...) sets both.
  2. retag_embeddings(label) rewrites labels in place -- no embed() calls,
     no recall regression (vector_search still filters by label).
  3. _check_embedding_space() relabels instead of re-embedding when the
     fingerprint probe matches but the model name improved.
  4. chunks left in the old 64-dim placeholder are re-embedded; healthy
     chunks are untouched.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from _smoke_common import install_stub

install_stub()

from hippocampus import MemoryConfig, MemoryService
from hippocampus.diary_store import DiaryChunk
from hippocampus.embeddings import EmbeddingProvider
from hippocampus.types import Engram
from handlers.init import PluginInitializer


class _CountingEmbedder(EmbeddingProvider):
    def __init__(self, dim=4):
        self._dim = dim
        self.calls = 0

    @property
    def dim(self):
        return self._dim

    def embed(self, text):
        self.calls += 1
        v = [0.0] * self._dim
        v[0] = 1.0
        return v


def banner(t):
    print(chr(10) + "=== " + t + " ===")


def _svc(tmp, dim=4):
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "h.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.enable_atom_extraction = False
    cfg.enable_graph_indexing = False
    cfg.dedup_enabled = False
    return MemoryService(cfg, embedder=_CountingEmbedder(dim))


def _init(svc):
    init = PluginInitializer.__new__(PluginInitializer)
    init.service = svc
    init._full_reembed_thread = None
    calls = []
    init._start_stale_reembed = lambda dim=0: calls.append(("stale", dim))
    init._start_full_reembed = lambda probe: calls.append(("full", len(probe)))
    return init, calls


def test_registry_key_and_label_are_separate():
    banner("registry key vs data label")
    svc = _svc(tempfile.mkdtemp())
    svc.set_embedding("hash", label="Qwen/Qwen3-Embedding-8B")
    assert svc.current_embedding() == "hash", svc.current_embedding()
    assert svc.embedding_label() == "Qwen/Qwen3-Embedding-8B"
    e = Engram(content="coffee", embedding=[1.0, 0.0, 0.0, 0.0],
               embedding_model=svc.embedding_label())
    svc.store.upsert(e)
    hits = svc.store.vector_search([1.0, 0.0, 0.0, 0.0], 3,
                                   embedding_model=svc.embedding_label())
    assert any(h[0].id == e.id for h in hits), hits
    svc.set_embedding("hash")
    assert svc.embedding_label() == "hash", svc.embedding_label()
    svc.close()
    print("registry/label split: OK")


def test_retag_rewrites_labels_without_embedding():
    banner("retag relabels rows and keeps recall working")
    svc = _svc(tempfile.mkdtemp())
    svc.set_embedding("hash", label="astrmock")
    e = Engram(content="old label", embedding=[1.0, 0.0, 0.0, 0.0],
               embedding_model="astrmock")
    svc.store.upsert(e)
    ds = svc.diary_store
    ch = DiaryChunk(diary_id="d1", text="chunk",
                    embedding=[1.0, 0.0, 0.0, 0.0],
                    embedding_model="astrmock")
    ds.add_chunks([ch])
    calls = svc.embedder.calls
    n = svc.retag_embeddings("Qwen/X")
    assert n == 2, n
    assert svc.embedder.calls == calls, "retag must not call embed()"
    assert svc.store.get(e.id).embedding_model == "Qwen/X"
    labels = {c.embedding_model for c in ds.all_chunks()}
    assert labels == {"Qwen/X"}, labels
    hits = svc.store.vector_search([1.0, 0.0, 0.0, 0.0], 3,
                                   embedding_model="Qwen/X")
    assert any(h[0].id == e.id for h in hits), hits
    svc.close()
    print("retag: OK")


def test_same_space_relabels_instead_of_reembedding():
    banner("same-space fingerprint with a better name relabels")
    svc = _svc(tempfile.mkdtemp())
    svc.set_embedding("hash", label="Qwen/X")
    e = Engram(content="old", embedding=[1.0, 0.0, 0.0, 0.0],
               embedding_model="astrmock")
    svc.store.upsert(e)
    svc.store.set_meta(PluginInitializer._FP_KEY, json.dumps({
        "model": "astrmock", "dim": 4, "probe": [1.0, 0.0, 0.0, 0.0]}))
    init, calls = _init(svc)
    init._check_embedding_space([1.0, 0.0, 0.0, 0.0])
    assert calls == [("stale", 4)], calls
    fp = json.loads(svc.store.get_meta(PluginInitializer._FP_KEY))
    assert fp["model"] == "Qwen/X", fp
    assert svc.store.get(e.id).embedding_model == "Qwen/X"
    assert svc.embedder.calls == 0, svc.embedder.calls
    svc.close()
    print("same-space relabel: OK")


def test_mismatched_chunks_are_reembedded():
    banner("dimension-mismatched chunks are repaired")
    svc = _svc(tempfile.mkdtemp(), dim=4)
    svc.set_embedding("hash", label="m")
    ds = svc.diary_store
    bad = DiaryChunk(diary_id="d1", text="legacy", embedding=[1.0, 0.0],
                     embedding_model="hash")
    good = DiaryChunk(diary_id="d1", text="healthy",
                      embedding=[1.0, 0.0, 0.0, 0.0], embedding_model="m")
    ds.add_chunks([bad, good])
    assert set(ds.chunk_ids_mismatching(4)) == {bad.id}
    calls = svc.embedder.calls
    n = svc.reembed_mismatched_chunks(4)
    assert n == 1, n
    assert svc.embedder.calls == calls + 1, svc.embedder.calls
    assert ds.chunk_ids_mismatching(4) == []
    rows = {c.id: c for c in ds.all_chunks()}
    assert len(rows[bad.id].embedding) == 4, rows[bad.id].embedding
    assert rows[bad.id].text == "legacy"
    assert len(rows[good.id].embedding) == 4
    svc.close()
    print("chunk dim repair: OK")


def main():
    test_registry_key_and_label_are_separate()
    test_retag_rewrites_labels_without_embedding()
    test_same_space_relabels_instead_of_reembedding()
    test_mismatched_chunks_are_reembedded()
    print(chr(10) + "ALL OK (v1.79.3)")


if __name__ == "__main__":
    main()
