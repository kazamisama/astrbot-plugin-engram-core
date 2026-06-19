from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Literal
import time, uuid, json

MemoryType = Literal["episodic", "semantic", "prospective"]
TriggerStatus = Literal["pending", "fired", "cancelled"]

def _now() -> float: return time.time()
def _new_id() -> str: return uuid.uuid4().hex

@dataclass
class Engram:
    id: str = field(default_factory=_new_id)
    created_at: float = field(default_factory=_now)
    session_id: str = ""
    actor_id: str = ""
    platform: str = ""
    channel_id: str = ""
    content: str = ""
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    entity_refs: list[str] = field(default_factory=list)  # 指向 semantic.entities.id
    importance: float = 0.5
    embedding: list[float] = field(default_factory=list)
    strength: float = 1.0
    access_count: int = 0
    last_accessed: float = 0.0
    reconsolidation_lock_until: float = 0.0
    supersedes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    similar_to: list[str] = field(default_factory=list)
    memory_type: str = "episodic"   # episodic | semantic | prospective
    promoted_at: float = 0.0        # 升级为 semantic 的时间
    embedding_model: str = ""      # 标识用哪个 embedding provider,recall 时按此过滤
    # --- v1.0: biology layer additions ---
    valence: float = 0.0            # 情绪价, [-1, 1]
    intensity: float = 0.0          # 情绪强度, [0, 1]
    temporal_bucket: int = 0        # 离散时间桶 id
    stream: str = ""               # "what" | "where_when" | ""
    forgotten_at: float = 0.0       # 软忘记时间戳
    # --- v1.1: cluster identity + profile binding ---
    cluster_id: str = ""              # 相似链构建的组 id(互相链接的 similar_to 一个 clique)
    profile_fact_id: str = ""         # 如果是表型支撑出来的知识, 链接到 profile_facts.id
    # --- v1.2: metamemory (feeling-of-knowing / confidence) ---
    confidence: float = 0.5           # 元记忆:对这条记忆的主观确定度, [0, 1]

    def to_json(self) -> str: return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Engram":
        d = json.loads(raw)
        return cls(**d)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Engram":
        d = dict(row)
        for k in ("topics", "entities", "entity_refs", "supersedes", "tags", "similar_to", "source_relation_ids", "source_engram_ids"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = json.loads(v) if v else []
        if d.get("embedding_json"):
            d["embedding"] = json.loads(d.pop("embedding_json"))
        else:
            d.pop("embedding", None); d.pop("embedding_json", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class Entity:
    id: str = field(default_factory=_new_id)
    name: str = ""
    type: str = "unknown"           # person / place / object / concept
    aliases: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    mention_count: int = 0
    created_at: float = field(default_factory=_now)
    last_seen: float = field(default_factory=_now)
    source_engram_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Entity":
        d = dict(row)
        for k in ("aliases", "source_engram_ids"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = json.loads(v) if v else []
        for k in ("attributes",):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = json.loads(v) if v else {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class Relation:
    id: str = field(default_factory=_new_id)
    subject_id: str = ""
    predicate: str = ""             # resides_in / likes / dislikes / identity / ...
    object_id: str = ""
    source_engram_id: str = ""
    confidence: float = 0.5
    created_at: float = field(default_factory=_now)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Relation":
        return cls(**row)

@dataclass
class Trigger:
    id: str = field(default_factory=_new_id)
    kind: str = "at_time"           # at_time / after_event / condition
    payload: dict = field(default_factory=dict)
    fire_at: float = 0.0
    status: str = "pending"         # pending / fired / cancelled
    created_engram_id: str = ""
    created_at: float = field(default_factory=_now)
    fired_at: float = 0.0
    actor_id: str = ""
    channel_id: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Trigger":
        d = dict(row)
        v = d.get("payload")
        if isinstance(v, str):
            d["payload"] = json.loads(v) if v else {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class Cue:
    text: str
    actor_id: str | None = None
    channel_id: str | None = None
    time_range: tuple[float, float] | None = None
    topics: list[str] | None = None
    k: int = 5
    memory_types: list[str] | None = None  # 召回过滤:episodic / semantic / prospective
    mode: str = "hybrid"                      # 召回模式:vector | fts | hybrid
    # --- v1.1: mood-congruent + activation rerank ---
    valence_hint: float | None = None  # caller-provided emotional tone in [-1, 1]; None = no bias
    activation: dict | None = None     # {engram_id: activation in [0,1]} from SpreadingActivation
@dataclass
class RecallResult:
    engrams: list[Engram]
    scores: list[float]
    snapshot_id: str | None = None
    # --- v1.2: per-engram recall confidence (metamemory) ---
    confidences: list[float] | None = None

@dataclass
class SemanticRecallResult:
    entities: list[Entity]
    relations: list[Relation]
    engrams: list[Engram]
    scores: list[float]

# --- v1.4 B3: MemoryAtom (sub-Engram grain) --------------------------
import enum as _enum
class AtomStatus(str, _enum.Enum):
    """Lifecycle status of a MemoryAtom."""
    ACTIVE = "active"
    SOFT_FORGOTTEN = "soft_forgotten"
    GC = "gc"

class AtomType(str, _enum.Enum):
    """What kind of fact this atom represents."""
    FACT = "fact"
    PREFERENCE = "preference"
    IDENTITY = "identity"
    EVENT = "event"
    RELATION = "relation"

class DecayType(str, _enum.Enum):
    """Decay curve family. preference decays slower than event."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PREFERENCE = "preference"

@dataclass
class MemoryAtom:
    """v1.4: a single, granular fact extracted from one or more engrams.

    Where Engram captures a whole message, an Atom captures one assertion:
    `(subject, predicate, object) -> value` with a confidence and a list of
    backing engrams. Many Atoms can be born from one Engram; many Engrams
    can back one Atom (via `upsert` MERGE on the logical triple).

    Examples:
        subject=Alice predicate=likes  object=Americano  -> 0.85
        subject=Alice predicate=lives_in object=Shanghai -> 0.7
        subject=user predicate=prefers object=dark_mode   -> 0.9
    """
    id: str = field(default_factory=lambda: "atom:" + _new_id())
    kind: str = AtomType.FACT.value
    subject: str = ""
    predicate: str = ""
    object: str = ""
    confidence: float = 0.5
    evidence_count: int = 1
    source_engram_ids: list[str] = field(default_factory=list)
    actor_id: str = ""
    platform: str = ""
    channel_id: str = ""
    created_at: float = field(default_factory=_now)
    last_seen: float = field(default_factory=_now)
    last_accessed: float = field(default_factory=_now)
    status: str = AtomStatus.ACTIVE.value
    decay_type: str = DecayType.EPISODIC.value
    importance: float = 0.5
    strength: float = 1.0
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryAtom":
        # Filter to known fields so a future schema change is forward-compat.
        keep = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**keep)

    def merge(self, other: "MemoryAtom") -> "MemoryAtom":
        """Combine two atoms of the same logical triple. Returns self with
        merged evidence. Caller should persist via AtomStore.upsert."""
        # One new observation contributes exactly one piece of evidence,
        # regardless of how many backings the other side carried. The store's
        # upsert will then sum existing + 1 on persist.
        self.evidence_count += 1
        self.last_seen = max(self.last_seen, other.last_seen)
        for sid in other.source_engram_ids:
            if sid not in self.source_engram_ids:
                self.source_engram_ids.append(sid)
        # Confidence moves toward the higher-confidence observation
        if other.confidence > self.confidence:
            self.confidence = other.confidence
        # Strength is the running max of the two
        if other.strength > self.strength:
            self.strength = other.strength
        return self

    @property
    def triple(self) -> tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)
