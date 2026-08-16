"""Portable memory import/export helpers (v1.76.6)."""
from __future__ import annotations
import csv
import io
import json
import time
from typing import Any

TRANSFER_FORMAT = "engram"
TRANSFER_SCHEMA_VERSION = 1
MAX_IMPORT_ENTRIES = 10_000
MAX_EXPORT_ENTRIES = 20_000

_CONTENT_KEYS = ("content", "text", "summary", "memory", "value")


def export_memory_payload(service) -> dict[str, Any]:
    # SQL-side count + bounded page; do not materialize the whole store.
    total = service.store.count_all()
    truncated = total > MAX_EXPORT_ENTRIES
    engrams = []
    for e in service.store.export_rows(MAX_EXPORT_ENTRIES, offset=0):
        engrams.append({
            "id": e.id,
            "content": e.content,
            "summary": e.summary,
            "actor_id": e.actor_id,
            "session_id": e.session_id,
            "platform": e.platform,
            "channel_id": e.channel_id,
            "persona_id": e.persona_id,
            "scope_id": e.scope_id,
            "memory_type": e.memory_type,
            "importance": e.importance,
            "confidence": e.confidence,
            "topics": e.topics,
            "tags": e.tags,
            "created_at": e.created_at,
            "forgotten_at": e.forgotten_at,
        })
    return {
        "format": TRANSFER_FORMAT,
        "schema_version": TRANSFER_SCHEMA_VERSION,
        "exported_at": time.time(),
        "memory_count": len(engrams),
        "truncated": truncated,
        "total_available": total,
        "memories": engrams,
    }


def export_memory_json(service) -> str:
    return json.dumps(export_memory_payload(service), ensure_ascii=False, indent=2, default=str)


def export_memory_csv(service) -> str:
    out = io.StringIO(newline="")
    fields = ["id", "summary", "content", "actor_id", "session_id", "platform",
              "channel_id", "persona_id", "scope_id", "memory_type",
              "importance", "topics", "tags", "created_at", "forgotten_at"]
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for item in export_memory_payload(service)["memories"]:
        row = {k: item.get(k, "") for k in fields}
        for k in ("topics", "tags"):
            row[k] = json.dumps(row[k] or [], ensure_ascii=False)
        writer.writerow(row)
    return out.getvalue()


def _normalize_entry(raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"content": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"entry {index}: expected object")
    content = next((str(raw.get(k) or "") for k in _CONTENT_KEYS if raw.get(k)), "")
    if not content.strip():
        raise ValueError(f"entry {index}: empty content")
    importance = 0.5
    try:
        importance = max(0.0, min(1.0, float(raw.get("importance", 0.5) or 0.5)))
    except Exception:
        pass
    topics = raw.get("topics") or []
    if isinstance(topics, str):
        try:
            topics = json.loads(topics)
        except Exception:
            topics = [x.strip() for x in topics.replace("，", ",").split(",") if x.strip()]
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [x.strip() for x in tags.split(",") if x.strip()]
    forgotten = 0.0
    try:
        forgotten = float(raw.get("forgotten_at", 0.0) or 0.0)
    except Exception:
        forgotten = 0.0
    confidence = 0.5
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5) or 0.5)))
    except Exception:
        confidence = 0.5
    created_at = 0.0
    try:
        created_at = float(raw.get("created_at", 0.0) or 0.0)
    except Exception:
        created_at = 0.0
    return {
        "id": str(raw.get("id", "") or ""),
        "content": content.strip(),
        "summary": str(raw.get("summary") or content).strip(),
        "forgotten_at": forgotten,
        "confidence": confidence,
        "created_at": created_at,
        "importance": importance,
        "session_id": str(raw.get("session_id") or ""),
        "actor_id": str(raw.get("actor_id") or raw.get("sender_id") or ""),
        "platform": str(raw.get("platform") or "external"),
        "channel_id": str(raw.get("channel_id") or raw.get("session_id") or "import"),
        "persona_id": str(raw.get("persona_id") or ""),
        "scope_id": str(raw.get("scope_id") or ""),
        "memory_type": str(raw.get("memory_type") or "episodic"),
        "topics": topics if isinstance(topics, list) else [],
        "tags": tags if isinstance(tags, list) else [],
    }


