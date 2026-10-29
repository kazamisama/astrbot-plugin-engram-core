"""Recall-side renderers (narrative/profile/activation/graph/confidence/curve).

Split from handlers/format.py at v1.78.1.
"""
from __future__ import annotations

import math, time

from hippocampus import Cue
from hippocampus.i18n_backend import t, t_or


def format_narrative(service, topic, k=8, *, persona_id=None, scope_id=None):
    """Chain engrams that share entities/topics with topic and present
    them as an autobiographical narrative in temporal order."""
    if service is None:
        return t("cmd.service_down")
    topic = (topic or "").strip()
    if not topic:
        return t_or("cmd.narrative.usage", "usage: /mem narrative <topic>")
    topic_l = topic.lower()
    allowed = service.store.engram_ids_for_persona(persona_id) if persona_id is not None else None
    if scope_id is not None:
        scoped = service.store.engram_ids_for_scope(scope_id)
        allowed = scoped if allowed is None else (allowed & scoped)
    seeds = []
    for e in service.store.list_active(limit=10_000):
        if allowed is not None and e.id not in allowed:
            continue
        if topic_l in (e.content or "").lower():
            seeds.append(e); continue
        if any(topic_l in (tp or "").lower() for tp in e.topics):
            seeds.append(e); continue
        if any(topic_l in (nm or "").lower() for nm in e.entities):
            seeds.append(e); continue
    if not seeds:
        return t_or("cmd.narrative.none", "no engrams found for topic: {topic}").format(topic=topic)
    seeds.sort(key=lambda e: e.created_at)
    items = seeds[:k]
    items.sort(key=lambda e: e.created_at)
    lines = [t_or("cmd.narrative.header", "## narrative: {topic}").format(topic=topic),
             t_or("cmd.narrative.seeds", "seed engrams: {n}, expanded to: {m}").format(
                 n=len(seeds), m=len(items))]
    import datetime
    for e in items:
        ts = datetime.datetime.fromtimestamp(e.created_at).strftime("%Y-%m-%d %H:%M")
        v_tag = ""
        if e.valence > 0.2: v_tag = " [+]"
        elif e.valence < -0.2: v_tag = " [-]"
        s_tag = "" if not e.stream else " (" + e.stream + ")"
        line = "- " + ts + v_tag + s_tag + "  " + (e.summary or e.content)[:80]
        lines.append(line)
    return chr(10).join(lines)


def format_profile(service, actor_id):
    """Render the user self-model (v1.1). If facts are absent, try a build first."""
    if service is None or service.profile is None:
        return t_or("cmd.profile.disabled",
                    "profile layer not initialized (cfg.enable_profile=False)")
    actor_id = (actor_id or "").strip() or "default"
    facts = service.profile_facts(actor_id)
    if not facts:
        built = service.build_profile(actor_id)
        facts = service.profile_facts(actor_id)
        if not built and not facts:
            return t_or("cmd.profile.empty",
                        "(no profile facts yet for {actor} - need more engrams)").format(actor=actor_id)
    return service.profile.render(actor_id)


def format_activation(service, seeds, depth=2, decay=0.55, floor=0.05, k=8, *,
                      persona_id=None, scope_id=None):
    """Spread activation from seed entity names / engram ids and show the top-k."""
    if service is None or service.activation is None:
        return t_or("cmd.activation.disabled",
                    "activation layer not initialized (need cfg.enable_semantic=True)")
    seeds = [s for s in (seeds or "").split() if s]
    if not seeds:
        return t_or("cmd.activation.usage", "usage: /mem activate <seed1> [seed2 ...]")
    acts = service.spread_activation(seeds, depth=int(depth),
                                    decay=float(decay), floor=float(floor))
    if persona_id is not None and acts:
        allowed = service.store.engram_ids_for_persona(persona_id)
        acts = {eid: val for eid, val in acts.items() if eid in allowed}
    if scope_id is not None and acts:
        scoped = service.store.engram_ids_for_scope(scope_id)
        acts = {eid: val for eid, val in acts.items() if eid in scoped}
    if not acts:
        return t_or("cmd.activation.none", "no activation for: {seeds}").format(
            seeds=" ".join(seeds))
    header = t_or("cmd.activation.header",
                  "## spreading activation from: {seeds}"
                  "  (depth={depth} decay={decay} floor={floor})").format(
        seeds=" ".join(seeds), depth=depth, decay=decay, floor=floor)
    lines = [header]
    for ln in service.activation.explain(acts, top_k=k):
        lines.append(ln)
    return chr(10).join(lines)


