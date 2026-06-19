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
        self.store = HippocampalStore(self.cfg.sqlite_path, self.embedder)
        self.encoder = EngramEncoder(self.embedder, llm=self.llm, cfg=self.cfg)
        self.separator = PatternSeparator(self.cfg)
        self.working = WorkingMemory(self.cfg)
        self.reconsolidator = Reconsolidator(self.store, self.cfg)
        self.completer = PatternCompleter(self.store, self.embedder, self.cfg, self.reconsolidator)
        self.consolidator = ReplayConsolidator(self.store, self.cfg, llm=self.llm)
        self.semantic = SemanticStore(self.cfg.sqlite_path) if self.cfg.enable_semantic else None
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
                channel_id: str, content: str) -> Engram:
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
            platform=platform, channel_id=channel_id, content=content)
        e.embedding_model = self._current_embedding_name
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

    def _post_ingest(self, e: Engram) -> None:
        if self.semantic is not None and self.extractor is not None:
            from .semantic import _classify
            ents = self.extractor.extract_entities(e)
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
            "你是一个用户画像助手。基于该用户最近的消息，用中文写一段简洁、"
            "客观的用户画像，概括其稳定的偏好、身份特征与行为习惯。只输出画像"
            "正文，不要前缀、不要罗列原文。控制在 120 字以内。")
        try:
            summary = self.llm.chat(system, corpus, max_tokens=256) or ""
        except Exception as ex:
            print("[hippocampus] build_persona llm error: " + repr(ex))
            summary = ""
        summary = summary.strip()
        if not summary:
            return None
        persona = Persona(actor_id=actor_id, summary=summary,
                          platform=platform, source_count=len(rows))
        return self.persona_store.upsert(persona)

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

        for name in ("store", "semantic", "atom_store", "graph_store",
                     "prospective_store", "profile", "persona_store"):
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
