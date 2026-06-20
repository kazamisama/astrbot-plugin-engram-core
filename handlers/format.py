from __future__ import annotations
import json, math, time
from typing import TYPE_CHECKING
from astrbot.api.event import AstrMessageEvent
from hippocampus import EXPORT_FORMAT_VERSION, __version__ as HIPPO_VERSION, Cue
if TYPE_CHECKING:
    from hippocampus import MemoryService
def _call(obj, name, default=None):
    """Call a 0-arg getter method if present, else return default."""
    fn = getattr(obj, name, None)
    if callable(fn):
        try:
            v = fn()
            return v if v not in (None, "") else default
        except Exception:
            return default
    return default


def _extract(event: AstrMessageEvent) -> dict:
    """Pull observation fields off a real AstrBot AstrMessageEvent.

    Uses the official getters / attributes (AstrBot
    core/platform/astr_message_event.py):
      - unified_msg_origin            -> session id (platform:type:sid)
      - get_sender_id()               -> actor id (message_obj.sender)
      - get_platform_name()           -> platform
      - get_group_id()                -> channel/group ("" in DM)
      - message_str / get_message_str -> text content
    Each lookup falls back to a sane default so a non-standard event
    (or a unit-test mock) never crashes the hook.
    """
    session_id = (getattr(event, "unified_msg_origin", None)
                  or _call(event, "get_session_id")
                  or getattr(event, "session_id", None)
                  or getattr(event, "message_id", "default"))
    actor_id = (_call(event, "get_sender_id")
                or getattr(getattr(event, "sender", None), "user_id", None)
                or "anonymous")
    platform = (_call(event, "get_platform_name")
                or getattr(getattr(event, "platform_meta", None), "name", None)
                or "unknown")
    channel_id = _call(event, "get_group_id", "") or "default"
    content = (getattr(event, "message_str", None)
               or _call(event, "get_message_str")
               or "")
    return {
        "session_id": session_id,
        "actor_id": actor_id,
        "platform": platform,
        "channel_id": channel_id,
        "content": content,
    }


def banner_text(service):
    if service is None:
        return "[hippocampus] not initialized"
    try:
        n_e = len(service.store.all(limit=10_000_000))
    except Exception:
        n_e = -1
    return ("[hippocampus] loaded: v" + HIPPO_VERSION
              + ", embedding=" + service.current_embedding()
            + ", llm=" + service.current_llm()
            + ", engrams=" + str(n_e)
            + ", embeddings=" + str(service.registry.list_embeddings())
            + ", llms=" + str(service.registry.list_llms())
            + " | type /mem help")


def render_stats(service: MemoryService) -> str:
    if service is None:
        return "Memory service not initialized."
    try:
        n_engram = len(service.store.list_active(limit=10_000_000))
    except Exception as e:
        n_engram = "ERR " + repr(e)
    try:
        n_fts = service.store.fts_count()
    except Exception as e:
        n_fts = "ERR " + repr(e)
    n_entity = 0
    if service.semantic is not None:
        try:
            n_entity = len(service.semantic.all_entities(limit=10_000_000))
        except Exception as e:
            n_entity = "ERR " + repr(e)
    n_pending = len(service.list_prospective("pending"))
    n_fired = len(service.list_prospective("fired"))
    return (
        "## stats\n"
        "engrams:        " + str(n_engram) + "\n"
        "fts indexed:    " + str(n_fts) + "\n"
        "entities:       " + str(n_entity) + "\n"
        "triggers pending: " + str(n_pending) + "\n"
        "triggers fired:   " + str(n_fired) + "\n"
        "embedding:      " + service.current_embedding() + "\n"
        "llm:            " + service.current_llm() + "\n"
        "--- v1.0 ---\n"
        "valence:        " + str(service.store.valence_histogram()) + "\n"
        "stream:         " + str(service.store.stream_breakdown()) + "\n"
        "soft-forgotten: " + str(len([e for e in service.store.all(limit=10_000_000) if e.forgotten_at > 0])) + "\n"
        "--- v1.1 ---\n"
        "profile facts:  " + str(len(service.profile.all_facts()) if service.profile is not None else 0) + "\n"
        "cluster gists:  " + str(len(service.store.list_cluster_summaries(limit=10_000))) + "\n"
        "--- v1.2 ---\n"
        "metamemory:     " + ("on" if service.cfg.metamemory_enabled else "off") + "\n"
        "epi->sem:       " + ("on" if service.cfg.enable_episodic_semantic else "off") + "\n"
        "consolidated facts: " + str(len([e for e in service.store.all(limit=10_000_000) if e.profile_fact_id])) + "\n"
    )

