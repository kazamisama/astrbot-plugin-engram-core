"""Smoke v1.76.24 forgetting must cascade to derived rows + the tier fix.

Two defects, both found on 2026-09-22 chasing "she knows something she cannot
possibly know":

D1  `soft_forget` marked `engrams.forgotten_at` and nothing else. The same
    text also lives in `graph_entries_v2` (keyed `source_memory_id`) and in
    `memory_sources` (keyed `memory_id`), neither of which has any forgetting
    concept -- so a "forgotten" memory stayed fully readable. Measured live,
    right after a dashboard soft-forget of an engram about a skirt:

        graph_entries_v2  '水手服' 82 rows  '深蓝' 82  '一寸' 208
        memory_sources    '一寸'   10 rows  '水手服' 3

    Consequence observed live: /reset at 21:44:22, same question at 21:44:28,
    the identical text back at 21:44:36 -- six seconds later.

D2  `TieringEngine.reclassify_all` passed `now` where the first placeholder
    (the forgotten branch) needed `COLD`, so every soft-forgotten engram got
    `tier = <epoch float>`; all of them shared one value and it was rewritten
    on every sweep. Live: '1790078743.84863' x18, 18 of 19 forgotten rows.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig            # noqa: E402
from hippocampus.embeddings import HashEmbeddingProvider  # noqa: E402
from hippocampus.service import MemoryService         # noqa: E402
from hippocampus.tiering import TieringEngine         # noqa: E402
from hippocampus.types import Engram                  # noqa: E402

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


def _add_graph_row(svc, source_id, text):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    svc.store._conn.execute(
        "INSERT INTO graph_entries_v2"
        "(entry_key, source_memory_id, session_id, persona_id, scope_id,"
        " entry_type, relation_type, content, metadata, edge_id,"
        " created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("k-" + source_id + text[:6], source_id, "s1", "p1", "", "summary",
         "", text, "{}", None, now, now))
    svc.store._conn.commit()


def _count(svc, sql, *p):
    return svc.store._conn.execute(sql, p).fetchone()[0]


def test_forget_cascades_and_spares_others():
    banner("D1: soft_forget removes derived rows, keeps the engram, spares others")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "a.db")
    a = Engram(content="target memory", summary="target memory", actor_id="u",
               persona_id="p1", session_id="s1", strength=1.0)
    b = Engram(content="bystander memory", summary="bystander memory", actor_id="u",
               persona_id="p1", session_id="s1", strength=1.0)
    svc.store.upsert(a)
    svc.store.upsert(b)
    for eid in (a.id, b.id):
        _add_graph_row(svc, eid, "fact about " + eid[:6])
        svc.store.save_memory_source(eid, [{"speaker": "u", "content": "raw line"}])

    before_g = _count(svc, "select count(*) from graph_entries_v2 where source_memory_id=?", a.id)
    before_s = _count(svc, "select count(*) from memory_sources where memory_id=?", a.id)
    check(before_g == 1, "target has 1 graph entry before (got %d)" % before_g)
    check(before_s == 1, "target has 1 memory_sources row before (got %d)" % before_s)

    ok = svc.store.soft_forget(a.id)
    check(ok is True, "soft_forget returned True")

    check(_count(svc, "select count(*) from graph_entries_v2 where source_memory_id=?", a.id) == 0,
          "graph entry for the forgotten engram is GONE")
    check(_count(svc, "select count(*) from memory_sources where memory_id=?", a.id) == 0,
          "memory_sources row for the forgotten engram is GONE")
    check(_count(svc, "select count(*) from graph_entries_v2_fts where entry_id not in "
                      "(select id from graph_entries_v2)") == 0,
          "no orphan rows left in graph_entries_v2_fts")

    row = svc.store._conn.execute(
        "select forgotten_at, strength from engrams where id=?", (a.id,)).fetchone()
    check(row is not None, "the engram row itself is still there (soft, not hard)")
    check((row[0] or 0) > 0, "forgotten_at is set")
    check(float(row[1] or 0) == 0.0, "strength zeroed")

    check(_count(svc, "select count(*) from graph_entries_v2 where source_memory_id=?", b.id) == 1,
          "the OTHER engram's graph entry is untouched")
    check(_count(svc, "select count(*) from memory_sources where memory_id=?", b.id) == 1,
          "the OTHER engram's memory_sources row is untouched")
    svc.store.close()


def test_double_forget_is_noop():
    banner("D1: a second soft_forget returns False and changes nothing")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "b.db")
    a = Engram(content="x", summary="x", actor_id="u", strength=1.0)
    svc.store.upsert(a)
    _add_graph_row(svc, a.id, "f")
    check(svc.store.soft_forget(a.id) is True, "first forget True")
    check(svc.store.soft_forget(a.id) is False, "second forget False")
    check(_count(svc, "select count(*) from graph_entries_v2 where source_memory_id=?", a.id) == 0,
          "still no derived rows")
    svc.store.close()


def test_tier_of_forgotten_is_cold():
    banner("D2: reclassify_all gives a forgotten engram tier='cold', not an epoch")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "c.db")
    e = Engram(content="forgotten one", summary="forgotten one", actor_id="u",
               persona_id="p1", strength=1.0)
    svc.store.upsert(e)
    svc.store.soft_forget(e.id)
    live = Engram(content="live one", summary="live one", actor_id="u",
                  persona_id="p1", strength=1.0)
    svc.store.upsert(live)

    TieringEngine(svc.store, svc.cfg).reclassify_all()
    rows = dict(svc.store._conn.execute("select id, tier from engrams").fetchall())
    print("  tiers: %s" % rows)
    t_forg = str(rows.get(e.id))
    check(t_forg == "cold", "forgotten engram tier == 'cold' (got %r)" % t_forg)
    check(not t_forg.replace(".", "").isdigit(),
          "tier is not a numeric epoch string")
    check(str(rows.get(live.id)) in ("hot", "warm", "cold"),
          "live engram has a real tier label (%r)" % rows.get(live.id))
    svc.store.close()


if __name__ == "__main__":
    test_forget_cascades_and_spares_others()
    test_double_forget_is_noop()
    test_tier_of_forgotten_is_cold()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.24 checks passed")