def _extract_raw_entries(payload: Any, fmt: str) -> list[Any]:
    fmt = (fmt or "json").lower()
    if fmt == "csv":
        return [dict(row) for row in csv.DictReader(io.StringIO(str(payload).lstrip("\ufeff")))]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        for key in ("memories", "long_term_memories", "data"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        return [payload]
    if isinstance(payload, list):
        return payload
    raise ValueError("unsupported payload")


def _content_key(e: dict) -> tuple:
    return (" ".join(str(e.get("content", "")).split()),
            str(e.get("session_id", "") or ""),
            str(e.get("persona_id", "") or ""))


def _existing_fingerprints(service) -> tuple[set, set]:
    """Return (content-keys, ids) currently active in the store."""
    keys: set = set()
    ids: set = set()
    if service is None:
        return keys, ids
    for content, sid, pid, eid in service.store.import_fingerprints():
        keys.add((" ".join(str(content or "").split()), str(sid), str(pid)))
        ids.add(str(eid))
    return keys, ids


def preview_import(content: str, fmt: str = "json",
                   service=None) -> dict[str, Any]:
    raw_entries = _extract_raw_entries(content, fmt)
    if len(raw_entries) > MAX_IMPORT_ENTRIES:
        raise ValueError(f"too many entries: {len(raw_entries)} > {MAX_IMPORT_ENTRIES}")
    entries = []
    errors = []
    for idx, raw in enumerate(raw_entries):
        try:
            entries.append(_normalize_entry(raw, idx))
        except ValueError as exc:
            errors.append({"index": idx, "error": str(exc)})
    existing_keys, existing_ids = _existing_fingerprints(service)
    seen = set(existing_keys)
    seen_ids = set(existing_ids)
    duplicates = 0
    for e in entries:
        if e.get("id") and e["id"] in seen_ids:
            duplicates += 1
            continue
        key = _content_key(e)
        if key in seen:
            duplicates += 1
        seen.add(key)
        if e.get("id"):
            seen_ids.add(e["id"])
    return {"entries": len(entries), "duplicates": duplicates,
            "existing_checked": len(existing_keys), "errors": errors}


def import_memories(service, content: str, fmt: str = "json",
                    allow_duplicates: bool = False) -> dict[str, Any]:
    preview = preview_import(content, fmt, service=service)
    if preview["errors"]:
        return {"imported": 0, "skipped": 0, "errors": preview["errors"]}
    raw_entries = _extract_raw_entries(content, fmt)
    entries = [_normalize_entry(r, i) for i, r in enumerate(raw_entries)]
    existing_keys, existing_ids = _existing_fingerprints(service)
    seen = set(existing_keys)
    seen_ids = set(existing_ids)
    imported = 0
    skipped = 0
    embedded = 0
    for ent in entries:
        if not allow_duplicates:
            if ent.get("id") and ent["id"] in seen_ids:
                skipped += 1
                continue
            key = _content_key(ent)
            if key in seen:
                skipped += 1
                continue
        try:
            if ent.get("id"):
                # Native engram round-trip: preserve id / status / tags.
                from .types import Engram
                e = Engram(
                    id=ent["id"],
                    content=ent["content"],
                    summary=ent["summary"],
                    actor_id=ent["actor_id"],
                    session_id=ent["session_id"],
                    platform=ent["platform"],
                    channel_id=ent["channel_id"],
                    persona_id=ent["persona_id"],
                    scope_id=ent["scope_id"],
                    memory_type=ent["memory_type"],
                    importance=ent["importance"],
                    confidence=ent["confidence"],
                    topics=list(ent["topics"]),
                    tags=list(ent["tags"]),
                    created_at=ent["created_at"] or time.time(),
                    forgotten_at=ent["forgotten_at"],
                    strength=0.0 if ent["forgotten_at"] > 0 else 1.0,
                    embedding=[],
                    embedding_model="",
                )
                # Rebuild the current-embedder vector and derived indexes for
                # imported active memories; archived rows stay audit-only.
                if ent["forgotten_at"] <= 0:
                    try:
                        e.embedding = service.embedder.embed(e.content or "")
                        e.embedding_model = service._current_embedding_name
                        embedded += 1
                    except Exception:
                        e.embedding = []
                    service.store.upsert(e)
                    try:
                        service._post_ingest(e)
                    except Exception as pex:
                        print("[hippocampus] import _post_ingest failed: " + repr(pex))
                else:
                    service.store.upsert(e)
            else:
                service.store_summary(
                    {"summary": ent["summary"], "key_facts": [], "topics": ent["topics"],
                     "participants": [], "relations": [], "importance": ent["importance"]},
                    {"session_id": ent["session_id"], "actor_id": ent["actor_id"],
                     "platform": ent["platform"], "channel_id": ent["channel_id"],
                     "persona_id": ent["persona_id"], "scope_id": ent["scope_id"],
                     "memory_type": ent["memory_type"]})
            imported += 1
            seen.add(_content_key(ent))
            if ent.get("id"):
                seen_ids.add(ent["id"])
        except Exception as exc:
            skipped += 1
            preview.setdefault("errors", []).append({
                "entry": ent["content"][:80], "error": repr(exc)})
    service._invalidate_search_cache()
    return {"imported": imported, "skipped": skipped,
            "embedded": embedded,
            "duplicates": preview["duplicates"],
            "existing_checked": len(existing_keys),
            "errors": preview.get("errors", [])}
