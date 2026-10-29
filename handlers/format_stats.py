"""Stats rendering (/mem stats) -- split from handlers/format.py (v1.78.1)."""
from __future__ import annotations

from hippocampus.i18n_backend import t, t_or


def render_stats(service: MemoryService) -> str:
    if service is None:
        return t("cmd.service_down")
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
    on = t_or("common.on", "on")
    off = t_or("common.off", "off")
    return t_or("cmd.stats.body", DEFAULT_STATS).format(
        engrams=n_engram,
        fts=n_fts,
        entities=n_entity,
        pending=n_pending,
        fired=n_fired,
        embedding=service.current_embedding(),
        llm=service.current_llm(),
        valence=service.store.valence_histogram(),
        stream=service.store.stream_breakdown(),
        forgotten=len([e for e in service.store.all(limit=10_000_000) if e.forgotten_at > 0]),
        profile=(len(service.profile.all_facts()) if service.profile is not None else 0),
        metamemory=(on if service.cfg.metamemory_enabled else off),
    )


DEFAULT_STATS = (
    "## stats\n"
    "engrams:        {engrams}\n"
    "fts indexed:    {fts}\n"
    "entities:       {entities}\n"
    "triggers pending: {pending}\n"
    "triggers fired:   {fired}\n"
    "embedding:      {embedding}\n"
    "llm:            {llm}\n"
    "--- v1.0 ---\n"
    "valence:        {valence}\n"
    "stream:         {stream}\n"
    "soft-forgotten: {forgotten}\n"
    "--- v1.1 ---\n"
    "profile facts:  {profile}\n"
    "--- v1.2 ---\n"
    "metamemory:     {metamemory}\n"
)


def format_stats(service):
    return render_stats(service)
