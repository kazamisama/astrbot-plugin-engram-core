"""Smoke v1.76.26: working memory must not re-inject a forgotten engram.

Found on 2026-09-22 while chasing a memory that would not go away. Live facts:

    851db5a64b1f4f33  created 22:49:12   soft-forgotten 23:23:25
    84fa5c19353946c8  created 23:20:49   soft-forgotten 23:23:17
    request at 23:32:33 -- nine minutes after the forget -- still carried
    BOTH, as the first two entries of the injected [长期记忆] block.

`WorkingMemory.snapshot()` returns the in-process Engram object captured at
observation time and never re-reads the store, so `forgotten_at` on that copy
stays 0. `MemoryService.recall()` prepends the cell with score 1.0 -- ahead of
every retrieval route -- while all four routes (vector / fts / graph / spread
/ atom) do filter forgotten rows. Working memory was the one merge point that
did not, and it evicts only at `working_memory_capacity` (32) memories or
`working_memory_idle_seconds` (24h), so a mid-session forget had no effect.

The buffered copy is stale the other way too: an edit or a hard delete made
after buffering never reached the injected block.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig                # noqa: E402
from hippocampus.embeddings import HashEmbeddingProvider   # noqa: E402
from hippocampus.service import MemoryService              # noqa: E402
from hippocampus.types import Cue, Engram                  # noqa: E402

FAILURES = []


def banner(m):
    print("\n=== " + m + " ===")


def check(c, m):
    if c:
        print("  OK   " + m)
    else:
        print("  FAIL " + m)
        FAILURES.append(m)


def _svc(tmp, name):
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, name)
    cfg.embedding_name = "testemb"
    # the graph tables are created by GraphStore, not by a fresh store
    from hippocampus.graph_store import GraphStore
    GraphStore(cfg.sqlite_path)
    return MemoryService(cfg, embedder=HashEmbeddingProvider(dim=32))


def _mk(svc, content, persona="p1", session="s1"):
    e = Engram(content=content, summary=content, actor_id="u",
               persona_id=persona, session_id=session, strength=1.0)
    e.embedding = svc.embedder.embed(content)
    svc.store.upsert(e)
    svc.working.add(e)          # what observe()/post-ingest does in production
    return e


def _cue(text="skirt memory", **kw):
    base = dict(actor_id="u", persona_id="p1", session_id="s1", k=5,
                memory_types=["episodic", "semantic", "prospective"])
    base.update(kw)
    return Cue(text=text, **base)


def _ids(svc, cue):
    # the dashboard forget path does this too (page_api_modules/memory.py);
    # without it a repeated identical cue would be served from _recall_cache
    # and this test would be measuring the cache, not working memory.
    svc._invalidate_search_cache()
    return [e.id for e in svc.recall(cue).engrams]


def test_forgotten_wm_item_is_not_injected():
    banner("v1.76.26: a forgotten engram still in working memory is NOT injected")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "a.db")
    a = _mk(svc, "the skirt memory that must go away")
    b = _mk(svc, "an unrelated live memory")

    cue = _cue()
    before = _ids(svc, cue)
    print("  before forget: %s" % [i[:8] for i in before])
    check(a.id in before, "target is injected while live")
    check(before and before[0] == a.id,
          "and it is injected FIRST (working memory scores 1.0)")
    check(b.id in before, "the sibling is injected too")

    ok = svc.store.soft_forget(a.id)
    check(ok is True, "soft_forget returned True")

    after = _ids(svc, _cue())
    print("  after forget:  %s" % [i[:8] for i in after])
    check(a.id not in after,
          "target is GONE from recall once forgotten (this is the regression)")
    check(a.id not in [x[:len(a.id)] for x in after] or a.id not in after,
          "no truncated-id variant either")
    check(b.id in after, "the live sibling is STILL injected")

    # the buffer itself is untouched -- the fix is at the merge point
    check(any(e.id == a.id for e in svc.working.snapshot("s1")),
          "working memory still holds the object (fix is at the merge point)")
    svc.store.close()


def test_restore_brings_it_back():
    banner("v1.76.26: restore() is honoured too (freshness, not one-way filter)")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "b.db")
    a = _mk(svc, "restorable memory")
    svc.store.soft_forget(a.id)
    check(a.id not in _ids(svc, _cue()), "gone after forget")
    svc.store.restore(a.id)
    check(a.id in _ids(svc, _cue()), "back after restore")
    svc.store.close()


def test_edited_wm_item_injects_current_text():
    banner("v1.76.26: an edited engram injects its CURRENT text, not the buffer copy")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "c.db")
    a = _mk(svc, "OLD text that was buffered")
    fresh = svc.store.get(a.id)
    fresh.content = "NEW text written after buffering"
    fresh.summary = fresh.content
    fresh.embedding = svc.embedder.embed(fresh.content)
    svc.store.upsert(fresh)

    got = svc.recall(_cue()).engrams
    mine = [e for e in got if e.id == a.id]
    check(len(mine) == 1, "the engram is still returned once (got %d)" % len(mine))
    if mine:
        print("  injected content: %r" % (mine[0].content[:60],))
        check(mine[0].content == "NEW text written after buffering",
              "injected content is the post-edit text")
    svc.store.close()


def test_hard_deleted_wm_item_is_dropped():
    banner("v1.76.26: a hard-deleted engram in the buffer does not crash recall")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "d.db")
    a = _mk(svc, "memory about to be hard deleted")
    keep = _mk(svc, "survivor")
    check(a.id in _ids(svc, _cue()), "present before delete")
    svc.store.delete(a.id)
    after = _ids(svc, _cue())
    print("  after hard delete: %s" % [i[:8] for i in after])
    check(a.id not in after, "hard-deleted engram is not injected")
    check(keep.id in after, "survivor still injected")
    svc.store.close()


if __name__ == "__main__":
    test_forgotten_wm_item_is_not_injected()
    test_restore_brings_it_back()
    test_edited_wm_item_injects_current_text()
    test_hard_deleted_wm_item_is_dropped()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.26 checks passed")
