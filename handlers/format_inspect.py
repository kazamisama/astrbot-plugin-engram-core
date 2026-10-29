"""Operational views: debug, dual-route, session, tier.

Split from handlers/format.py at v1.78.1.
"""
from __future__ import annotations

from hippocampus import Cue
from hippocampus.i18n_backend import t, t_or
from hippocampus.observability import note as _obs_note


def format_debug(service, query, k=5, *, persona_id=None, scope_id=None):
    """v1.64 B14 /mem debug: explain why a search returned what it returned.

    Diagnostic report covering:
      1. Route distribution: hit count + top RRF per route (document / graph / spread)
      2. Top-k detail: per-engram RRF score + which route(s) contributed + entity tag
      3. Candidates cut by MMR/k: would-have-been-in-top-k (post-RRF, pre-truncation)
      4. Summary: total fused / returned / cut counts

    Pairs with the underlying DualRouteRetriever.explain() (which was
    extended in B14 to include the spread route, matching search()).

    Use case: a user types `/mem debug <query>` and gets a structured
    answer to "why did this engram surface" or "why didn't that one
    show up", without modifying any code.
    """
    if service is None:
        return t("error.service_not_initialized")
    query = (query or "").strip()
    if not query:
        return t("debug.usage")
    cue = Cue(text=query, k=int(k), actor_id=None, channel_id=None, persona_id=persona_id, scope_id=scope_id)
    from hippocampus.retrieval import DualRouteRetriever
    dr = DualRouteRetriever(service, service._dual_route_config())

    # 1) Run search() to know the actual final top-k (post-MMR + truncation).
    res = dr.search(cue)
    topk_ids = {e.id for e in res.engrams}

    # 2) Run explain() to get per-route hit attribution.
    hits = dr.explain(cue)

    # 3) Aggregate by engram id across all routes.
    by_id: dict = {}
    for h in hits:
        rec = by_id.setdefault(h.engram.id, {
            "doc": 0.0, "graph": 0.0, "spread": 0.0,
            "raw_doc": 0.0, "raw_graph": 0.0, "raw_spread": 0.0,
            "entity": None, "engram": h.engram,
        })
        rec[h.route.value] = h.rrf_contribution
        rec["raw_" + h.route.value] = h.raw_score
        if h.matched_entity:
            rec["entity"] = h.matched_entity

    # 4) Sort fused set by total RRF (descending).
    fused = sorted(
        by_id.values(),
        key=lambda r: r["doc"] + r["graph"] + r["spread"],
        reverse=True,
    )

    # 5) Render.
    lines = [t("debug.header", query=query, k=str(k))]

    # 5a) Route distribution table.
    lines.append("")
    lines.append(t("debug.route_dist_header"))
    route_stats = {"document": 0, "graph": 0, "spread": 0}
    route_top = {"document": 0.0, "graph": 0.0, "spread": 0.0}
    for h in hits:
        route_stats[h.route.value] += 1
        if h.rrf_contribution > route_top[h.route.value]:
            route_top[h.route.value] = h.rrf_contribution
    for rname in ("document", "graph", "spread"):
        n = route_stats[rname]
        if n == 0:
            lines.append(t("debug.route_line_skipped", name=rname))
        else:
            top = "%.4f" % route_top[rname]
            lines.append(t("debug.route_line", name=rname, n=str(n), top=top))

    # 5b) Top-k detail with per-route contribution breakdown.
    lines.append("")
    lines.append(t("debug.topk_header"))
    if not res.engrams:
        lines.append(t("debug.topk_empty"))
    else:
        for i, (e, s) in enumerate(zip(res.engrams, res.scores), 1):
            rec = by_id.get(e.id, {})
            tags = []
            if rec.get("doc", 0.0) > 0:
                tags.append("doc")
            if rec.get("graph", 0.0) > 0:
                tag = "graph"
                if rec.get("entity"):
                    tag += " via " + rec["entity"]
                tags.append(tag)
            if rec.get("spread", 0.0) > 0:
                tags.append("spread")
            tag_str = "+".join(tags) if tags else "?"
            summ = (e.summary or e.content or "")[:50]
            lines.append(
                t("debug.topk_item", i=str(i), score="%.4f" % s,
                  tags=tag_str, id8=e.id[:8], summary=summ)
            )
            parts = []
            for r in ("doc", "graph", "spread"):
                if rec.get(r, 0.0) > 0:
                    parts.append(r + "=" + ("%.4f" % rec[r]))
            if parts:
                lines.append(t("debug.topk_rrf", parts="  ".join(parts)))

    # 5c) Candidates cut by MMR or k.
    cut = [r for r in fused if r["engram"].id not in topk_ids]
    lines.append("")
    lines.append(t("debug.cut_header"))
    if not cut:
        lines.append(t("debug.cut_none"))
    else:
        lines.append(t("debug.cut_total", n=str(len(cut))))
        for rec in cut[:5]:  # cap to 5 to keep output bounded
            e = rec["engram"]
            total = rec["doc"] + rec["graph"] + rec["spread"]
            summ = (e.summary or e.content or "")[:50]
            lines.append(
                t("debug.cut_item", score="%.4f" % total, id8=e.id[:8], summary=summ)
            )
        if len(cut) > 5:
            lines.append(t("debug.cut_more", n=str(len(cut) - 5)))

    # 5d) Summary line.
    lines.append("")
    lines.append(t("debug.summary_header"))
    lines.append(t("debug.summary_candidates", n=str(len(fused))))
    lines.append(t("debug.summary_returned", n=str(len(res.engrams))))
    lines.append(t("debug.summary_cut", n=str(len(cut))))

    return chr(10).join(lines)


