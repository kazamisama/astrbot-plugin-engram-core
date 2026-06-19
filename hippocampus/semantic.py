from __future__ import annotations
import json, sqlite3, threading, re
from .types import Entity, Relation, Engram
from .config import MemoryConfig

class SemanticStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        from .sqlite_util import apply_pragmas
        apply_pragmas(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
              id TEXT PRIMARY KEY,
              name TEXT, type TEXT,
              aliases TEXT, attributes TEXT,
              mention_count INTEGER,
              created_at REAL, last_seen REAL,
              source_engram_ids TEXT
            );
            CREATE TABLE IF NOT EXISTS relations (
              id TEXT PRIMARY KEY,
              subject_id TEXT, predicate TEXT, object_id TEXT,
              source_engram_id TEXT, confidence REAL, created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name);
            CREATE INDEX IF NOT EXISTS idx_rel_subj ON relations(subject_id);
            CREATE INDEX IF NOT EXISTS idx_rel_obj ON relations(object_id);
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_entity(self, e: Entity) -> Entity:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM entities WHERE name=? COLLATE NOCASE LIMIT 1", (e.name,))
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO entities(id,name,type,aliases,attributes,mention_count,created_at,last_seen,source_engram_ids) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (e.id, e.name, e.type,
                     json.dumps(e.aliases, ensure_ascii=False),
                     json.dumps(e.attributes, ensure_ascii=False),
                     e.mention_count, e.created_at, e.last_seen,
                     json.dumps(e.source_engram_ids, ensure_ascii=False)))
                return e
            existing = Entity.from_row(dict(row))
            existing.mention_count += e.mention_count
            existing.last_seen = max(existing.last_seen, e.last_seen)
            for a in e.aliases:
                if a not in existing.aliases: existing.aliases.append(a)
            for k, v in e.attributes.items():
                existing.attributes.setdefault(k, v)
            for sid in e.source_engram_ids:
                if sid not in existing.source_engram_ids:
                    existing.source_engram_ids.append(sid)
            self._conn.execute(
                "UPDATE entities SET mention_count=?, last_seen=?, aliases=?, attributes=?, source_engram_ids=? WHERE id=?",
                (existing.mention_count, existing.last_seen,
                 json.dumps(existing.aliases, ensure_ascii=False),
                 json.dumps(existing.attributes, ensure_ascii=False),
                 json.dumps(existing.source_engram_ids, ensure_ascii=False),
                 existing.id))
            return existing

    def find_entity_by_name(self, name: str) -> Entity | None:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM entities WHERE name=? COLLATE NOCASE LIMIT 1", (name,))
            row = cur.fetchone()
        return Entity.from_row(dict(row)) if row else None

    def search_entities(self, q: str, limit: int = 10) -> list[Entity]:
        like = "%" + q.lower() + "%"
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM entities WHERE LOWER(name) LIKE ? OR LOWER(aliases) LIKE ? "
                "ORDER BY mention_count DESC LIMIT ?",
                (like, like, limit))
            return [Entity.from_row(dict(r)) for r in cur.fetchall()]

    def add_relation(self, r: Relation) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO relations(id,subject_id,predicate,object_id,source_engram_id,confidence,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (r.id, r.subject_id, r.predicate, r.object_id,
                 r.source_engram_id, r.confidence, r.created_at))

    def relations_of(self, entity_id: str) -> list[Relation]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM relations WHERE subject_id=? OR object_id=?",
                (entity_id, entity_id))
            return [Relation.from_row(dict(r)) for r in cur.fetchall()]

    def all_entities(self, limit: int = 1000) -> list[Entity]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT * FROM entities ORDER BY mention_count DESC LIMIT ?", (limit,))
            return [Entity.from_row(dict(r)) for r in cur.fetchall()]

    def get_entity(self, entity_id: str) -> Entity | None:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM entities WHERE id=? LIMIT 1", (entity_id,))
            row = cur.fetchone()
        return Entity.from_row(dict(row)) if row else None

_TYPE_HINTS: list[tuple[str, str]] = [
    ("shanghai", "place"), ("beijing", "place"), ("shenzhen", "place"),
    ("guangzhou", "place"), ("hangzhou", "place"), ("tokyo", "place"),
    ("new york", "place"), ("san francisco", "place"), ("london", "place"),
    ("上海", "place"), ("北京", "place"), ("广州", "place"), ("深圳", "place"),
    ("杭州", "place"), ("成都", "place"), ("武汉", "place"),
    ("mom", "person"), ("dad", "person"), ("father", "person"), ("mother", "person"),
    ("americano", "object"), ("latte", "object"), ("coffee", "object"),
    ("tea", "object"), ("cola", "object"), ("wine", "object"),
    ("cilantro", "object"), ("pizza", "object"), ("pasta", "object"),
    ("美式", "object"), ("拿铁", "object"), ("咖啡", "object"),
    ("香菜", "object"), ("可乐", "object"),
]

def _classify(name: str) -> str:
    ln = name.lower().strip()
    for hint, t in _TYPE_HINTS:
        if hint in ln: return t
    return "unknown"

