"""v1.64 B14 smoke: /mem debug command diagnostic output.

Covers:
- DualRouteRetriever.explain() now includes the spread route
  (was a latent inconsistency vs search()).
- handlers.format.format_debug() renders all 3 sections:
  route distribution / top-k detail / candidates cut by MMR.
- format_debug handles 0 hits without crashing.
- format_debug output mentions engram id and summary snippet.
- Empty query returns a usage hint.
- explain() / search() route composition stays aligned after the fix
  (B14 invariant: every search() result has an explain() attribution).
- /mem debug command is wired through CommandRouter.

v1.64: B14 ship. Pairs with format_debug() in handlers/format and
the spread-route extension in dual_route.explain().
"""
import os, sys, types, tempfile


from _smoke_common import install_stub as _install_stub


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from hippocampus.i18n_backend import init as i18n_init
i18n_init("en")  # B14: format_debug uses t() calls, i18n must be booted before import

from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.retrieval.dual_route import DualRouteRetriever, DualRouteConfig, RouteKind


def _mk(db):
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False
    return MemoryService(cfg)


def main():
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    svc = _mk(db)
    try:
        common = dict(actor_id="u1", platform="qq", channel_id="g1", persona_id="")
        # Seed 3 engrams (cats / fish / dogs)
        e1 = svc.observe(session_id="sess_test", content="talk about cats", **common)
        e2 = svc.observe(session_id="sess_test", content="cats love fish", **common)
        e3 = svc.observe(session_id="sess_test", content="dog park visit", **common)

        # ---- test 1: explain() routes are valid RouteKind members ----
        dr = DualRouteRetriever(svc, DualRouteConfig())
        cue = Cue(text="cats", k=3)
        hits = dr.explain(cue)
        routes_seen = {h.route for h in hits}
        # DOCUMENT is the baseline; graph + spread may or may not fire
        # depending on entity extraction / activation. We just check
        # the values are all valid RouteKind members and DOCUMENT is
        # always present when the corpus has lexical matches.
        assert all(isinstance(r, RouteKind) for r in routes_seen)
        assert RouteKind.DOCUMENT in routes_seen, routes_seen
        print(f"[OK] explain() routes: {sorted(r.value for r in routes_seen)}")

        # ---- test 2: format_debug 0 hits does not crash ----
        from handlers.format import format_debug
        out_empty = format_debug(svc, "zzz_no_match_query_xyzzy")
        assert "## debug" in out_empty
        assert "no results" in out_empty or "0 hits" in out_empty
        print(f"[OK] format_debug 0-hit: {len(out_empty)} chars")

        # ---- test 3: format_debug with hits shows all 3 sections ----
        out = format_debug(svc, "cats", k=2)
        assert "## debug: cats" in out
        assert "### route distribution" in out
        assert "### top-k" in out
        assert "### summary" in out
        assert "document" in out  # at least the document route
        print(f"[OK] format_debug hits: {len(out)} chars, all 3 sections present")

        # ---- test 4: format_debug includes engram id + summary ----
        assert e1.id[:8] in out or e2.id[:8] in out
        assert "talk about cats" in out or "cats love fish" in out
        print(f"[OK] format_debug content: engram id and summary rendered")

        # ---- test 5: empty query returns usage hint ----
        out_usage = format_debug(svc, "")
        assert "usage" in out_usage
        print(f"[OK] format_debug empty query: usage hint shown")

        # ---- test 6: B14 invariant -- explain() attributes every search() hit ----
        # After the spread-route fix, both methods use the same routes
        # tuple. So every engram in search()'s top-k must have at least
        # one matching RouteHit from explain().
        cue2 = Cue(text="cats", k=5)
        res = dr.search(cue2)
        hits2 = dr.explain(cue2)
        for e in res.engrams:
            e_hits = [h for h in hits2 if h.engram.id == e.id]
            assert len(e_hits) >= 1, f"no explain hit for search result {e.id[:8]}"
        print(f"[OK] explain()/search() alignment: {len(res.engrams)} results all explained")

        # ---- test 7: small k forces the "candidates cut" section ----
        out_cut = format_debug(svc, "cats", k=1)
        # Either some candidates were cut, or all RRF survivors fit in
        # k=1; both states must render the section header.
        assert "### candidates cut" in out_cut
        print(f"[OK] format_debug k=1: cut section present")

        # ---- test 8: format_debug is exposed via handlers package ----
        from handlers import format_debug as fmt_dbg_top
        assert fmt_dbg_top is format_debug
        print(f"[OK] format_debug re-exported from handlers package")

        # ---- test 9: CommandRouter has the 'mem debug' entry ----
        from handlers.commands import CommandRouter
        class _Stub:
            def __getattr__(self, n): return lambda *a, **kw: None
        rt = CommandRouter(_Stub(), _Stub(), _Stub())
        assert "mem debug" in rt._table, "mem debug missing from CommandRouter"
        assert rt._table["mem debug"] == "recall.cmd_mem_debug"
        print(f"[OK] CommandRouter registers 'mem debug' -> recall.cmd_mem_debug")

        print("ALL PASS v68-mem-debug")
    finally:
        try: svc.close()
        except Exception: pass
        try: os.remove(db)
        except Exception: pass


if __name__ == "__main__":
    main()
