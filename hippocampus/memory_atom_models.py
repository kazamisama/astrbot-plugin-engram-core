# v1.4 B3: factory helpers for MemoryAtom.
# Kept in a single-file layout (NOT hippocampus/models/) to avoid colliding
# with the legacy storage.py as a Python module name.
from __future__ import annotations

from typing import Iterable

from .types import (
    MemoryAtom,
    AtomStatus,
    AtomType,
    DecayType,
)


def _norm(s: str) -> str:
    """Lowercase + strip. The atom triple key is canonical, so factory
    output and the UNIQUE constraint agree regardless of caller input."""
    return (s or "").strip().lower()


def triple_key(subject: str, predicate: str, obj: str) -> tuple[str, str, str]:
    """Canonical key for a logical triple. Used by AtomStore.upsert to
    decide whether to INSERT a new row or MERGE into an existing one."""
    return (subject.strip().lower(), predicate.strip().lower(), obj.strip().lower())


def make_fact_atom(
    subject: str,
    predicate: str,
    obj: str,
    *,
    source_engram_id: str = "",
    confidence: float = 0.7,
    actor_id: str = "",
    platform: str = "",
    channel_id: str = "",
    importance: float = 0.5,
    decay_type: str = DecayType.SEMANTIC.value,
    attributes: dict | None = None,
) -> MemoryAtom:
    """A bare fact: "Alice likes Americano"."""
    atom = MemoryAtom(
        kind=AtomType.FACT.value,
        subject=_norm(subject),
        predicate=_norm(predicate),
        object=_norm(obj),
        confidence=confidence,
        actor_id=actor_id,
        platform=platform,
        channel_id=channel_id,
        importance=importance,
        decay_type=decay_type,
        attributes=dict(attributes) if attributes else {},
    )
    if source_engram_id:
        atom.source_engram_ids.append(source_engram_id)
    return atom


def make_preference_atom(
    subject: str,
    predicate: str,
    obj: str,
    *,
    source_engram_id: str = "",
    confidence: float = 0.8,
    actor_id: str = "",
    platform: str = "",
    channel_id: str = "",
    importance: float = 0.7,
    attributes: dict | None = None,
) -> MemoryAtom:
    """A user preference: "user prefers dark mode". Decays slower (preference)."""
    atom = MemoryAtom(
        kind=AtomType.PREFERENCE.value,
        subject=_norm(subject),
        predicate=_norm(predicate),
        object=_norm(obj),
        confidence=confidence,
        actor_id=actor_id,
        platform=platform,
        channel_id=channel_id,
        importance=importance,
        decay_type=DecayType.PREFERENCE.value,
        attributes=dict(attributes) if attributes else {},
    )
    if source_engram_id:
        atom.source_engram_ids.append(source_engram_id)
    return atom


__all__ = [
    "MemoryAtom",
    "AtomStatus",
    "AtomType",
    "DecayType",
    "triple_key",
    "make_fact_atom",
    "make_preference_atom",
]
