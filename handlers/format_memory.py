"""Forget / export / import renderers -- split from handlers/format.py (v1.78.1)."""
from __future__ import annotations

import json, time

from hippocampus import EXPORT_FORMAT_VERSION
from hippocampus.i18n_backend import t, t_or
from hippocampus.observability import note as _obs_note


def find_and_forget(service: MemoryService, eid: str) -> str:
    if service is None:
        return t("cmd.service_down")
    eid = (eid or "").strip()
    if not eid:
        return t("forget.usage")
    e = service.store.get(eid)
    if e is None:
        all_e = service.store.all(limit=10_000_000)
        matches = [x for x in all_e if x.id.startswith(eid)]
        if len(matches) == 1:
            e = matches[0]
        elif len(matches) > 1:
            preview = ", ".join(m.id[:8] for m in matches[:5])
            return t_or("cmd.ambiguous", DEFAULT_AMBIGUOUS).format(
                n=len(matches), preview=preview)
        else:
            return t_or("cmd.not_found", DEFAULT_NOT_FOUND).format(id=eid)
    try:
        service.store.delete(e.id)
        # v1.76.4: drop any cached recall result containing this id.
        try:
            service._invalidate_search_cache()
        except Exception:
            _obs_note("format", "find_and_forget")
    except Exception as ex:
        return t_or("cmd.err", DEFAULT_ERR).format(err=repr(ex))
    return t_or("cmd.forget.ok", DEFAULT_FORGET_OK).format(
        id8=e.id[:8], summary=repr((e.summary or "")[:60]))


DEFAULT_AMBIGUOUS = "ambiguous: {n} matches: {preview}; use full id"
DEFAULT_NOT_FOUND = "not found: {id}"
DEFAULT_ERR = "ERR: {err}"
DEFAULT_FORGET_OK = "forgot engram {id8}: {summary}"


def export_engrams(service, path):
    """Export all engrams + entities + relations + triggers to JSON.
    Embeddings are skipped (rebuild later)."""
    if service is None:
        return t("cmd.service_down")
    if not path:
        return t_or("cmd.export.usage", "usage: /mem export <path>")
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
        return t_or("cmd.export.done", DEFAULT_EXPORT_DONE).format(
            engrams=len(engrams), entities=len(entities),
            relations=len(relations), triggers=len(triggers), path=path)
    except Exception as e:
        return t_or("cmd.err", DEFAULT_ERR).format(err=repr(e))


DEFAULT_EXPORT_DONE = ("exported: engrams={engrams} entities={entities} "
                       "relations={relations} triggers={triggers} -> {path}")


def import_engrams(service, path):
    """Import from a JSON file produced by export_engrams. Skips embeddings;
    caller should run /mem rebuild after import to regenerate them."""
    if service is None:
        return t("cmd.service_down")
    if not path:
        return t_or("cmd.import.usage", "usage: /mem import <path>")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fmt = str(payload.get("version", ""))
        fmt_warn = ""
        if fmt and fmt != EXPORT_FORMAT_VERSION:
            fmt_warn = t_or("cmd.import.fmt_warn", DEFAULT_FMT_WARN).format(
                fmt=fmt, cur=EXPORT_FORMAT_VERSION)
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
                trig = Trigger(**{k: v for k, v in d.items() if k in Trigger.__dataclass_fields__})
                service.prospective_store.add(trig)
            n_tr += 1
        return t_or("cmd.import.done", DEFAULT_IMPORT_DONE).format(
            engrams=n_e, entities=n_ent, relations=n_rel,
            triggers=n_tr, fmt_warn=fmt_warn)
    except Exception as e:
        return t_or("cmd.err", DEFAULT_ERR).format(err=repr(e))


DEFAULT_IMPORT_DONE = ("imported: engrams={engrams} entities={entities} "
                       "relations={relations} triggers={triggers}"
                       "; run /mem rebuild to regenerate embeddings{fmt_warn}")
DEFAULT_FMT_WARN = " [warn: export format v{fmt} != current v{cur}]"
