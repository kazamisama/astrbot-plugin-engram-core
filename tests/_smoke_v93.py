"""Smoke v1.76.28: re-summarizing must REPLACE the derived graph rows, not
stack a new layer on top of them.

`resummarize_engram` rewrites `engrams.summary` / `content` / embedding in
place, then called `_post_ingest` -- which only ever ADDS graph rows. The rows
extracted from the previous text were never removed, so each re-summarize left
another copy of the old text in the graph.

Measured live 2026-09-23 on `b7776817155a4d7a84ed8682ef84d625`, the one engram
whose text was a raw transcript (`[20:04 X] … <output><message>…`):

    before re-summarize   graph_entries_v2 = 3   (all 3 carried <output>)
    after  re-summarize   graph_entries_v2 = 12  (the same 3 + 9 new)

The 3 stale rows had to be deleted by hand. The hard-delete cascade already
did this teardown (`remove_engram_refs` + `delete_graph_memory_v2`); the
re-summarize path, which rewrites exactly the same derived data, did not.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig                    # noqa: E402
from hippocampus.embeddings import HashEmbeddingProvider       # noqa: E402
from hippocampus.llm import ProxyLLMProvider                   # noqa: E402
from hippocampus.service import MemoryService                  # noqa: E402
from hippocampus.types import Engram                           # noqa: E402

FAILURES = []


def banner(m):
    print("\n=== " + m + " ===")


def check(c, m):
    if c:
        print("  OK   " + m)
    else:
        print("  FAIL " + m)
        FAILURES.append(m)


class _StubLLM:
    """ProxyLLMProvider returning a fixed, valid summary JSON + a call count."""

    def __init__(self, summary):
        self._summary = summary
        self.calls = 0
        self.provider = ProxyLLMProvider("stub", self._call)

    def _call(self, system=None, user=None, **kw):
        self.calls += 1
        import json
        return json.dumps({
            "summary": self._summary,
            "key_facts": ["要点一", "要点二"],
            "topics": ["话题甲"],
            "participants": ["風見かずき"],
            "relations": [],
        }, ensure_ascii=False)


def _svc(tmp, name, llm):
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, name)
    cfg.embedding_name = "testemb"
    from hippocampus.graph_store import GraphStore
    GraphStore(cfg.sqlite_path)
    return MemoryService(cfg, embedder=HashEmbeddingProvider(dim=32),
                         llm=llm.provider)


def _count(svc, sql, *p):
    return svc.store._conn.execute(sql, p).fetchone()[0]


def _setup(svc, old_text):
    e = Engram(content=old_text, summary=old_text, actor_id="u",
               persona_id="p1", session_id="s1", strength=1.0)
    e.embedding = svc.embedder.embed(old_text)
    svc.store.upsert(e)
    svc._post_ingest(e)                     # v1: the rows we must not keep
    svc.store.save_memory_source(e.id, [
        {"actor_id": "u", "speaker": "風見かずき", "content": "原始消息一",
         "ts": 1000.0, "is_bot": False},
        {"actor_id": "b", "speaker": "Mortis", "content": "原始消息二",
         "ts": 1060.0, "is_bot": True},
    ])
    return e


def test_resummarize_replaces_instead_of_stacking():
    banner("v1.76.28: re-summarize does not stack a second layer of graph rows")
    tmp = tempfile.mkdtemp()
    llm = _StubLLM("重写后的干净摘要，内容已经换掉了。")
    svc = _svc(tmp, "a.db", llm)
    e = _setup(svc, "旧的原始正文 [20:04 X] <output><message>旧内容</message></output>")

    old_ids = [r[0] for r in svc.store._conn.execute(
        "SELECT id FROM graph_entries_v2 WHERE source_memory_id=?", (e.id,))]
    check(len(old_ids) > 0, "the first ingest created graph rows (%d)" % len(old_ids))
    old_with_xml = _count(
        svc, "SELECT COUNT(*) FROM graph_entries_v2 WHERE source_memory_id=? "
             "AND content LIKE '%<output>%'", e.id)
    check(old_with_xml > 0, "and they carry the old markup (%d rows)" % old_with_xml)

    ok = svc.resummarize_engram(e.id)
    check(ok is True, "resummarize_engram returned True")
    check(llm.calls >= 1, "the summarizer LLM was actually called")

    new_ids = [r[0] for r in svc.store._conn.execute(
        "SELECT id FROM graph_entries_v2 WHERE source_memory_id=?", (e.id,))]
    print("  old entry ids: %s" % old_ids)
    print("  new entry ids: %s" % new_ids)
    survivors = [i for i in old_ids if i in new_ids]
    check(not survivors,
          "NONE of the pre-rewrite graph rows survive (this is the regression); "
          "leaked=%s" % survivors)
    leftover_xml = _count(
        svc, "SELECT COUNT(*) FROM graph_entries_v2 WHERE source_memory_id=? "
             "AND content LIKE '%<output>%'", e.id)
    check(leftover_xml == 0,
          "no graph row still carries the old <output> markup (got %d)" % leftover_xml)
    check(len(new_ids) > 0, "the new text produced its own rows (%d)" % len(new_ids))

    row = svc.store.get(e.id)
    check(row.content.startswith("重写后的干净摘要"), "engram content was rewritten")
    svc.store.close()


def test_resummarize_twice_does_not_grow_forever():
    banner("v1.76.28: a second re-summarize does not grow the row count")
    tmp = tempfile.mkdtemp()
    llm = _StubLLM("第一版重写摘要。")
    svc = _svc(tmp, "b.db", llm)
    e = _setup(svc, "最开始的一段正文")

    svc.resummarize_engram(e.id)
    first = _count(svc, "SELECT COUNT(*) FROM graph_entries_v2 WHERE source_memory_id=?",
                   e.id)
    llm._summary = "第二版重写摘要。"
    svc.resummarize_engram(e.id)
    second = _count(svc, "SELECT COUNT(*) FROM graph_entries_v2 WHERE source_memory_id=?",
                    e.id)
    print("  after 1st=%d  after 2nd=%d" % (first, second))
    check(second == first,
          "row count is stable across re-summarizes (%d -> %d)" % (first, second))
    svc.store.close()


def test_other_engrams_are_untouched():
    banner("v1.76.28: a sibling engram's graph rows survive")
    tmp = tempfile.mkdtemp()
    llm = _StubLLM("重写摘要。")
    svc = _svc(tmp, "c.db", llm)
    a = _setup(svc, "要被重写的那条正文")
    b = Engram(content="旁观者的正文", summary="旁观者的正文", actor_id="u",
               persona_id="p1", session_id="s1", strength=1.0)
    b.embedding = svc.embedder.embed(b.content)
    svc.store.upsert(b)
    svc._post_ingest(b)
    b_before = _count(svc, "SELECT COUNT(*) FROM graph_entries_v2 WHERE source_memory_id=?",
                      b.id)
    check(b_before > 0, "sibling has graph rows (%d)" % b_before)
    svc.resummarize_engram(a.id)
    b_after = _count(svc, "SELECT COUNT(*) FROM graph_entries_v2 WHERE source_memory_id=?",
                     b.id)
    check(b_after == b_before,
          "sibling's rows are unchanged (%d -> %d)" % (b_before, b_after))
    b_row = svc.store.get(b.id)
    check(b_row.content == "旁观者的正文", "sibling's text is unchanged")
    svc.store.close()


if __name__ == "__main__":
    test_resummarize_replaces_instead_of_stacking()
    test_resummarize_twice_does_not_grow_forever()
    test_other_engrams_are_untouched()
    print()
    if FAILURES:
        print("FAILED %d check(s):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all v1.76.28 checks passed")
