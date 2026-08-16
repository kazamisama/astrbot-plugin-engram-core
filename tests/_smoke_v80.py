"""Smoke v1.76.7: review-fix regression batch."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryConfig, MemoryService, Cue
from hippocampus.types import Engram


def _service(tmp, **over):
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "h.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    for k, v in over.items():
        setattr(cfg, k, v)
    return MemoryService(cfg)


def test_graph_route_partition_filters():
    svc = _service(tempfile.mkdtemp())
    from hippocampus.retrieval.graph_retriever import GraphRetriever
    gr = GraphRetriever(service=svc)
    cue = Cue(text="x", persona_id="p1", scope_id="s1", actor_id="u1",
              channel_id="g1")
    ok = Engram(id="ok", persona_id="p1", scope_id="s1", actor_id="u1",
                channel_id="g1", forgotten_at=0.0)
    assert gr._passes_filters(ok, cue)
    typed_cue = Cue(text="x", persona_id="p1", scope_id="s1", actor_id="u1",
                     channel_id="g1", memory_types=["episodic"])
    assert not gr._passes_filters(
        Engram(id="d", persona_id="p1", scope_id="s1", actor_id="u1",
               channel_id="g1", memory_type="diary"), typed_cue)
    for bad in (
        Engram(id="a", persona_id="p2", scope_id="s1", actor_id="u1", channel_id="g1"),
        Engram(id="b", persona_id="p1", scope_id="s2", actor_id="u1", channel_id="g1"),
        Engram(id="c", persona_id="p1", scope_id="s1", actor_id="u1", channel_id="g1", forgotten_at=time.time()),
    ):
        assert not gr._passes_filters(bad, cue), bad.id
    svc.close()
    print("R1 graph route filters: OK")


def test_scope_identity_prefers_id_and_consolidation_is_safe():
    from hippocampus.memory_scope import resolve_event_identity
    class Ev:
        def get_sender_id(self): return "u123"
        def get_sender_name(self): return "Nick"
        def get_platform_name(self): return "qq"
    assert resolve_event_identity(MemoryConfig(), Ev()) == "u123"

    svc = _service(tempfile.mkdtemp(), memory_consolidation_enabled=True,
                   memory_consolidation_min_age_days=0,
                   memory_consolidation_min_memories_per_group=3,
                   memory_consolidation_max_importance=1.0)
    old = time.time() - 86400
    ids = []
    for i in range(3):
        e = Engram(id=f"old{i}", actor_id="u", platform="test", channel_id="g",
                   session_id="s", persona_id="p", scope_id="sc",
                   content=f"old {i}", summary=f"old {i}",
                   importance=0.2, created_at=old, embedding_model=svc._current_embedding_name)
        e.embedding = svc.embedder.embed(e.content)
        svc.store.upsert(e)
        ids.append(e.id)
    res = svc.run_memory_consolidation(force=True)
    # RuleLLM cannot merge; originals must survive.
    assert res["failed"] >= 1, res
    assert all(svc.store.get(eid) is not None for eid in ids)
    svc.close()
    print("R2/R3 identity + consolidation safety: OK")


def test_prompt_override_namespace_isolation():
    svc1 = _service(tempfile.mkdtemp())
    svc2 = _service(tempfile.mkdtemp())
    assert svc1.set_prompt("summary_system", "CUSTOM-A")
    from hippocampus.prompts import get_prompt
    assert get_prompt("summary_system", namespace=svc1._prompt_namespace) == "CUSTOM-A"
    from hippocampus.prompts import BUILTIN_PROMPTS
    assert get_prompt("summary_system", namespace=svc2._prompt_namespace) == BUILTIN_PROMPTS["summary_system"]
    svc1.close(); svc2.close()
    print("R5 prompt namespace isolation: OK")


def main():
    test_graph_route_partition_filters()
    test_scope_identity_prefers_id_and_consolidation_is_safe()
    test_prompt_override_namespace_isolation()
    print("\nv1.76.7 smoke: ALL PASS")


if __name__ == "__main__":
    main()
