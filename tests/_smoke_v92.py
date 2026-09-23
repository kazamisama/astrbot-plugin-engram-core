"""Smoke v1.76.27: two more ways the same text survived a forget, and the one
way memory markup got INTO the store in the first place.

D1  `_cascade_derived` deleted the edge LINK but never the edge ROW.
    `graph_edge_memories_v2` is an `(edge_id, source_memory_id)` link table, so
    an edge can be shared and the delete must not be keyed by owner alone.
    Measured live 2026-09-23 before this fix:

        graph_edges_v2             946 rows
          owned by forgotten ones  483
          still linked             482   <- every forget since v1.76.24

    `graph_engram_refs` (entity -> engram) was never cascaded either: 75 rows
    pointed at forgotten engrams, 2 at engrams that no longer existed. Those
    are only harmless because `GraphRetriever._passes_filters` re-checks
    `forgotten_at`; they are still accumulation.

D2  The no-LLM summarizer fallback stored the RAW transcript as `summary` and
    `content`. `content` is what auto-inject prefers, so the bot's own
    `<output>` XML and `[HH:MM 我]` turn markers were injected as remembered
    facts. Three such engrams existed; `e1c662ffc64a4324` was the FIRST
    injected memory on every turn from 11:11 to 14:29 on 2026-09-23.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig                     # noqa: E402
from hippocampus.embeddings import HashEmbeddingProvider        # noqa: E402
from hippocampus.service import MemoryService                   # noqa: E402
from hippocampus.summarizer import (                            # noqa: E402
    ConversationSummarizer, _sanitize_transcript)
from hippocampus.types import Engram                            # noqa: E402

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
    from hippocampus.graph_store import GraphStore
    GraphStore(cfg.sqlite_path)
    return MemoryService(cfg, embedder=HashEmbeddingProvider(dim=32))


def _engram(svc, content, persona="p1"):
    e = Engram(content=content, summary=content, actor_id="u",
               persona_id=persona, session_id="s1", strength=1.0)
    e.embedding = svc.embedder.embed(content)
    svc.store.upsert(e)
    return e


def _edge(svc, owner_ids, key):
    """Create one edge owned by every id in owner_ids (link table is M:N)."""
    c = svc.store._conn
    now = time.time()
    cur = c.execute(
        "INSERT INTO graph_edges_v2(edge_key, source_node_id, target_node_id,"
        " relation_type, source_memory_id, weight, confidence, status, metadata,"
        " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (key, 1, 2, "describes", owner_ids[0], 1.0, 1.0, "active", "{}", now, now))
    eid = cur.lastrowid
    for oid in owner_ids:
        c.execute("INSERT INTO graph_edge_memories_v2(edge_id, source_memory_id)"
                  " VALUES(?,?)", (eid, oid))
    return eid


def _count(svc, sql, *p):
    return svc.store._conn.execute(sql, p).fetchone()[0]


def test_sole_owner_edge_is_deleted():
    banner("D1: an edge whose ONLY owner is the forgotten engram is deleted")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "a.db")
    a = _engram(svc, "memory with an edge")
    edge = _edge(svc, [a.id], "k-a")
    check(_count(svc, "SELECT COUNT(*) FROM graph_edges_v2 WHERE id=?", edge) == 1,
          "edge exists before")
    svc.store.soft_forget(a.id)
    check(_count(svc, "SELECT COUNT(*) FROM graph_edges_v2 WHERE id=?", edge) == 0,
          "edge ROW is gone after the forget (this is the D1 regression)")
    check(_count(svc, "SELECT COUNT(*) FROM graph_edge_memories_v2 WHERE edge_id=?",
                 edge) == 0, "its link row is gone too")
    svc.store.close()


def test_shared_edge_survives():
    banner("D1: an edge SHARED with a live engram survives")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "b.db")
    a = _engram(svc, "doomed")
    b = _engram(svc, "survivor")
    edge = _edge(svc, [a.id, b.id], "k-shared")
    svc.store.soft_forget(a.id)
    check(_count(svc, "SELECT COUNT(*) FROM graph_edges_v2 WHERE id=?", edge) == 1,
          "shared edge SURVIVES the forget")
    check(_count(svc, "SELECT COUNT(*) FROM graph_edge_memories_v2 WHERE edge_id=?"
                 " AND source_memory_id=?", edge, b.id) == 1,
          "the live engram's link SURVIVES")
    check(_count(svc, "SELECT COUNT(*) FROM graph_edge_memories_v2 WHERE edge_id=?"
                 " AND source_memory_id=?", edge, a.id) == 0,
          "the forgotten engram's link is gone")
    svc.store.close()


def test_edge_still_used_by_an_entry_survives():
    banner("D1: an edge another engram's entry still points at survives")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "c.db")
    a = _engram(svc, "doomed")
    b = _engram(svc, "owner of the entry")
    edge = _edge(svc, [a.id], "k-in-use")
    c = svc.store._conn
    now = time.time()
    c.execute(
        "INSERT INTO graph_entries_v2(entry_key, source_memory_id, session_id,"
        " persona_id, scope_id, entry_type, relation_type, content, metadata,"
        " edge_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("e1", b.id, "s1", "p1", "", "summary", "", "entry text", "{}", edge, now, now))
    c.commit()
    svc.store.soft_forget(a.id)
    check(_count(svc, "SELECT COUNT(*) FROM graph_edges_v2 WHERE id=?", edge) == 1,
          "edge kept because a live entry still references it")
    svc.store.close()


def test_engram_refs_are_cascaded():
    banner("D1: graph_engram_refs rows for the forgotten engram are deleted")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp, "d.db")
    a = _engram(svc, "doomed")
    b = _engram(svc, "survivor")
    c = svc.store._conn
    for eid in (a.id, b.id):
        c.execute("INSERT INTO graph_engram_refs(entity_id, engram_id, weight)"
                  " VALUES(?,?,?)", ("ent-" + eid[:6], eid, 1.0))
    c.commit()
    svc.store.soft_forget(a.id)
    check(_count(svc, "SELECT COUNT(*) FROM graph_engram_refs WHERE engram_id=?",
                 a.id) == 0, "forgotten engram's refs are GONE")
    check(_count(svc, "SELECT COUNT(*) FROM graph_engram_refs WHERE engram_id=?",
                 b.id) == 1, "the OTHER engram's refs are untouched")
    svc.store.close()


def test_sanitize_transcript():
    banner("D2: transcript scaffolding is stripped")
    raw = ("[23:21 風見かずき] 那那里能画吗，只给我看，可以吗 "
           "[23:21 我] <output>   <message>那里不行。只给您看也一样。</message> </output> "
           "[23:23 風見かずき] reset")
    got = _sanitize_transcript(raw)
    print("  -> %r" % got)
    check("<output>" not in got and "</output>" not in got, "no <output> tag")
    check("<message>" not in got and "</message>" not in got, "no <message> tag")
    check("[23:21" not in got and "[23:23" not in got, "no [HH:MM speaker] marker")
    check("那里不行" in got and "reset" in got, "the actual words survive")
    check("  " not in got, "runs of spaces collapsed")
    plain = "这是一段没有任何脚手架的正常文本。"
    check(_sanitize_transcript(plain) == plain, "plain text passes through unchanged")
    check(_sanitize_transcript("") == "", "empty stays empty")
    check(_sanitize_transcript(None) == "", "None does not raise")


class _FakeRec:
    chat_type = "private"

    def __init__(self, text):
        self._t = text

    def transcript(self):
        return self._t

    def participants(self, include_bot=False):
        return []


def test_fallback_output_is_clean():
    banner("D2: the fallback's own output carries no markup and is tagged")
    s = ConversationSummarizer.__new__(ConversationSummarizer)
    s._cfg = MemoryConfig()
    rec = _FakeRec("[23:21 我] <output><message>画好了，停在裙边。</message></output>")
    out = s._fallback(rec, 400)
    print("  -> %r" % out["summary"])
    check("<output>" not in out["summary"], "fallback summary has no XML")
    check("[23:21" not in out["summary"], "fallback summary has no turn marker")
    check(out.get("_fallback") is True,
          "the result is marked _fallback so store_summary can tag it")


if __name__ == "__main__":
    test_sole_owner_edge_is_deleted()
    test_shared_edge_survives()
    test_edge_still_used_by_an_entry_survives()
    test_engram_refs_are_cascaded()
    test_sanitize_transcript()
    test_fallback_output_is_clean()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.27 checks passed")