def find_and_forget(service: MemoryService, eid: str) -> str:
    if service is None:
        return "Memory service not initialized."
    eid = (eid or "").strip()
    if not eid:
        return "usage: /mem forget <id>"
    e = service.store.get(eid)
    if e is None:
        all_e = service.store.all(limit=10_000_000)
        matches = [x for x in all_e if x.id.startswith(eid)]
        if len(matches) == 1:
            e = matches[0]
        elif len(matches) > 1:
            preview = ", ".join(m.id[:8] for m in matches[:5])
            return ("ambiguous: " + str(len(matches)) + " matches: " + preview
                    + "; use full id")
        else:
            return "not found: " + eid
    try:
        service.store.delete(e.id)
    except Exception as ex:
        return "ERR: " + repr(ex)
    return "forgot engram " + e.id[:8] + ": " + repr(e.summary[:60])



def export_engrams(service, path):
    """Export all engrams + entities + relations + triggers to JSON. Skips embeddings (rebuild later)."""
    if service is None:
        return "Memory service not initialized."
    if not path:
        return "usage: /mem export <path>"
    try:
        engrams = service.store.all(limit=10_000_000)
        entities = service.semantic.all_entities(limit=10_000_000) if service.semantic else []
        relations = []
        if service.semantic is not None:
            for ent in entities:
                relations.extend(service.semantic.relations_of(ent.id))
        triggers = service.list_prospective(status=None) if service.prospective_store else []
        payload = {
            "version": EXPORT_FORMAT_VERSION,
            "exported_at": time.time(),
            "engrams": [
                {**{k: v for k, v in e.__dict__.items() if k != "embedding"},
                 "_embedding_dim": len(e.embedding)}
                for e in engrams
            ],
            "entities": [e.__dict__ for e in entities],
            "relations": [r.__dict__ for r in relations],
            "triggers": [t.__dict__ for t in triggers],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return ("exported: engrams=" + str(len(engrams))
                + " entities=" + str(len(entities))
                + " relations=" + str(len(relations))
                + " triggers=" + str(len(triggers))
                + " -> " + path)
    except Exception as e:
        return "ERR: " + repr(e)


def import_engrams(service, path):
    """Import from a JSON file produced by export_engrams. Skips embeddings;
    caller should run /mem rebuild after import to regenerate them."""
    if service is None:
        return "Memory service not initialized."
    if not path:
        return "usage: /mem import <path>"
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fmt = str(payload.get("version", ""))
        fmt_warn = ""
        if fmt and fmt != EXPORT_FORMAT_VERSION:
            fmt_warn = (" [warn: export format v" + fmt
                        + " != current v" + EXPORT_FORMAT_VERSION + "]")
        from hippocampus.types import Engram, Entity, Relation, Trigger
        n_e = n_ent = n_rel = n_tr = 0
        for d in payload.get("engrams", []):
            emb_dim = d.pop("_embedding_dim", 64)
            d.pop("embedding", None)
            d["embedding"] = [0.0] * emb_dim
            e = Engram(**{k: v for k, v in d.items() if k in Engram.__dataclass_fields__})
            service.store.upsert(e)
            n_e += 1
        for d in payload.get("entities", []):
            if service.semantic is not None:
                ent = Entity(**{k: v for k, v in d.items() if k in Entity.__dataclass_fields__})
                service.semantic.upsert_entity(ent)
            n_ent += 1
        for d in payload.get("relations", []):
            if service.semantic is not None:
                r = Relation(**{k: v for k, v in d.items() if k in Relation.__dataclass_fields__})
                service.semantic.add_relation(r)
            n_rel += 1
        for d in payload.get("triggers", []):
            if service.prospective_store is not None:
                t = Trigger(**{k: v for k, v in d.items() if k in Trigger.__dataclass_fields__})
                service.prospective_store.add(t)
            n_tr += 1
        return ("imported: engrams=" + str(n_e)
                + " entities=" + str(n_ent)
                + " relations=" + str(n_rel)
                + " triggers=" + str(n_tr)
                + "; run /mem rebuild to regenerate embeddings" + fmt_warn)
    except Exception as e:
        return "ERR: " + repr(e)


def format_cluster(service, eid):
    if service is None:
        return "Memory service not initialized."
    eid = (eid or "").strip()
    if not eid:
        return "usage: /mem cluster <id>"
    e = service.store.get(eid)
    if e is None:
        all_e = service.store.all(limit=10_000_000)
        matches = [x for x in all_e if x.id.startswith(eid)]
        if len(matches) == 1:
            e = matches[0]
        elif len(matches) > 1:
            preview = ", ".join(m.id[:8] for m in matches[:5])
            return ("ambiguous: " + str(len(matches)) + " matches: " + preview
                    + "; use full id")
        else:
            return "not found: " + eid
    from hippocampus.separation import PatternSeparator
    cluster = PatternSeparator.expand_cluster(
        [e], fetch=service.store.get, max_total=20)
    lines = ["## cluster for: " + (e.summary or e.content)[:60]]
    lines.append("root: " + e.id[:8])
    sibs = cluster[1:]
    if not sibs:
        lines.append("(no similar_to links)")
    else:
        lines.append("similar_to: " + str(len(sibs)) + " sibling(s)")
        for sib, _s, origin in sibs:
            tag = "via " + origin[:8] if origin else "(orphan root)"
            lines.append("  - " + sib.id[:8] + ": "
                          + (sib.summary or sib.content)[:60] + "  [" + tag + "]")
    return chr(10).join(lines)

def format_narrative(service, topic, k=8):
    """Chain engrams that share entities/topics with `topic` and present
    them as an autobiographical narrative in temporal order."""
    if service is None:
        return "Memory service not initialized."
    topic = (topic or "").strip()
    if not topic:
        return "usage: /mem narrative <topic>"
    # Find seed engrams via FTS-like topic/entity match
    topic_l = topic.lower()
    seeds = []
    for e in service.store.list_active(limit=10_000):
        if topic_l in (e.content or "").lower():
            seeds.append(e); continue
        if any(topic_l in (t or "").lower() for t in e.topics):
            seeds.append(e); continue
        if any(topic_l in (n or "").lower() for n in e.entities):
            seeds.append(e); continue
    if not seeds:
        return "no engrams found for topic: " + topic
    seeds.sort(key=lambda e: e.created_at)
    head = seeds[:k]
    # Expand to cluster siblings (1-hop) to flesh out the story
    from hippocampus.separation import PatternSeparator
    cluster = PatternSeparator.expand_cluster(head, fetch=service.store.get, max_total=k * 2)
    items = [e for e, _s, _o in cluster]
    items.sort(key=lambda e: e.created_at)
    lines = ["## narrative: " + topic, "seed engrams: " + str(len(seeds)) + ", expanded to: " + str(len(items))]
    import datetime
    for e in items:
        ts = datetime.datetime.fromtimestamp(e.created_at).strftime("%Y-%m-%d %H:%M")
        v_tag = ""
        if e.valence > 0.2: v_tag = " [+]"
        elif e.valence < -0.2: v_tag = " [-]"
        s_tag = "" if not e.stream else " (" + e.stream + ")"
        line = "- " + ts + v_tag + s_tag + "  " + (e.summary or e.content)[:80]
        if e.similar_to:
            line += "  [linked:" + str(len(e.similar_to)) + "]"
        lines.append(line)
    return chr(10).join(lines)

def format_profile(service, actor_id):
    """Render the user self-model (v1.1). If facts are absent, try a build first."""
    if service is None or service.profile is None:
        return "profile layer not initialized (cfg.enable_profile=False)"
    actor_id = (actor_id or "").strip() or "default"
    facts = service.profile_facts(actor_id)
    if not facts:
        # auto-build on first view so /mem profile is useful out of the box
        built = service.build_profile(actor_id)
        facts = service.profile_facts(actor_id)
        if not built and not facts:
            return "(no profile facts yet for " + actor_id + " - need more engrams)"
    return service.profile.render(actor_id)


def format_activation(service, seeds, depth=2, decay=0.55, floor=0.05, k=8):
    """Spread activation from seed entity names / engram ids and show the top-k."""
    if service is None or service.activation is None:
        return "activation layer not initialized (need cfg.enable_semantic=True)"
    seeds = [s for s in (seeds or "").split() if s]
    if not seeds:
        return "usage: /mem activate <seed1> [seed2 ...]"
    acts = service.spread_activation(seeds, depth=int(depth),
                                    decay=float(decay), floor=float(floor))
    if not acts:
        return "no activation for: " + " ".join(seeds)
    header = "## spreading activation from: " + " ".join(seeds) \
        + "  (depth=" + str(depth) + " decay=" + str(decay) \
        + " floor=" + str(floor) + ")"
    lines = [header]
    for ln in service.activation.explain(acts, top_k=k):
        lines.append(ln)
    return chr(10).join(lines)


def format_graph(service, query, k=5):
    if service is None or service.semantic is None:
        return "Memory service or semantic layer not initialized."
    if not query:
        return "usage: /mem graph <entity>"
    result = service.recall_semantic(query, k=k)
    if not result.entities and not result.relations:
        return "no entities/relations found for: " + query
    lines = ["## graph for: " + query]
    if result.entities:
        lines.append("### entities")
        for ent in result.entities:
            lines.append("- " + ent.name + " (" + ent.type
                         + ", mentions=" + str(ent.mention_count) + ")")
    if result.relations:
        lines.append("### relations")
        ent_by_id = {e.id: e for e in result.entities}
        for ent in service.semantic.all_entities(limit=1000):
            ent_by_id.setdefault(ent.id, ent)
        for r in result.relations:
            sub = ent_by_id.get(r.subject_id)
            obj = ent_by_id.get(r.object_id)
            sub_name = sub.name if sub else r.subject_id[:8]
            obj_name = obj.name if obj else r.object_id[:8]
            lines.append("- " + sub_name + " --[" + r.predicate + "]--> " + obj_name
                         + " (conf=" + str(round(r.confidence, 2)) + ")")
    if result.engrams:
        lines.append("### source engrams")
        for e in result.engrams:
            lines.append("- " + e.id[:8] + ": " + e.summary[:60])
    return chr(10).join(lines)


def parse_search_args(arg):
    """Parse 'foo bar --mode=fts' -> ('foo bar', 'fts')."""
    arg = (arg or "").strip()
    mode = "hybrid"
    if " --mode=" in arg:
        arg, mode_part = arg.rsplit(" --mode=", 1)
        arg = arg.strip()
        mode = mode_part.strip().split()[0] if mode_part.strip() else "hybrid"
    if mode not in ("vector", "fts", "hybrid", "dual"):
        mode = "hybrid"
    return arg, mode

def format_confidence(service, query, k=5):
    """v1.2 metamemory: recall + show a feeling-of-knowing per hit."""
    if service is None:
        return "Memory service not initialized."
    if not query:
        return "usage: /mem confidence <query>"
    from hippocampus.metamemory import confidence_label, is_tip_of_tongue
    res = service.recall(Cue(text=query, k=int(k)))
    if not res.engrams:
        return "(nothing recalled for: " + query + ")"
    confs = res.confidences or [0.0] * len(res.engrams)
    lines = ["## metamemory for: " + query]
    for e, sc, cf in zip(res.engrams, res.scores, confs):
        label = confidence_label(cf, service.cfg)
        tot = " [tip-of-tongue]" if is_tip_of_tongue(cf, sc, service.cfg) else ""
        lines.append("- " + ("%.2f" % cf) + " " + label + tot
                     + "  " + (e.summary or e.content)[:70])
    return chr(10).join(lines)


def format_decaycurve(service, arg, k=8):
    """v1.2 forgetting-curve viz: project each engram's strength forward in time
    using the Ebbinghaus decay model and draw an ASCII curve."""
    import math, time
    if service is None:
        return "Memory service not initialized."
    cfg = service.cfg
    arg = (arg or "").strip()
    if arg and arg.lower() != "all":
        e = service.store.get(arg)
        if e is None:
            # allow id prefix
            for cand in service.store.all(limit=10_000):
                if cand.id.startswith(arg):
                    e = cand
                    break
        if e is None:
            return "no engram found for id: " + arg
        targets = [e]
    else:
        targets = sorted(service.store.all(limit=10_000),
                         key=lambda x: x.strength, reverse=True)[:k]
    if not targets:
        return "(no engrams to plot)"
    buckets = max(2, int(cfg.decaycurve_buckets))
    width = max(8, int(cfg.decaycurve_width))
    now = time.time()
    horizon = cfg.decay_tau_base * 3.0  # ~3 base time-constants ahead
    lines = ["## forgetting curve (next " + ("%.1f" % (horizon / 86400.0)) + " days)"]
    for e in targets:
        tau = cfg.decay_tau_base * (1.0 + 4.0 * e.importance)
        last = max(e.last_accessed or 0.0, e.created_at)
        head = "- " + e.id[:8] + " imp=" + ("%.2f" % e.importance) \
               + " s0=" + ("%.2f" % e.strength) + ": " + (e.summary or e.content)[:36]
        lines.append(head)
        row_chars = []
        for i in range(buckets):
            dt_future = (now - last) + horizon * (i / (buckets - 1))
            s = e.strength * math.exp(-dt_future / tau)
            filled = int(round(max(0.0, min(1.0, s)) * width))
            row_chars.append("  " + "#" * filled + "." * (width - filled)
                             + " " + ("%.2f" % s))
        # show first / middle / last sample to stay compact
        idxs = sorted(set([0, buckets // 2, buckets - 1]))
        for i in idxs:
            day = (horizon * (i / (buckets - 1))) / 86400.0
            lines.append("    t+" + ("%.1f" % day) + "d" + row_chars[i])
    return chr(10).join(lines)



def format_stats(service):
    return render_stats(service)

def format_dual_route(service, query, k=5):
    """v1.3 dual-route result renderer.

    Shows hits from both document route (vector+FTS5) and graph route
    (entity match + 1-hop relations), merged by RRF. Each hit is tagged
    with which route(s) contributed and the rrf score breakdown.
    """
    if service is None:
        return "Memory service not initialized."
    query = (query or "").strip()
    if not query:
        return "usage: /mem search <q> --mode=dual"
    cue = Cue(text=query, k=int(k), actor_id=None, channel_id=None)
    from hippocampus.retrieval import DualRouteRetriever, DualRouteConfig, RouteKind
    retriever = DualRouteRetriever(service, DualRouteConfig())
    res = retriever.search(cue)
    if not res.engrams:
        return "[dual] no hit for: " + query
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
    lines = ["[dual] hits for: " + query + "  (doc=document-route graph=entity-route)"]
    for e, s in zip(res.engrams, res.scores):
        info = by_id.get(e.id, {})
        tag = ""
        if info.get("doc") and info.get("graph"):
            tag = "[doc+graph"
            if info.get("entity"):
                tag += " via " + info["entity"]
            tag += "]"
        elif info.get("graph"):
            tag = "[graph"
            if info.get("entity"):
                tag += " via " + info["entity"]
            tag += "]"
        else:
            tag = "[doc]"
        summ = (e.summary or e.content or "")[:60]
        lines.append("- " + ("%.3f" % s) + " " + tag + "  " + summ)
    return chr(10).join(lines)

def format_session(service):
    """v1.4 /mem session: render the current session filter policy.

    Shows whether the filter is enabled and which rules are active.
    Tests against a few example contexts to demonstrate pass/deny.
    """
    if service is None:
        return "Memory service not initialized."
    from hippocampus.session_filter import SessionFilter, FilterContext
    sf = SessionFilter(service.cfg)
    s = sf.summary()
    lines = ["## session filter"]
    lines.append("enabled:        " + str(s["enabled"]))
    lines.append("platform allow:  " + _join_or_all(s["platform_allowlist"]))
    lines.append("platform block:  " + _join_or_all(s["platform_blocklist"]))
    lines.append("channel  allow:  " + _join_or_all(s["channel_allowlist"]))
    lines.append("channel  block:  " + _join_or_all(s["channel_blocklist"]))
    lines.append("actor    allow:  " + _join_or_all(s["actor_allowlist"]))
    lines.append("keywords block:  " + _join_or_all(s["blocked_keywords"]))
    if s["enabled"]:
        lines.append("")
        lines.append("### quick test")
        for plat, chan, actor, content, label in (
            ("qq", "group-1", "alice", "hello world", "default priv"),
            ("qq", "group-1", "alice", "this is spam content", "default + spam"),
        ):
            d = sf.decide(FilterContext(platform=plat, channel_id=chan, actor_id=actor, content=content))
            tag = "PASS" if d.is_pass() else "DENY(" + d.reason + ")"
            lines.append("  [" + label + "] " + tag)
    return chr(10).join(lines)


def _join_or_all(xs):
    return "(all)" if not xs else ", ".join(xs)


def format_tier(service, counts=None):
    """v1.13 /mem tier: hot/warm/cold breakdown.

    When `counts` (from reclassify_tiers) is given, show the persisted
    result; otherwise classify live over all engrams for a fresh view.
    """
    if service is None:
        return "Memory service not initialized."
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
            return "tier classify error: " + repr(ex)
    hot = counts.get("hot", 0)
    warm = counts.get("warm", 0)
    cold = counts.get("cold", 0)
    total = hot + warm + cold
    lines = ["## 记忆分层 (hot/warm/cold)",
             "  热 (hot)： " + str(hot) + "  → 优先召回",
             "  温 (warm)：" + str(warm) + "  → 正常召回",
             "  冷 (cold)：" + str(cold) + "  → 仅兑底召回",
             "  总计：    " + str(total)]
    if "changed" in counts:
        lines.append("  本次重算变更：" + str(counts.get("changed", 0)))
    try:
        from hippocampus.cold_archive import ColdArchiver
        n_arch = ColdArchiver(service.store, cfg).count_archived()
        lines.append("  已归档(冷存文件)：" + str(n_arch))
    except Exception:
        pass
    return chr(10).join(lines)


def format_tier_archive(res):
    """Render the result of service.archive_cold()."""
    if not isinstance(res, dict):
        return "tier archive: " + str(res)
    if res.get("error"):
        return "冷层归档失败：" + str(res.get("error"))
    lines = ["## 冷层归档",
             "  已归档并从库中移除：" + str(res.get("archived", 0)),
             "  因太新跳过：" + str(res.get("skipped_recent", 0)),
             "  归档文件：" + str(res.get("path", ""))]
    return chr(10).join(lines)

