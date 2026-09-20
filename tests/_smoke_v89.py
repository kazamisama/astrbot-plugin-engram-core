"""Smoke v1.76.23 reembed_stale must also catch vectorless rows.

The selector matched on `embedding_model != target` only. A row carrying the
*correct* model label but an empty `embedding_json` therefore never matched,
was never re-embedded, and stayed invisible to vector recall forever (FTS kept
covering it, which is why it went unnoticed).

Found live on 2026-09-21 while auditing a backfill:
    id=55cc73461ca8  2026-07-11 11:18  persona=mortis  episodic
    embedding_model='astrmock'  embedding_json=''      summary=37 chars

Asserted here:
  - a correct-label/empty-vector row IS selected;
  - a wrong-label row is still selected (the original job);
  - '[]' and 'null' are treated as empty, and a real vector is NOT;
  - soft-forgotten rows are still excluded;
  - the re-embedded row ends up with the target label and a real vector.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig                      # noqa: E402
from hippocampus.embeddings import EmbeddingProvider             # noqa: E402
from hippocampus.service import MemoryService                    # noqa: E402
from hippocampus.types import Engram                             # noqa: E402

FAILURES = []


def banner(m):
    print("\n=== " + m + " ===")


def check(c, m):
    if c:
        print("  OK   " + m)
    else:
        print("  FAIL " + m)
        FAILURES.append(m)


class _Emb(EmbeddingProvider):
    """Deterministic 8-dim embedder standing in for the real provider."""

    @property
    def dim(self):
        return 8

    def embed(self, text):
        h = abs(hash(text or ""))
        return [((h >> (i * 3)) & 0xFF) / 255.0 for i in range(8)]


def _mk(cfg, tmp, **over):
    svc = MemoryService(cfg, embedder=_Emb())
    return svc


def test_selector_picks_vectorless_and_mismatched():
    banner("reembed_stale selects empty-vector + wrong-label, skips good/forgotten")
    tmp = tempfile.mkdtemp()
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h.db")
    cfg.embedding_name = "testemb"
    svc = MemoryService(cfg, embedder=_Emb())
    target = svc._current_embedding_name
    check(target == "testemb", "active embedding name is testemb (got %r)" % target)

    good = Engram(content="already fine", summary="already fine", actor_id="u",
                  embedding=[0.1] * 8, embedding_model=target, strength=1.0)
    svc.store.upsert(good)

    empty = Engram(content="correct label but no vector", summary="correct label but no vector",
                   actor_id="u", embedding=[], embedding_model=target, strength=1.0)
    svc.store.upsert(empty)

    bracket = Engram(content="empty list json", summary="empty list json", actor_id="u",
                     embedding=[], embedding_model=target, strength=1.0)
    svc.store.upsert(bracket)
    svc.store._conn.execute("UPDATE engrams SET embedding_json='[]' WHERE id=?", (bracket.id,))
    svc.store._conn.commit()

    wrong = Engram(content="old provider", summary="old provider", actor_id="u",
                   embedding=[0.2] * 64, embedding_model="hash", strength=1.0)
    svc.store.upsert(wrong)

    forgotten = Engram(content="soft deleted", summary="soft deleted", actor_id="u",
                       embedding=[], embedding_model=target, strength=1.0)
    svc.store.upsert(forgotten)
    svc.store._conn.execute("UPDATE engrams SET forgotten_at=1.0 WHERE id=?", (forgotten.id,))
    svc.store._conn.commit()

    n = svc.reembed_stale(limit=100)
    print("  reembed_stale returned %d" % n)
    check(n == 3, "re-embedded exactly 3 (empty + '[]' + wrong-label), got %d" % n)

    def vec_len(eid):
        row = svc.store._conn.execute(
            "SELECT embedding_json FROM engrams WHERE id=?", (eid,)).fetchone()
        try:
            return len(json.loads(row[0] or "[]"))
        except Exception:
            return -1

    check(vec_len(empty.id) == 8, "empty-vector row now has a real vector")
    check(vec_len(bracket.id) == 8, "'[]' row now has a real vector")
    check(vec_len(wrong.id) == 8, "wrong-label row re-embedded to target dim")
    check(vec_len(good.id) == 8, "already-good row untouched (still 8)")
    check(vec_len(forgotten.id) == 0, "soft-forgotten row left alone")

    for eid, want in ((empty.id, target), (bracket.id, target), (wrong.id, target)):
        got = svc.store._conn.execute(
            "SELECT embedding_model FROM engrams WHERE id=?", (eid,)).fetchone()[0]
        check(got == want, "row %s labelled %r" % (eid[:8], got))

    check(svc.reembed_stale(limit=100) == 0, "second pass is a no-op (idempotent)")
    svc.store.close()


def test_real_vector_not_treated_as_empty():
    banner("a real vector is never re-embedded (no false positive)")
    tmp = tempfile.mkdtemp()
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h2.db")
    cfg.embedding_name = "testemb"
    svc = MemoryService(cfg, embedder=_Emb())
    svc.store.upsert(Engram(content="x", summary="x", actor_id="u",
                            embedding=[0.5] * 4096, embedding_model="testemb",
                            strength=1.0))
    check(svc.reembed_stale(limit=100) == 0,
          "4096-dim row is not selected by the length<=4 predicate")
    svc.store.close()


if __name__ == "__main__":
    test_selector_picks_vectorless_and_mismatched()
    test_real_vector_not_treated_as_empty()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.23 checks passed")