class EntityExtractor:
    def __init__(self, llm=None) -> None:
        self._llm = llm
        self._identity_by_actor: dict[str, str] = {}
    def set_llm(self, llm) -> None: self._llm = llm
    def extract_entities(self, engram: Engram) -> list[Entity]:
        out: list[Entity] = []
        for name in engram.entities:
            name_clean = (name or "").strip()
            if not name_clean: continue
            out.append(Entity(
                name=name_clean, type=_classify(name_clean),
                source_engram_ids=[engram.id],
                created_at=engram.created_at, last_seen=engram.created_at,
                mention_count=1,
            ))
        return out

    def extract_relations(self, engram: Engram, entities: list[Entity],
                          actor_id: str | None = None, resolve=None) -> list[Relation]:
        out: list[Relation] = []
        text = engram.content.lower()
        e_by_name = {e.name.lower(): e for e in entities}

        def _ensure(name: str, etype: str) -> Entity:
            hit = e_by_name.get(name.lower())
            if hit is not None:
                return hit
            if resolve is not None:
                ent = resolve(name, etype)
                if ent is not None:
                    e_by_name[name.lower()] = ent
                    return ent
            ent = Entity(
                name=name, type=etype, source_engram_ids=[engram.id],
                created_at=engram.created_at, last_seen=engram.created_at)
            e_by_name[name.lower()] = ent
            return ent

        explicit_person = next((e for e in entities if e.type == "person"), None)
        explicit_name = explicit_person.name if explicit_person is not None else None
        if explicit_name is None:
            ident0 = re.search(r"(?:i am|i''m|my name is|i am called|我(?:叫|是))\s*([\w\u4e00-\u9fff]+)", text)
            if ident0:
                explicit_name = ident0.group(1).strip()
        if explicit_name and actor_id:
            self._identity_by_actor[actor_id] = explicit_name

        def _subject() -> Entity | None:
            person = next((e for e in entities if e.type == "person"), None)
            if person: return person
            ident = re.search(r"(?:i am|i''m|my name is|i am called|我(?:叫|是))\s*([\w\u4e00-\u9fff]+)", text)
            if ident:
                name = ident.group(1).strip()
                return _ensure(name, "person")
            if actor_id:
                carried = self._identity_by_actor.get(actor_id)
                if carried:
                    return _ensure(carried, "person")
            return entities[0] if entities else None

        # 居住
        m = re.search(r"(?:i )?live in\s+([\w\u4e00-\u9fff]+)|(?:住在|在)\s*([\u4e00-\u9fff]{2,12})", text)
        if m:
            place_name = (m.group(1) or m.group(2) or "").strip()
            if place_name:
                place = _ensure(place_name, "place")
                sub = _subject()
                if sub is not None and sub.id != place.id:
                    out.append(Relation(
                        subject_id=sub.id, predicate="resides_in", object_id=place.id,
                        source_engram_id=engram.id, confidence=0.8,
                        created_at=engram.created_at))
        # 偏好
        for pattern, pred, conf in [
            (r"(?:i (?:love|like))\s+([\w\u4e00-\u9fff]+)", "likes", 0.8),
            (r"(?:i (?:hate|dislike))\s+([\w\u4e00-\u9fff]+)", "dislikes", 0.8),
            (r"我喜欢\s*([\u4e00-\u9fff]{2,12})", "likes", 0.8),
            (r"我讨厌\s*([\u4e00-\u9fff]{2,12})", "dislikes", 0.8),
        ]:
            mm = re.search(pattern, text)
            if not mm: continue
            obj = _ensure(mm.group(1).strip(), "object")
            sub = _subject()
            if sub is not None and sub.id != obj.id:
                out.append(Relation(
                    subject_id=sub.id, predicate=pred, object_id=obj.id,
                    source_engram_id=engram.id, confidence=conf,
                    created_at=engram.created_at))
        return out
    _PREFERENCE_PREDICATES = frozenset({"likes", "loves", "prefers", "dislikes", "hates"})

    def extract_atoms(self, engram):
        # v1.4 B3 hook: produce atom dicts from this engram.
        # Thin wrapper over extract_relations. Each relation becomes a
        # candidate (subject_id, predicate, object_id) atom. The caller
        # (MemoryService) resolves id -> name on the way into the
        # AtomStore; that is where the canonical normalization happens.
        if not getattr(engram, "content", None):
            return []
        try:
            rels = self.extract_relations(engram, entities=[], actor_id=engram.actor_id)
        except Exception:
            return []
        out_atoms = []
        for r in rels:
            kind = "preference" if r.predicate in self._PREFERENCE_PREDICATES else "fact"
            decay = "preference" if kind == "preference" else "semantic"
            out_atoms.append({
                "subject_id": r.subject_id,
                "predicate": r.predicate,
                "object_id": r.object_id,
                "kind": kind,
                "confidence": float(r.confidence),
                "importance": 0.6 if kind == "preference" else 0.5,
                "decay_type": decay,
            })
        return out_atoms
