from __future__ import annotations
import os
import asyncio
from typing import Callable
from .types import Cue, Engram, RecallResult, SemanticRecallResult
from .config import MemoryConfig
from .embeddings import EmbeddingProvider, HashEmbeddingProvider
from .providers import ProviderRegistry, default_registry
from .storage import HippocampalStore
from .semantic import SemanticStore, EntityExtractor
from .prospective import ProspectiveStore, ProspectiveScheduler
from .llm import LLMProvider, RuleLLMProvider
from .encoder import EngramEncoder
from .separation import PatternSeparator
from .working_memory import WorkingMemory
from .recall import PatternCompleter, Reconsolidator
from .consolidator import ReplayConsolidator
from .profile import ProfileStore, ProfileFact
from .persona import PersonaStore, Persona
from .activation import SpreadingActivation
import math as _math


def _diary_cos(a, b) -> float:
    """Cosine similarity over two embedding vectors.

    FIX (v1.41): bail to 0.0 when dimensions disagree. Cross-dimension
    comparisons used to silently truncate the longer vector to the
    shorter one AND compute norms on the truncated slice, returning
    meaningless scores after an embedding provider switch without a
    full rebuild.
    """
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    da = _math.sqrt(sum(x * x for x in a)) or 1.0
    db = _math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(a[i] * b[i] for i in range(len(a))) / (da * db)