def format_graph(service, query, k=5, *, persona_id=None, scope_id=None):
    if service is None or service.semantic is None:
        return t_or("cmd.graph.disabled",
                    "Memory service or semantic layer not initialized.")
    if not query:
        return t_or("cmd.graph.usage", "usage: /mem graph <entity>")
    result = service.recall_semantic(query, k=k, persona_id=persona_id,
                                    scope_id=scope_id)
    if not result.entities and not result.relations:
        return t_or("cmd.graph.none", "no entities/relations found for: {q}").format(q=query)
    lines = [t_or("cmd.graph.header", "## graph for: {q}").format(q=query)]
    if result.entities:
        lines.append(t_or("cmd.graph.entities", "### entities"))
        for ent in result.entities:
            lines.append(t_or("cmd.graph.entity_line", "- {name} ({type}, mentions={n})").format(
                name=ent.name, type=ent.type, n=ent.mention_count))
    if result.relations:
        lines.append(t_or("cmd.graph.relations", "### relations"))
        ent_by_id = {e.id: e for e in result.entities}
        for ent in service.semantic.all_entities(limit=1000):
            ent_by_id.setdefault(ent.id, ent)
        for r in result.relations:
            sub = ent_by_id.get(r.subject_id)
            obj = ent_by_id.get(r.object_id)
            sub_name = sub.name if sub else r.subject_id[:8]
            obj_name = obj.name if obj else r.object_id[:8]
            lines.append(t_or("cmd.graph.relation_line",
                              "- {sub} --[{pred}]--> {obj} (conf={conf})").format(
                sub=sub_name, pred=r.predicate, obj=obj_name,
                conf=round(r.confidence, 2)))
    if result.engrams:
        lines.append(t_or("cmd.graph.engrams", "### source engrams"))
        for e in result.engrams:
            lines.append(t_or("cmd.graph.engram_line", "- {id8}: {summary}").format(
                id8=e.id[:8], summary=e.summary[:60]))
    return chr(10).join(lines)


def format_confidence(service, query, k=5, *, persona_id=None, scope_id=None):
    """v1.2 metamemory: recall + show a feeling-of-knowing per hit."""
    if service is None:
        return t("cmd.service_down")
    if not query:
        return t_or("cmd.confidence.usage", "usage: /mem confidence <query>")
    from hippocampus.metamemory import confidence_label, is_tip_of_tongue
    res = service.recall(Cue(text=query, k=int(k), persona_id=persona_id, scope_id=scope_id))
    if not res.engrams:
        return t_or("cmd.confidence.none", "(nothing recalled for: {q})").format(q=query)
    confs = res.confidences or [0.0] * len(res.engrams)
    lines = [t_or("cmd.confidence.header", "## metamemory for: {q}").format(q=query)]
    tip = t_or("cmd.confidence.tip", "tip-of-tongue")
    for e, sc, cf in zip(res.engrams, res.scores, confs):
        label = confidence_label(cf, service.cfg)
        tot = " [" + tip + "]" if is_tip_of_tongue(cf, sc, service.cfg) else ""
        lines.append("- " + ("%.2f" % cf) + " " + label + tot
                     + "  " + (e.summary or e.content)[:70])
    return chr(10).join(lines)


def format_decaycurve(service, arg, k=8):
    """v1.2 forgetting-curve viz: project each engram strength forward in time
    using the Ebbinghaus decay model and draw an ASCII curve."""
    import math, time
    if service is None:
        return t("cmd.service_down")
    cfg = service.cfg
    arg = (arg or "").strip()
    if arg and arg.lower() != "all":
        e = service.store.get(arg)
        if e is None:
            for cand in service.store.all(limit=10_000):
                if cand.id.startswith(arg):
                    e = cand
                    break
        if e is None:
            return t_or("cmd.decay.not_found", "no engram found for id: {id}").format(id=arg)
        targets = [e]
    else:
        targets = sorted(service.store.all(limit=10_000),
                         key=lambda x: x.strength, reverse=True)[:k]
    if not targets:
        return t_or("cmd.decay.empty", "(no engrams to plot)")
    buckets = max(2, int(cfg.decaycurve_buckets))
    width = max(8, int(cfg.decaycurve_width))
    now = time.time()
    horizon = cfg.decay_tau_base * 3.0
    lines = [t_or("cmd.decay.header", "## forgetting curve (next {days} days)").format(
        days="%.1f" % (horizon / 86400.0))]
    for e in targets:
        tau = cfg.decay_tau_base * (1.0 + 4.0 * e.importance)
        last = max(e.last_accessed or 0.0, e.created_at)
        head = "- " + e.id[:8] + " imp=" + ("%.2f" % e.importance)
        head += " s0=" + ("%.2f" % e.strength) + ": " + (e.summary or e.content)[:36]
        lines.append(head)
        row_chars = []
        for i in range(buckets):
            dt_future = (now - last) + horizon * (i / (buckets - 1))
            s = e.strength * math.exp(-dt_future / tau)
            filled = int(round(max(0.0, min(1.0, s)) * width))
            row_chars.append("  " + "#" * filled + "." * (width - filled)
                             + " " + ("%.2f" % s))
        idxs = sorted(set([0, buckets // 2, buckets - 1]))
        for i in idxs:
            day = (horizon * (i / (buckets - 1))) / 86400.0
            lines.append("    t+" + ("%.1f" % day) + "d" + row_chars[i])
    return chr(10).join(lines)