def format_dual_route(service, query, k=5, *, persona_id=None, scope_id=None):
    """v1.3 dual-route result renderer.

    Shows hits from both document route (vector+FTS5) and graph route
    (entity match + 1-hop relations), merged by RRF. Each hit is tagged
    with which route(s) contributed and the rrf score breakdown.
    """
    if service is None:
        return t("cmd.service_down")
    query = (query or "").strip()
    if not query:
        return t_or("cmd.dual.usage", "usage: /mem search <q> --mode=dual")
    cue = Cue(text=query, k=int(k), actor_id=None, channel_id=None,
              persona_id=persona_id, scope_id=scope_id)
    from hippocampus.retrieval import DualRouteRetriever, RouteKind
    retriever = DualRouteRetriever(service, service._dual_route_config())
    res = retriever.search(cue)
    if not res.engrams:
        return t_or("cmd.dual.no_hit", "[dual] no hit for: {q}").format(q=query)
    hits = retriever.explain(cue)
    by_id = {}
    for h in hits:
        by_id.setdefault(h.engram.id, {"doc": 0.0, "graph": 0.0, "entity": None})
        if h.route == RouteKind.DOCUMENT:
            by_id[h.engram.id]["doc"] = h.rrf_contribution
        else:
            by_id[h.engram.id]["graph"] = h.rrf_contribution
            if h.matched_entity:
                by_id[h.engram.id]["entity"] = h.matched_entity
    lines = [t_or("cmd.dual.header",
                  "[dual] hits for: {q}  (doc=document-route graph=entity-route)").format(q=query)]
    via = t_or("cmd.dual.via", "via {entity}")
    for e, s in zip(res.engrams, res.scores):
        info = by_id.get(e.id, {})
        tag = ""
        if info.get("doc") and info.get("graph"):
            tag = "[doc+graph"
            if info.get("entity"):
                tag += " " + via.format(entity=info["entity"])
            tag += "]"
        elif info.get("graph"):
            tag = "[graph"
            if info.get("entity"):
                tag += " " + via.format(entity=info["entity"])
            tag += "]"
        else:
            tag = "[doc]"
        summ = (e.summary or e.content or "")[:60]
        lines.append("- " + ("%.3f" % s) + " " + tag + "  " + summ)
    return chr(10).join(lines)