class MemoryService:
    """Single facade. observe / recall / recall_semantic / list_prospective / cancel_prospective /
    set_embedding / set_llm / rebuild_embeddings.

    The v1.4.x additions live at the bottom: lazy atom / graph layer,
    background maintenance tasks, and a Windows-friendly close() that
    releases every SQLite connection the service opened in __init__.
    """
    def __init__(self, cfg: MemoryConfig | None = None,
                 registry: ProviderRegistry | None = None,
                 embedder: EmbeddingProvider | None = None,
                 llm: LLMProvider | None = None,
                 on_trigger_fire: Callable | None = None) -> None:
        self.cfg = cfg or MemoryConfig()
        self.registry: ProviderRegistry = registry or self._build_default_registry()
        if embedder is not None:
            self.registry.register_embedding(self.cfg.embedding_name, embedder)
        if llm is not None:
            self.registry.register_llm(self.cfg.llm_name, llm)
        _p = os.path.dirname(self.cfg.sqlite_path)
        if _p: os.makedirs(_p, exist_ok=True)
        self.embedder: EmbeddingProvider = self.registry.get_embedding(self.cfg.embedding_name)
        self.llm: LLMProvider = self.registry.get_llm(self.cfg.llm_name)
        self._current_embedding_name = self.cfg.embedding_name
        self._current_llm_name = self.cfg.llm_name
        self.store = HippocampalStore(
            self.cfg.sqlite_path, self.embedder,
            tokenizer_mode=getattr(self.cfg, "tokenizer_mode", "char"))
        self.encoder = EngramEncoder(self.embedder, llm=self.llm, cfg=self.cfg)
        self.separator = PatternSeparator(self.cfg)
        self.working = WorkingMemory(self.cfg)
        self.reconsolidator = Reconsolidator(self.store, self.cfg)
        self.completer = PatternCompleter(self.store, self.embedder, self.cfg, self.reconsolidator)
        self.consolidator = ReplayConsolidator(self.store, self.cfg, llm=self.llm)
        self.semantic = SemanticStore(self.cfg.sqlite_path) if self.cfg.enable_semantic else None
        from .relation_store import RelationStore
        self.relation_store = RelationStore(self.cfg.sqlite_path)
        from .diary_store import DiaryStore
        self.diary_store = DiaryStore(self.cfg.sqlite_path)
        self.extractor = EntityExtractor(self.llm) if self.cfg.enable_semantic else None
        self.prospective_store = ProspectiveStore(self.cfg.sqlite_path) if self.cfg.enable_prospective else None
        self.prospective_scheduler = (
            ProspectiveScheduler(self.prospective_store, self.cfg, on_fire=on_trigger_fire)
            if self.cfg.enable_prospective else None
        )
        self._consolidate_task: asyncio.Task | None = None
        self._prospective_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.profile = ProfileStore(self.cfg.sqlite_path) if self.cfg.enable_profile else None
        self.persona_store = PersonaStore(self.cfg.sqlite_path) if getattr(self.cfg, "enable_persona", False) else None
        self.activation = SpreadingActivation(self.semantic, self.store, self.cfg) if (self.semantic is not None) else None
        self.consolidator._semantic = self.semantic
        self.consolidator._profile = self.profile
        # v1.4 B3 + B4: atom + graph layers. Created lazily so the cold
        # path stays light and tests that do not need them avoid the
        # extra SQLite connections (which otherwise make Windows test
        # cleanup race-y).
        self.atom_store = None
        self.graph_store = None
        self.atom_lifecycle = None
        self._atom_task = None
        self._atom_thread = None
        self._atom_loop = None
        # v1.33: dedicated decay/tier maintenance loop (own daemon thread,
        # independent of the atom layer so it runs even when atoms are off).
        self._decay_thread = None
        self._decay_loop = None
        self._decay_stop = None

    def _ensure_atom_layer(self) -> bool:
        # v1.4 B3/B4: lazy-create AtomStore + GraphStore + AtomLifecycleManager
        # the first time anyone touches them. Returns True when the layer
        # is available, False if the cfg disables it or construction failed.
        if not (self.cfg.enable_atom_extraction or self.cfg.enable_graph_indexing):
            return False
        if self.atom_store is None and self.cfg.enable_atom_extraction:
            try:
                from .atom_store import AtomStore as _AtomStore
                self.atom_store = _AtomStore(self.cfg.sqlite_path)
            except Exception:
                self.atom_store = None
        if self.graph_store is None and self.cfg.enable_graph_indexing:
            try:
                from .graph_store import GraphStore as _GraphStore
                self.graph_store = _GraphStore(self.cfg.sqlite_path)
            except Exception:
                self.graph_store = None
        if self.atom_lifecycle is None and self.atom_store is not None:
            try:
                from .atom_lifecycle_manager import AtomLifecycleManager as _ALM
                self.atom_lifecycle = _ALM(self.atom_store)
            except Exception:
                self.atom_lifecycle = None
        return (self.atom_store is not None) or (self.graph_store is not None)

    def _build_default_registry(self) -> ProviderRegistry:
        return default_registry()

    # ---------- model switch ----------
    def set_embedding(self, name: str) -> str:
        if not self.registry.has_embedding(name):
            raise KeyError(f"unknown embedding: {name} (available: {self.registry.list_embeddings()})")
        old = self._current_embedding_name
        self.embedder = self.registry.get_embedding(name)
        self._current_embedding_name = name
        self.encoder.set_embedder(self.embedder)
        if self.cfg.auto_rebuild_on_switch and old != name:
            dirty = self.rebuild_embeddings()
            if dirty: return f"switched embedding {old} -> {name} (rebuilt {dirty} engrams)"
        return f"switched embedding {old} -> {name}"

    def set_llm(self, name: str) -> str:
        if not self.registry.has_llm(name):
            raise KeyError(f"unknown llm: {name} (available: {self.registry.list_llms()})")
        old = self._current_llm_name
        self.llm = self.registry.get_llm(name)
        self._current_llm_name = name
        # keep downstream LLM consumers in sync (mirrors set_embedding ->
        # encoder.set_embedder). Without this the encoder/extractor/consolidator
        # stay pinned to the construction-time provider (usually RuleLLM),
        # so LLM-based extraction never fires after a runtime switch.
        try:
            self.encoder.set_llm(self.llm)
        except Exception:
            pass
        if getattr(self, "extractor", None) is not None:
            try:
                self.extractor.set_llm(self.llm)
            except Exception:
                pass
        if getattr(self, "consolidator", None) is not None:
            try:
                self.consolidator._llm = self.llm
            except Exception:
                pass
        return f"switched llm {old} -> {name}"

    # ---------- provider registration (delegates to registry) ----------
    def register_embedding(self, name: str, provider: EmbeddingProvider) -> None:
        self.registry.register_embedding(name, provider)

    def register_llm(self, name: str, provider: LLMProvider) -> None:
        self.registry.register_llm(name, provider)

    def current_embedding(self) -> str:
        return self._current_embedding_name

    def current_llm(self) -> str:
        return self._current_llm_name

    def rebuild_embeddings(self) -> int:
        # Re-embed every engram under the current model. Returns count rebuilt.
        n = 0
        batch = self.cfg.rebuild_batch_size
        for e in self.store.all(limit=100000):
            try:
                e.embedding = self.embedder.embed(e.content or "")
                e.embedding_model = self._current_embedding_name
                self.store.upsert(e)
                n += 1
                if batch and n % batch == 0:
                    pass
            except Exception:
                continue
        return n

    # ---------- observe ----------
    def observe(self, *, session_id: str, actor_id: str, platform: str,
                channel_id: str, content: str, persona_id: str = "") -> Engram:
        from .session_filter import SessionFilter, FilterContext, FilterVerdict
        decision = SessionFilter(self.cfg).decide(FilterContext(
            platform=platform, channel_id=channel_id,
            actor_id=actor_id, content=content))
        if decision.verdict == FilterVerdict.DENY:
            from .types import Engram
            denied = Engram(
                id="denied:" + (session_id or "?") + ":" + (actor_id or "?"),
                created_at=0.0,
                session_id=session_id or "",
                actor_id=actor_id or "",
                platform=platform or "",
                channel_id=channel_id or "",
                content="[filtered: " + decision.reason + "]",
                summary="[filtered: " + decision.reason + "]",
            )
            setattr(denied, "_filter_denied", True)
            setattr(denied, "_filter_reason", decision.reason)
            setattr(denied, "_filter_matched", decision.matched_rule)
            return denied
        e = self.encoder.encode(
            session_id=session_id, actor_id=actor_id,
            platform=platform, channel_id=channel_id, content=content,
            persona_id=persona_id)
        e.embedding_model = self._current_embedding_name
        if getattr(self.cfg, "dedup_enabled", False):
            dup = self._find_text_duplicate(e)
            if dup is not None:
                dup.strength = min(1.0, dup.strength + 0.05)
                dup.importance = min(1.0, max(dup.importance, e.importance))
                dup.access_count = (dup.access_count or 0) + 1
                self.working.add(dup)
                self.store.upsert(dup)
                self._post_ingest(dup)
                return dup
        cands = self.working.candidates_for_separation(session_id)
        if self.cfg.enable_separation:
            action, target = self.separator.resolve(e, cands)
        else:
            action, target = ("new", None)
        if action == "merge" and target is not None:
            target.content = (target.content + "\n" + e.content).strip()
            target.strength = min(1.0, target.strength + 0.05)
            target.importance = min(1.0, max(target.importance, e.importance))
            target.embedding = e.embedding
            target.embedding_model = e.embedding_model
            drop = self.cfg.interference_strength_drop
            from .storage import _cos
            for sib in cands:
                if sib.id == target.id:
                    continue
                if _cos(e.embedding, sib.embedding) >= self.cfg.pattern_similar_threshold:
                    sib.strength = max(0.0, sib.strength - drop)
                    self.store.upsert(sib)
            self.working.add(target)
            self.store.upsert(target)
            self._post_ingest(target)
            return target
        if action == "link" and target is not None:
            self.separator.apply_link(e, target, self.cfg.separation_max_links)
            drop = self.cfg.interference_strength_drop
            target.strength = max(0.0, target.strength - drop)
            self.working.add(e)
            if e.importance >= self.cfg.importance_floor_for_long_term:
                self.store.upsert(e)
            self.store.upsert(target)
            self._post_ingest(e)
            return e
        self.working.add(e)
        if e.importance >= self.cfg.importance_floor_for_long_term:
            self.store.upsert(e)
        self._post_ingest(e)
        return e

    def store_summary(self, summary: dict, identity: dict) -> "Engram | None":
        """Store ONE conversation/diary summary as an engram (v1.17 B-1).

        `summary` is the ConversationSummarizer output (summary/key_facts/
        topics/participants/relations + _* meta). `identity` carries the
        channel stamps (chat_type / session_id / actor_id / platform /
        channel_id / group_id / group_name / peer_* / memory_type).
        Returns the stored Engram, or None when the summary text is empty.
        """
        from .types import Engram
        text = (summary.get("summary") or "").strip()
        if not text:
            return None
        facts = summary.get("key_facts") or []
        content = text
        if facts:
            content = text + "\n" + "\n".join("- " + str(f) for f in facts)
        actor_id = identity.get("actor_id") or identity.get("peer_actor_id") or "conversation"
        e = Engram(
            session_id=identity.get("session_id", "") or "",
            actor_id=actor_id,
            platform=identity.get("platform", "") or "",
            channel_id=identity.get("channel_id", "") or "",
            persona_id=identity.get("persona_id", "") or "",
            content=content,
            summary=text,
            topics=list(summary.get("topics") or []),
            entities=list(summary.get("participants") or []),
            importance=float(summary.get("importance", 0.6) or 0.6),
            memory_type=identity.get("memory_type", "episodic") or "episodic",
            embedding_model=self._current_embedding_name,
        )
        try:
            e.embedding = self.embedder.embed(content)
        except Exception as ex:
            print("[hippocampus] store_summary embed error: " + repr(ex))
            e.embedding = []
        # attach conversation identity + relations as tags/metadata-ish fields.
        stamps = []
        if identity.get("chat_type"):
            stamps.append("chat:" + identity["chat_type"])
        if identity.get("group_id"):
            stamps.append("group:" + str(identity["group_id"]))
        if identity.get("group_name"):
            stamps.append("groupname:" + str(identity["group_name"]))
        if identity.get("peer_name"):
            stamps.append("peer:" + str(identity["peer_name"]))
        if stamps:
            e.tags = list(e.tags) + stamps
        name_map = summary.get("participant_names") or {}
        try:
            self.working.add(e)
            self.store.upsert(e)
            self._post_ingest(e, name_map=name_map)
        except Exception as ex:
            print("[hippocampus] store_summary persist error: " + repr(ex))
            return None
        # v1.19 B-2: persist structured relations with conflict-driven supersede.
        # v1.30: LLM relations are the single source of truth. We also mirror
        # them into SemanticStore (entities + relations) so the internal graph
        # algorithms (spreading activation, profile, graph retrieval) and the
        # WebUI see the SAME LLM-derived facts, with entity type taken from
        # the LLM (fixing rule-classified "unknown").
        rels = summary.get("relations") or []
        if rels and getattr(self, "relation_store", None) is not None:
            from .relation_store import Relation
            hyst = float(getattr(self.cfg, "relation_supersede_hysteresis", 0.0) or 0.0)
            for r in rels:
                try:
                    rel = Relation(
                        subject=str(r.get("subject", "") or "").strip(),
                        predicate=str(r.get("relation", "") or "").strip(),
                        object=str(r.get("object", "") or "").strip(),
                        confidence=float(r.get("confidence", 0.5) or 0.5),
                        actor_id=actor_id,
                        channel_id=identity.get("channel_id", "") or "",
                        source_engram_id=e.id,
                        subject_type=str(r.get("subject_type", "") or "").strip(),
                        object_type=str(r.get("object_type", "") or "").strip())
                    if rel.subject and rel.predicate:
                        self.relation_store.add_with_supersede(rel, hysteresis=hyst)
                        self._mirror_relation_to_semantic(rel, e)
                except Exception as rex:
                    print("[hippocampus] relation persist error: " + repr(rex))
        return e

    def recall_relations(self, query: str, *, top_n: int = 3,
                         min_confidence: float = 0.0) -> list:
        """v1.19 B-2: pipeline-filtered relations for injection (option 4,
        no weighting): relevance (subject/object/predicate appears in query)
        -> confidence threshold -> top-N. Returns list[Relation]."""
        rs = getattr(self, "relation_store", None)
        if rs is None:
            return []
        try:
            q = (query or "").lower()
            cands = rs.all_active(limit=500)
            # relevance filter: any of subject/object/predicate substring-matches query
            relevant = []
            for r in cands:
                fields = (r.subject, r.object, r.predicate)
                if any(f and f.lower() in q for f in fields):
                    relevant.append(r)
            # fallback: if query matched nothing, do not inject noise
            pool = relevant
            pool = [r for r in pool if r.confidence >= min_confidence]
            pool.sort(key=lambda r: (r.confidence, r.updated_at), reverse=True)
            return pool[:max(0, top_n)]
        except Exception as ex:
            print("[hippocampus] recall_relations error: " + repr(ex))
            return []

    # ---------- v1.20 (B-3): diary layer ----------
    def cache_daily_line(self, meta: dict) -> None:
        """Append one raw line (user OR bot) to the daily cache. Best-effort;
        never raises out of the event hook."""
        ds = getattr(self, "diary_store", None)
        if ds is None:
            return
        if not bool(getattr(self.cfg, "diary_enabled", False)):
            return
        content = (meta.get("content", "") or "").strip()
        if not content:
            return
        try:
            from .diary_store import DailyLine
            ds.add_line(DailyLine(
                channel_id=meta.get("channel_id", "") or "",
                chat_type=meta.get("chat_type", "") or "",
                actor_id=meta.get("actor_id", "") or "",
                speaker=meta.get("speaker", "") or meta.get("actor_id", "") or "",
                content=content,
                is_bot=bool(meta.get("is_bot", False)),
                group_id=meta.get("group_id", "") or "",
                group_name=meta.get("group_name", "") or "",
                peer_actor_id=meta.get("peer_actor_id", "") or "",
                peer_name=meta.get("peer_name", "") or "",
                session_id=meta.get("session_id", "") or "",
                platform=meta.get("platform", "") or "",
                persona_id=meta.get("persona_id", "") or ""))
        except Exception as ex:
            print("[hippocampus] cache_daily_line error: " + repr(ex))

    def store_diary(self, diary: dict, identity: dict) -> "Engram | None":
        """Persist ONE diary as an engram (memory_type=diary) + chunk-level
        embeddings for chunk recall. Mirrors store_summary persistence."""
        from .types import Engram
        text = (diary.get("summary") or "").strip()
        if not text:
            return None
        facts = diary.get("key_facts") or []
        content = text
        if facts:
            content = text + "\n" + "\n".join("- " + str(f) for f in facts)
        actor_id = identity.get("actor_id") or identity.get("peer_actor_id") or "diary"
        e = Engram(
            session_id=identity.get("session_id", "") or "",
            actor_id=actor_id,
            platform=identity.get("platform", "") or "",
            channel_id=identity.get("channel_id", "") or "",
            persona_id=identity.get("persona_id", "") or "",
            content=content,
            summary=text,
            topics=list(diary.get("topics") or []),
            entities=list(diary.get("participants") or []),
            importance=float(diary.get("importance", 0.6) or 0.6),
            memory_type="diary",
            embedding_model=self._current_embedding_name,
        )
        try:
            e.embedding = self.embedder.embed(content)
        except Exception as ex:
            print("[hippocampus] store_diary embed error: " + repr(ex))
            e.embedding = []
        stamps = ["kind:diary"]
        if identity.get("chat_type"):
            stamps.append("chat:" + identity["chat_type"])
        if identity.get("group_id"):
            stamps.append("group:" + str(identity["group_id"]))
        if identity.get("group_name"):
            stamps.append("groupname:" + str(identity["group_name"]))
        if identity.get("peer_name"):
            stamps.append("peer:" + str(identity["peer_name"]))
        if identity.get("day_label"):
            stamps.append("day:" + str(identity["day_label"]))
        e.tags = list(e.tags) + stamps
        try:
            self.store.upsert(e)
            self._post_ingest(e)
        except Exception as ex:
            print("[hippocampus] store_diary persist error: " + repr(ex))
            return None
        # chunk-level embeddings for chunk recall
        ds = getattr(self, "diary_store", None)
        if ds is not None:
            try:
                from .diary_writer import split_chunks
                from .diary_store import DiaryChunk
                first_ts = float(diary.get("_first_ts", 0.0) or 0.0)
                last_ts = float(diary.get("_last_ts", 0.0) or 0.0)
                cmax = int(getattr(self.cfg, "diary_chunk_max_chars", 400) or 400)
                pieces = split_chunks(text, first_ts, last_ts, max_chars=cmax)
                chunks = []
                for seq, ptext, ts0, ts1 in pieces:
                    try:
                        emb = self.embedder.embed(ptext)
                    except Exception:
                        emb = []
                    chunks.append(DiaryChunk(
                        diary_id=e.id, channel_id=e.channel_id, seq=seq,
                        text=ptext, embedding=emb,
                        embedding_model=self._current_embedding_name,
                        ts_start=ts0, ts_end=ts1,
                        persona_id=getattr(e, "persona_id", "") or ""))
                if chunks:
                    ds.add_chunks(chunks)
            except Exception as ex:
                print("[hippocampus] store_diary chunk error: " + repr(ex))
        return e

    def recall_diary_chunks(self, query: str, *, top_n: int = 1,
                            min_score: float = 0.0,
                            persona_id: str | None = None) -> list:
        """Chunk-level diary recall (B-3 req 13): embed query, score against
        stored diary chunks by cosine, return top-N chunk texts. Returns
        list[(text, score)].

        FIX (v1.41) BUG-4: when the diary_chunks table is missing entries
        for some diary engrams (e.g. process crashed between the engram
        upsert and the chunks commit), fall back to slicing the diary
        engram's summary on the fly so the user does not silently lose
        recall coverage. The fallback embeds each slice with the current
        embedder on demand; no write back.
        """
        ds = getattr(self, "diary_store", None)
        if ds is None or top_n <= 0:
            return []
        try:
            qvec = self.embedder.embed(query or "")
        except Exception:
            return []
        if not qvec:
            return []
        try:
            chunks = ds.all_chunks(limit=2000, persona_id=persona_id)
        except Exception:
            chunks = []
        scored = []
        seen_diary_ids = set()
        for ch in chunks:
            if not ch.embedding:
                continue
            seen_diary_ids.add(ch.diary_id)
            s = _diary_cos(qvec, ch.embedding)
            if s >= min_score:
                scored.append((ch.text, s))
        # Fallback: cover diaries that have an engram but no chunks yet.
        try:
            for d in self.store.list_active(limit=50000):
                if (getattr(d, "memory_type", "") or "") != "diary":
                    continue
                if d.id in seen_diary_ids:
                    continue
                if (persona_id is not None
                        and (getattr(d, "persona_id", "") or "") != (persona_id or "")):
                    continue
                text = (getattr(d, "summary", "") or getattr(d, "content", "") or "").strip()
                if not text:
                    continue
                try:
                    from .diary_writer import split_chunks as _split
                except Exception:
                    continue
                try:
                    pieces = _split(text, 0.0, 0.0, max_chars=400)
                except Exception:
                    pieces = []
                for _seq, ptext, _t0, _t1 in pieces:
                    try:
                        emb = self.embedder.embed(ptext)
                    except Exception:
                        emb = []
                    if not emb:
                        continue
                    s = _diary_cos(qvec, emb)
                    if s >= min_score:
                        scored.append((ptext, s))
        except Exception:
            pass
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:max(0, top_n)]

    def run_daily_diary(self, *, now: float | None = None) -> "tuple[int, list]":
        """Build diaries for the just-finished logical day across all cached
        channels, then TTL-purge old daily messages.

        FIX (v1.41):
          - BUG-2:  t1 extends to next_start + night_hours so a still-active
                    late-night session is not truncated at today 00:00.
          - BUG-3:  Idempotency check skips (ch, pid, day_label) already
                    written; the raw daily_messages for that window are
                    purged after a successful write so a manual /mem diary
                    re-run on the same day cannot re-produce the diary.
          - BUG-11: Dropped the dead `+ 3600.0` that day_bounds() ate.
          - BUG-12: Returns (written, failed) so callers can surface partial
                    failure instead of silently swallowing it.
        """
        import time as _t
        ds = getattr(self, "diary_store", None)
        if ds is None or not bool(getattr(self.cfg, "diary_enabled", False)):
            return (0, [])
        now = now if now is not None else _t.time()
        from .diary_writer import (DiaryWriter, day_bounds, resolve_cut)
        writer = getattr(self, "_diary_writer", None)
        if writer is None:
            writer = DiaryWriter(self.cfg, llm=self.llm)
            self._diary_writer = writer
        else:
            try:
                writer.set_llm(self.llm)
            except Exception:
                pass
        night_h = float(getattr(self.cfg, "diary_night_window_hours", 6.0) or 6.0)
        gap = float(getattr(self.cfg, "diary_night_gap_seconds", 1800.0) or 1800.0)
        consume = bool(getattr(self.cfg, "diary_consume_cache_on_write", True))
        # target = the day that ended at the most recent midnight (yesterday)
        today_start, _ = day_bounds(now)
        target_day = today_start - 86400.0
        d_start, next_start = day_bounds(target_day)
        day_label = _t.strftime("%Y-%m-%d", _t.localtime(d_start))
        written = 0
        failed: list = []
        # search upper bound for channel activity: include the entire
        # night window past next_start so cross-midnight sessions show up.
        search_end = next_start + night_h * 3600.0
        try:
            channels = ds.channels_with_lines(d_start, search_end)
        except Exception as ex:
            print("[hippocampus] run_daily_diary channels error: " + repr(ex))
            channels = []
        for ch, pid in channels:
            try:
                # Idempotency: skip (channel, persona, day) already written.
                if self._existing_diary_for_day(ch, pid, day_label) is not None:
                    continue
                t0 = resolve_cut(ds, ch, d_start,
                                  night_hours=night_h,
                                  min_gap_seconds=gap,
                                  persona_id=pid)
                # FIX (v1.41) BUG-2: when no idle gap exists in the night
                # window, extend the diary to the end of the night window
                # rather than clamping at today 00:00.
                t1 = resolve_cut(ds, ch, next_start,
                                  night_hours=night_h,
                                  min_gap_seconds=gap,
                                  persona_id=pid,
                                  fallback=search_end)
                lines = ds.lines_in_range(ch, t0, t1, persona_id=pid)
                if not lines:
                    continue
                diary = writer.compose(lines, day_label)
                if diary is None or not (diary.get("summary") or "").strip():
                    continue
                first = lines[0]
                identity = {
                    "session_id": first.session_id,
                    "actor_id": first.peer_actor_id or "",
                    "platform": first.platform,
                    "channel_id": ch,
                    "persona_id": pid,
                    "chat_type": first.chat_type,
                    "group_id": first.group_id,
                    "group_name": first.group_name,
                    "peer_actor_id": first.peer_actor_id,
                    "peer_name": first.peer_name,
                    "day_label": day_label,
                }
                if self.store_diary(diary, identity) is not None:
                    written += 1
                    # FIX (v1.41) BUG-3: consume the cache window so the
                    # next run (manual / scheduler drift) cannot re-write
                    # the same diary from the same raw lines.
                    if consume:
                        try:
                            ds.purge_lines_in_range(ch, t0, t1, persona_id=pid)
                        except Exception as pex:
                            print("[hippocampus] diary cache purge error: " + repr(pex))
            except Exception as ex:
                print("[hippocampus] run_daily_diary channel error: " + repr(ex))
                failed.append({"channel_id": ch, "persona_id": pid, "error": repr(ex)})
        # TTL purge
        try:
            ttl_days = int(getattr(self.cfg, "diary_message_ttl_days", 7) or 7)
            ds.purge_older_than(now - ttl_days * 86400.0)
        except Exception as ex:
            print("[hippocampus] run_daily_diary purge error: " + repr(ex))
        return (written, failed)

    def _existing_diary_for_day(self, channel_id: str, persona_id: str,
                               day_label: str):
        """FIX (v1.41) BUG-3: scan active diary engrams for an existing
        (channel_id, persona_id, day:<day_label>) triple. Returns the
        Engram id or None. Cheap O(N) over active engrams; runs at most
        once per channel per day.
        """
        if not day_label:
            return None
        day_tag = "day:" + str(day_label)
        try:
            for r in self.store.list_active(limit=200000):
                if (getattr(r, "memory_type", "") or "") != "diary":
                    continue
                if (getattr(r, "channel_id", "") or "") != channel_id:
                    continue
                if (getattr(r, "persona_id", "") or "") != (persona_id or ""):
                    continue
                tags = getattr(r, "tags", None) or []
                if any(str(t) == day_tag for t in tags):
                    return getattr(r, "id", None)
        except Exception:
            return None
        return None

    def _find_text_duplicate(self, e: Engram):
        """Text-layer near-duplicate check (v1.11). Uses FTS to pull
        cross-session candidates for the same actor, then word-level
        Jaccard (shared tokenizer) gated at dedup_threshold. Returns the
        existing Engram to merge into, or None. Advisory; never raises."""
        text = (e.content or e.summary or "").strip()
        if not text:
            return None
        try:
            from .dedup import best_duplicate
            k = int(getattr(self.cfg, "dedup_candidate_k", 10) or 10)
            thr = float(getattr(self.cfg, "dedup_threshold", 0.9) or 0.9)
            mode = getattr(self.cfg, "tokenizer_mode", "char")
            hits = self.store.fts_search(
                text, k=k, actor_id=(e.actor_id or None))
            cands = [h for (h, _s) in hits if h.id != e.id]
            res = best_duplicate(text, cands, mode=mode, threshold=thr)
            return res[0] if res else None
        except Exception as ex:
            print("[hippocampus] dedup check error: " + repr(ex))
            return None

    def _mirror_relation_to_semantic(self, rel, engram) -> None:
        """v1.30: mirror an LLM RelationStore triple into SemanticStore so
        internal graph algorithms share the same LLM-derived facts. Upserts
        subject/object as entities (type taken from the LLM, with rule
        fallback) and links them with a confidence-carrying relation.
        Best-effort: any failure is swallowed so it never blocks the primary
        RelationStore write."""
        if self.semantic is None:
            return
        try:
            from .types import Entity, Relation as SemRelation
            from .semantic import _classify

            def _ensure(name: str, llm_type: str):
                name = (name or "").strip()
                if not name:
                    return None
                etype = (llm_type or "").strip().lower()
                if not etype or etype == "unknown":
                    etype = _classify(name)
                stored = self.semantic.upsert_entity(Entity(
                    name=name, type=etype,
                    source_engram_ids=[engram.id],
                    created_at=engram.created_at, last_seen=engram.created_at,
                    mention_count=1))
                # upsert_entity never downgrades type; upgrade unknown -> typed
                if etype and etype != "unknown":
                    self.semantic.update_entity_type(stored.id, etype)
                return stored

            subj = _ensure(rel.subject, getattr(rel, "subject_type", ""))
            obj = _ensure(rel.object, getattr(rel, "object_type", ""))
            if subj is None or obj is None:
                return
            self.semantic.add_relation(SemRelation(
                subject_id=subj.id, predicate=rel.predicate,
                object_id=obj.id, source_engram_id=engram.id,
                confidence=float(getattr(rel, "confidence", 0.5) or 0.5),
                created_at=engram.created_at))
            refs = list(dict.fromkeys([*engram.entity_refs, subj.id, obj.id]))
            engram.entity_refs = refs
        except Exception as ex:
            print("[hippocampus] mirror relation error: " + repr(ex))

    def _post_ingest(self, e: Engram, name_map: dict | None = None) -> None:
        # FIX (v1.41) BUG-9: diary + summary engrams are already produced
        # by the diary / summary pipeline (which extracts relations and
        # entities itself). Running them through the per-engram extractor
        # again would dump every diary-mentioned person into the semantic
        # graph as a fresh entity and re-fan-out the atom layer every
        # single day. Skip silently.
        mtype = (getattr(e, "memory_type", "") or "").lower()
        if mtype in ("diary", "summary", "conversation"):
            return
        if self.semantic is not None and self.extractor is not None:
            from .semantic import _classify
            ents = self.extractor.extract_entities(e)
            nm = name_map or {}
            if nm:
                for ent in ents:
                    disp = (nm.get(ent.name) or "").strip()
                    if disp and disp != ent.name:
                        if ent.name not in ent.aliases:
                            ent.aliases.append(ent.name)
                        ent.name = disp
                        ent.type = "person"
            stored_ids: list = []
            for ent in ents:
                stored = self.semantic.upsert_entity(ent)
                stored_ids.append(stored.id)
            stored_entities = [self.semantic.find_entity_by_name(ent.name) for ent in ents]
            stored_entities = [s for s in stored_entities if s is not None]

            def _resolve(name: str, etype: str):
                existing = self.semantic.find_entity_by_name(name)
                if existing is not None:
                    return existing
                from .types import Entity
                created = Entity(
                    name=name, type=(etype or _classify(name)),
                    source_engram_ids=[e.id],
                    created_at=e.created_at, last_seen=e.created_at,
                    mention_count=1)
                return self.semantic.upsert_entity(created)

            rels = self.extractor.extract_relations(
                e, stored_entities, actor_id=e.actor_id, resolve=_resolve)
            ref_ids = list(stored_ids)
            for r in rels:
                self.semantic.add_relation(r)
                ref_ids.append(r.subject_id)
                ref_ids.append(r.object_id)
            e.entity_refs = list(dict.fromkeys([*e.entity_refs, *ref_ids]))
            self.store.upsert(e)
        # v1.4 B3: extract atoms from this engram and upsert.
        # Walk a small set of preference patterns and pair them against
        # engram.entities (which always reflect what the encoder extracted).
        if self.cfg.enable_atom_extraction and self.extractor is not None and e.entities and e.actor_id:
            self._ensure_atom_layer()
            if self.atom_store is not None:
                try:
                    from .memory_atom_models import make_fact_atom, make_preference_atom
                    import re as _re
                    text = (e.content or "").lower()
                    subject_name = None
                    for nm in e.entities:
                        if nm and nm.lower() in text:
                            subject_name = nm
                            break
                    if subject_name is None:
                        pass
                    else:
                        for pattern, pred, kind in [
                            (_re.compile(r"i (?:love|like)\s+"), "likes", "preference"),
                            (_re.compile(r"i (?:hate|dislike)\s+"), "dislikes", "preference"),
                            (_re.compile(r"i live in\s+"), "resides_in", "fact"),
                        ]:
                            m = pattern.search(text)
                            if not m:
                                continue
                            tail = text[m.end():]
                            object_name = None
                            for nm in e.entities:
                                if not nm or nm == subject_name:
                                    continue
                                if nm.lower() in tail or tail.startswith(nm.lower()):
                                    object_name = nm
                                    break
                            if object_name is None:
                                continue
                            factory = make_preference_atom if kind == "preference" else make_fact_atom
                            atom = factory(
                                subject_name, pred, object_name,
                                source_engram_id=e.id,
                                confidence=0.8 if kind == "preference" else 0.7,
                                actor_id=e.actor_id or "",
                                platform=e.platform or "",
                                channel_id=e.channel_id or "",
                                importance=0.6 if kind == "preference" else 0.5,
                            )
                            if kind == "preference":
                                atom.decay_type = "preference"
                            else:
                                atom.decay_type = "semantic"
                            self.atom_store.upsert(atom)
                except Exception:
                    pass
        # v1.4 B4: mirror entity_refs into the GraphStore fast-path index.
        if self.cfg.enable_graph_indexing and e.entity_refs:
            self._ensure_atom_layer()
            if self.graph_store is not None:
                try:
                    for ref in e.entity_refs:
                        self.graph_store.add_entity_engram_ref(ref, e.id, weight=1.0)
                except Exception:
                    pass
        if self.prospective_scheduler is not None:
            self.prospective_scheduler.create_from_engram(e)

    # ---------- recall ----------
    def recall(self, cue: Cue) -> RecallResult:
        result = self.completer.recall(cue, embedding_model=self._current_embedding_name)
        wm_key = cue.channel_id or cue.actor_id or ""
        wm = self.working.snapshot(wm_key)
        if wm:
            head = wm[-cue.k:]
            result.engrams = head + result.engrams
            result.scores = [1.0] * len(head) + result.scores
            if result.confidences is not None:
                result.confidences = [1.0] * len(head) + result.confidences
        return result

    def recall_semantic(self, query: str, *, actor_id: str | None = None,
                        k: int = 5) -> SemanticRecallResult:
        if self.semantic is None:
            return SemanticRecallResult(entities=[], relations=[], engrams=[], scores=[])
        entities = self.semantic.search_entities(query, limit=k * 2)
        relations = []
        for ent in entities:
            relations.extend(self.semantic.relations_of(ent.id))
        seen = set(); uniq_rels = []
        for r in relations:
            if r.id in seen: continue
            seen.add(r.id); uniq_rels.append(r)
        ent_ids = {e.id for e in entities}
        engrams = []
        for e in self.store.all(limit=10000):
            if any(ref in ent_ids for ref in e.entity_refs):
                engrams.append(e)
        engrams = engrams[:k]
        scores = [1.0] * len(engrams)
        return SemanticRecallResult(entities=entities, relations=uniq_rels,
                                       engrams=engrams, scores=scores)

    def recall_dual_route(self, cue: Cue) -> RecallResult:
        # v1.3 dual route lives in retrieval.dual_route. We delegate.
        from .retrieval.dual_route import DualRouteRetriever, DualRouteConfig
        dr = DualRouteRetriever(self, DualRouteConfig())
        return dr.search(cue)

    # ---------- prospective ----------
    def list_prospective(self, status: str | None = None) -> list:
        if self.prospective_store is None:
            return []
        return self.prospective_store.list(status=status)

    def cancel_prospective(self, trigger_id: str) -> bool:
        if self.prospective_store is None:
            return False
        return self.prospective_store.cancel(trigger_id)

    # ---------- async lifecycle (used by the AstrBot plugin shell) ----------
    async def start(self) -> None:
        self.start_background_tasks()

    async def stop(self) -> None:
        await self.stop_background_tasks()

    # ---------- v1.4 B3: background maintenance ----------
    # Works whether or not a loop is running: falls back to a dedicated
    # daemon thread + new_event_loop when called from sync code.
    def start_background_tasks(self) -> None:
        # v1.13: one-shot tier reclassification at startup (cheap, no loop).
        # Refreshes hot/warm/cold for memories that aged while offline.
        if getattr(self.cfg, "tiering_enabled", False):
            try:
                self.reclassify_tiers()
            except Exception as ex:
                print("[hippocampus] startup tier sweep error: " + repr(ex))
        # v1.33: start the periodic memory-decay + tier-maintenance loop.
        try:
            self._start_decay_loop()
        except Exception as ex:
            print("[hippocampus] decay loop start error: " + repr(ex))
        if not (self.cfg.enable_atom_extraction or self.cfg.enable_graph_indexing):
            return
        self._ensure_atom_layer()
        if self.atom_lifecycle is None:
            return
        di = float(self.cfg.atom_decay_interval_seconds)
        gi = float(self.cfg.atom_gc_interval_seconds)
        if di <= 0 and gi <= 0:
            return
        if self._atom_task is not None and not self._atom_task.done():
            return
        import asyncio as _asyncio
        import threading as _threading
        try:
            _asyncio.get_running_loop()
        except RuntimeError:
            self._self_threaded_start(di, gi)
            return
        self._atom_task = _asyncio.create_task(
            self.atom_lifecycle._maintenance_loop(di, gi)
        )

    def _self_threaded_start(self, di: float, gi: float) -> None:
        # No running loop: drive the maintenance loop in a dedicated
        # daemon thread with its own event loop.
        import asyncio as _asyncio
        import threading as _threading
        import concurrent.futures as _cf
        if self._atom_thread is None or not self._atom_thread.is_alive():
            self._atom_loop = _asyncio.new_event_loop()
            def _runner():
                _asyncio.set_event_loop(self._atom_loop)
                try:
                    self._atom_loop.run_forever()
                finally:
                    try:
                        self._atom_loop.close()
                    except Exception:
                        pass
            self._atom_thread = _threading.Thread(
                target=_runner, name="hippocampus-atom-maint", daemon=True
            )
            self._atom_thread.start()
        fut = _cf.Future()
        def _schedule():
            try:
                t = self._atom_loop.create_task(
                    self.atom_lifecycle._maintenance_loop(di, gi)
                )
                fut.set_result(t)
            except BaseException as exc:
                fut.set_exception(exc)
        self._atom_loop.call_soon_threadsafe(_schedule)
        self._atom_task = fut.result(timeout=2.0)

    def _self_threaded_stop(self) -> None:
        import asyncio as _asyncio
        worker_loop = getattr(self, "_atom_loop", None)
        if worker_loop is not None and not worker_loop.is_closed():
            try:
                worker_loop.call_soon_threadsafe(worker_loop.stop)
            except Exception:
                pass
        thread = getattr(self, "_atom_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._atom_task = None
        self._atom_loop = None
        self._atom_thread = None

    async def stop_background_tasks(self) -> None:
        try:
            self._stop_decay_loop()
        except Exception:
            pass
        if self.atom_lifecycle is None:
            return
        import asyncio as _asyncio
        task = self._atom_task
        if isinstance(task, _asyncio.Task):
            try:
                await self.atom_lifecycle.stop()
            except Exception:
                pass
            self._atom_task = None
            return
        # Worker-thread task: stop the loop, join the thread, then clear.
        await _asyncio.to_thread(self._self_threaded_stop)

    def stop_background_tasks_sync(self) -> None:
        # Sync variant for callers without a running loop (e.g. close()).
        try:
            self._stop_decay_loop()
        except Exception:
            pass
        if self.atom_lifecycle is None:
            return
        import asyncio as _asyncio
        task = self._atom_task
        if isinstance(task, _asyncio.Task):
            try:
                task.cancel()
            except Exception:
                pass
            self._atom_task = None
            return
        self._self_threaded_stop()
    async def stop_background_tasks_async(self) -> None:
        # Awaitable variant for callers that already have a running loop.
        if self.atom_lifecycle is None:
            return
        try:
            await self.atom_lifecycle.stop()
        except Exception:
            pass
        self._atom_task = None

    def run_atom_decay(self) -> int:
        if self.atom_lifecycle is None:
            return 0
        return self.atom_lifecycle.run_decay()

    def run_atom_gc(self, floor: float = 0.05, min_age_seconds: float = 0.0) -> int:
        if self.atom_lifecycle is None:
            return 0
        return self.atom_lifecycle.run_gc(floor=floor, min_age_seconds=min_age_seconds)

    def run_memory_decay(self) -> dict:
        """One synchronous sweep: Ebbinghaus strength decay on every engram,
        then a hot/warm/cold reclassification. Non-destructive (no deletes;
        strength only drops, tiers only move). Returns a small report dict."""
        out = {"decayed_below_floor": 0, "reclassified": False}
        try:
            tau = float(getattr(self.cfg, "decay_tau_base", 60 * 60 * 24 * 7.0))
            floor = float(getattr(self.cfg, "decay_floor", 0.05))
            out["decayed_below_floor"] = self.store.decay_pass(tau, floor)
        except Exception as ex:
            print("[hippocampus] engram decay_pass error: " + repr(ex))
        try:
            if getattr(self.cfg, "tiering_enabled", False):
                self.reclassify_tiers()
                out["reclassified"] = True
        except Exception as ex:
            print("[hippocampus] decay-loop reclassify error: " + repr(ex))
        try:
            if (bool(getattr(self.cfg, "relation_decay_enabled", True))
                    and getattr(self, "relation_store", None) is not None
                    and self.relation_store.is_open()):
                tau = float(getattr(self.cfg, "relation_decay_tau_seconds",
                                    60 * 60 * 24 * 30.0))
                rfloor = float(getattr(self.cfg, "relation_decay_floor", 0.1))
                out["relations"] = self.relation_store.decay_pass(tau, rfloor)
        except Exception as ex:
            print("[hippocampus] relation decay error: " + repr(ex))
        # v1.34: hard-delete relations soft-forgotten longer than the retention.
        try:
            ret_days = float(getattr(self.cfg, "relation_forget_retention_days", 14.0))
            if (ret_days > 0
                    and getattr(self, "relation_store", None) is not None
                    and self.relation_store.is_open()):
                out["relations_purged"] = self.relation_store.purge_forgotten(
                    ret_days * 86400.0)
        except Exception as ex:
            print("[hippocampus] relation purge error: " + repr(ex))
        # v1.35: fade profile-fact confidence for stale (long-unrefreshed) facts.
        try:
            if (bool(getattr(self.cfg, "profile_decay_enabled", True))
                    and getattr(self, "profile", None) is not None):
                out["profile_facts"] = self.decay_profile()
        except Exception as ex:
            print("[hippocampus] profile decay error: " + repr(ex))
        return out

    def _start_decay_loop(self) -> None:
        """Run run_memory_decay() on a fixed interval in a daemon thread.
        Default-on; disabled when memory_decay_enabled is False or the
        interval is <= 0."""
        if not bool(getattr(self.cfg, "memory_decay_enabled", True)):
            return
        interval = float(getattr(self.cfg, "memory_decay_interval_seconds", 1800.0) or 0.0)
        if interval <= 0:
            return
        if self._decay_thread is not None and self._decay_thread.is_alive():
            return
        import threading as _threading
        self._decay_stop = _threading.Event()
        stop = self._decay_stop

        def _runner():
            # Wait first, then sweep: startup already did a one-shot tier sweep,
            # and a cold DB needs no immediate decay.
            while not stop.wait(interval):
                try:
                    self.run_memory_decay()
                except Exception as ex:
                    print("[hippocampus] decay loop iteration error: " + repr(ex))

        self._decay_thread = _threading.Thread(
            target=_runner, name="hippocampus-decay-maint", daemon=True)
        self._decay_thread.start()

    def _stop_decay_loop(self) -> None:
        stop = getattr(self, "_decay_stop", None)
        if stop is not None:
            try:
                stop.set()
            except Exception:
                pass
        thread = getattr(self, "_decay_thread", None)
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass
        self._decay_thread = None
        self._decay_stop = None

    def reclassify_tiers(self) -> dict:
        """v1.13: recompute + persist hot/warm/cold for every engram.
        Non-destructive (no deletes). Returns a count dict, or {} when
        tiering is disabled."""
        if not getattr(self.cfg, "tiering_enabled", False):
            return {}
        from .tiering import TieringEngine
        try:
            return TieringEngine(self.store, self.cfg).reclassify_all()
        except Exception as ex:
            print("[hippocampus] reclassify_tiers error: " + repr(ex))
            return {}

    def archive_cold(self, *, min_age_days=None) -> dict:
        """v1.14: physically archive cold-tier engrams to a compressed file
        and evict them from the live DB. Explicit / destructive to live rows
        (data moves to the archive). Returns the archiver result dict, or {}
        when tiering is disabled."""
        if not getattr(self.cfg, "tiering_enabled", False):
            return {}
        from .cold_archive import ColdArchiver
        try:
            return ColdArchiver(self.store, self.cfg).archive_cold(
                min_age_days=min_age_days)
        except Exception as ex:
            print("[hippocampus] archive_cold error: " + repr(ex))
            return {"archived": 0, "error": repr(ex)}

    # ---------- v1.1+ delegations to profile / activation / consolidator ----------
    def build_profile(self, actor_id):
        if self.profile is None or self.semantic is None:
            return []
        return self.profile.build_from_relations(
            actor_id, self.semantic, self.store, self.cfg)

    def profile_facts(self, actor_id, *, predicate=None, limit=200):
        if self.profile is None:
            return []
        return self.profile.facts_for(actor_id, predicate=predicate, limit=limit)

    def build_persona(self, actor_id):
        """Summarize a speaker's recent engrams into a natural-language
        persona via the encoder LLM, and store it. Returns the Persona, or
        None when persona is disabled / there is nothing to summarize / the
        LLM is the rule fallback (which returns "")."""
        if self.persona_store is None or not actor_id:
            return None
        cap = int(getattr(self.cfg, "persona_max_source", 20) or 20)
        rows = [e for e in self.store.all(limit=5000)
                if (e.actor_id or "") == actor_id]
        rows = rows[:max(1, cap)]
        if not rows:
            return None
        platform = ""
        for e in rows:
            if getattr(e, "platform", ""):
                platform = e.platform
                break
        lines = []
        for e in rows:
            txt = (e.summary or e.content or "").strip()
            if txt:
                lines.append("- " + txt.replace("\n", " "))
        if not lines:
            return None
        corpus = "\n".join(lines)
        system = (
            "你是一个用户画像助手。基于该用户最近的消息，输出一个 JSON 对象："
            "{\"summary\": \"一段简洁客观的中文画像，概括稳定偏好/身份/行为，"
            "120 字以内\", \"tags\": [\"3 到 5 个最能概括该用户的关键词\"]}。"
            "只输出 JSON，不要额外文字。")
        try:
            raw = self.llm.chat(system, corpus, max_tokens=320) or ""
        except Exception as ex:
            print("[hippocampus] build_persona llm error: " + repr(ex))
            raw = ""
        summary, tags = self._parse_persona_output(raw)
        if not summary:
            return None
        from .quality import check_summary
        warn = check_summary(summary, label="persona")
        if warn:
            print(warn + " actor=" + str(actor_id))
        persona = Persona(actor_id=actor_id, summary=summary, tags=tags,
                          platform=platform, source_count=len(rows))
        return self.persona_store.upsert(persona)

    @staticmethod
    def _parse_persona_output(raw):
        """Parse the persona LLM output into (summary, tags). Accepts a JSON
        object {summary, tags}; falls back to treating the whole text as the
        summary (with empty tags) when it is not valid JSON."""
        import json as _json
        text = (raw or "").strip()
        if not text:
            return "", []
        # Strip ```json fences if present.
        if text.startswith("```"):
            text = text.strip("`")
            nl = text.find("\n")
            if nl != -1 and text[:nl].strip().lower() in ("json", ""):
                text = text[nl + 1:]
        # Try to isolate the first {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            blob = text[start:end + 1]
            try:
                obj = _json.loads(blob)
                summary = str(obj.get("summary", "")).strip()
                tags_raw = obj.get("tags", [])
                tags = []
                if isinstance(tags_raw, list):
                    tags = [str(t).strip() for t in tags_raw if str(t).strip()][:5]
                if summary:
                    return summary, tags
            except Exception:
                pass
        # Fallback: whole text is the summary.
        return text, []

    def get_persona(self, actor_id):
        if self.persona_store is None or not actor_id:
            return None
        return self.persona_store.get(actor_id)

    def decay_profile(self, actor_id=None):
        if self.profile is None:
            return 0
        return self.profile.decay_facts(actor_id, self.cfg)

    def spread_activation(self, seeds, *, depth=None, decay=None, floor=None):
        if self.activation is None:
            return {}
        try:
            return self.activation.activate(seeds, depth=depth, decay=decay, floor=floor)
        except Exception:
            return {}

    def recall_with_activation(self, cue, *, seeds=None):
        res = self.recall(cue)
        if self.activation is None or not res.engrams:
            return res
        # Explicit seeds win; otherwise fall back to cue.topics for backward compat.
        seed_list = list(seeds) if seeds is not None else list(getattr(cue, "topics", None) or [])
        try:
            acts = self.activation.activate(seed_list)
            engram_acts = self.activation.engram_activation(acts)
            for i, e in enumerate(res.engrams):
                a = float(engram_acts.get(e.id, 0.0))
                res.scores[i] = res.scores[i] + self.cfg.activation_score_weight * a
        except Exception:
            pass
        pairs = sorted(zip(res.engrams, res.scores), key=lambda p: p[1], reverse=True)
        if pairs:
            res.engrams, res.scores = list(zip(*pairs))
            res.engrams = list(res.engrams)
            res.scores = list(res.scores)
        return res

    def force_consolidate(self):
        try:
            return self.consolidator.step()
        except Exception:
            return {}

    # ---------- shutdown ----------
    def close(self) -> None:
        try:
            self._stop_decay_loop()
        except Exception:
            pass
        for name in ("store", "semantic", "atom_store", "graph_store",
                     "prospective_store", "profile", "persona_store",
                     "relation_store", "diary_store"):
            obj = getattr(self, name, None)
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass
        t = getattr(self, "_atom_task", None)
        if t is not None and not t.done():
            try:
                t.cancel()
            except Exception:
                pass
            self._atom_task = None
        import gc as _gc
        _gc.collect()