def format_session(service):
    """v1.4 /mem session: render the current session filter policy."""
    if service is None:
        return t("cmd.service_down")
    from hippocampus.session_filter import SessionFilter, FilterContext
    sf = SessionFilter(service.cfg)
    s = sf.summary()
    lines = [t_or("cmd.session.header", "## session filter")]
    lines.append(t_or("cmd.session.enabled", "enabled:        {v}").format(v=s["enabled"]))
    lines.append(t_or("cmd.session.platform_allow", "platform allow:  {v}").format(
        v=_join_or_all(s["platform_allowlist"])))
    lines.append(t_or("cmd.session.platform_block", "platform block:  {v}").format(
        v=_join_or_all(s["platform_blocklist"])))
    lines.append(t_or("cmd.session.channel_allow", "channel  allow:  {v}").format(
        v=_join_or_all(s["channel_allowlist"])))
    lines.append(t_or("cmd.session.channel_block", "channel  block:  {v}").format(
        v=_join_or_all(s["channel_blocklist"])))
    lines.append(t_or("cmd.session.actor_allow", "actor    allow:  {v}").format(
        v=_join_or_all(s["actor_allowlist"])))
    lines.append(t_or("cmd.session.keywords_block", "keywords block:  {v}").format(
        v=_join_or_all(s["blocked_keywords"])))
    if s["enabled"]:
        lines.append("")
        lines.append(t_or("cmd.session.quick_test", "### quick test"))
        for plat, chan, actor, content, label in (
            ("qq", "group-1", "alice", "hello world", t_or("cmd.session.case_priv", "default priv")),
            ("qq", "group-1", "alice", "this is spam content", t_or("cmd.session.case_spam", "default + spam")),
        ):
            d = sf.decide(FilterContext(platform=plat, channel_id=chan,
                                        actor_id=actor, content=content))
            if d.is_pass():
                tag = t_or("cmd.session.pass", "PASS")
            else:
                tag = t_or("cmd.session.deny", "DENY({reason})").format(reason=d.reason)
            lines.append("  [" + label + "] " + tag)
    return chr(10).join(lines)


def _join_or_all(xs):
    return t_or("cmd.session.all", "(all)") if not xs else ", ".join(xs)


def format_tier(service, counts=None):
    """v1.13 /mem tier: hot/warm/cold breakdown."""
    if service is None:
        return t("cmd.service_down")
    cfg = getattr(service, "cfg", None)
    if counts is None:
        try:
            from hippocampus.tiering import classify, HOT, WARM, COLD
            import time as _t
            now = _t.time()
            c = {HOT: 0, WARM: 0, COLD: 0}
            for e in service.store.all(limit=10_000_000):
                c[classify(e, cfg, now)] += 1
            counts = c
        except Exception as ex:
            return t_or("cmd.tier.err", "tier classify error: {err}").format(err=repr(ex))
    hot = counts.get("hot", 0)
    warm = counts.get("warm", 0)
    cold = counts.get("cold", 0)
    total = hot + warm + cold
    lines = [t_or("cmd.tier.header", "## 记忆分层 (hot/warm/cold)"),
             t_or("cmd.tier.hot", "  热 (hot)： {n}  → 优先召回").format(n=hot),
             t_or("cmd.tier.warm", "  温 (warm)：{n}  → 正常召回").format(n=warm),
             t_or("cmd.tier.cold", "  冷 (cold)：{n}  → 仅兜底召回").format(n=cold),
             t_or("cmd.tier.total", "  总计：    {n}").format(n=total)]
    if "changed" in counts:
        lines.append(t_or("cmd.tier.changed", "  本次重算变更：{n}").format(
            n=counts.get("changed", 0)))
    try:
        from hippocampus.cold_archive import ColdArchiver
        n_arch = ColdArchiver(service.store, cfg).count_archived()
        lines.append(t_or("cmd.tier.archived", "  已归档(冷存文件)：{n}").format(n=n_arch))
    except Exception:
        _obs_note("format", "format_tier")
    return chr(10).join(lines)


def format_tier_archive(res):
    """Render the result of service.archive_cold()."""
    if not isinstance(res, dict):
        return t_or("cmd.archive.invalid", "tier archive: {v}").format(v=str(res))
    if res.get("error"):
        return t_or("cmd.archive.error", "冷层归档失败：{err}").format(err=str(res.get("error")))
    lines = [t_or("cmd.archive.header", "## 冷层归档"),
             t_or("cmd.archive.archived", "  已归档并从库中移除：{n}").format(
                 n=res.get("archived", 0)),
             t_or("cmd.archive.skipped", "  因太新跳过：{n}").format(
                 n=res.get("skipped_recent", 0)),
             t_or("cmd.archive.path", "  归档文件：{path}").format(path=res.get("path", ""))]
    return chr(10).join(lines)
