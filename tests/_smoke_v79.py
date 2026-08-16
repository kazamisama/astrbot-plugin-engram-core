"""Smoke v1.76.6: memory scope, prompt registry, transfer surface."""
import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryConfig, MemoryService, Cue
from hippocampus.memory_scope import parse_identity_aliases, resolve_scope_id


def _service(tmp, **cfg_over):
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "h.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    return MemoryService(cfg)


def test_memory_scope_partitions_recall():
    svc = _service(tempfile.mkdtemp(), memory_scope_mode="user",
                   identity_aliases="qq:123=Alice\n")
    assert parse_identity_aliases("qq:123=Alice")["qq:123"] == "Alice"
    a = svc.observe(session_id="s1", actor_id="123", platform="qq",
                    channel_id="g1", content="scope alpha",
                    scope_id="scope:user:qq:alice")
    svc.observe(session_id="s2", actor_id="456", platform="qq",
                channel_id="g1", content="scope alpha",
                scope_id="scope:user:qq:bob")
    res = svc.recall(Cue(text="scope alpha", k=5,
                         scope_id="scope:user:qq:alice"))
    assert res.engrams and all(e.id == a.id for e in res.engrams)
    svc.close()
    print("I memory scope: OK")


def test_prompt_registry_persists_overrides():
    tmp = tempfile.mkdtemp()
    svc = _service(tmp)
    items = svc.list_prompts()
    assert any(p["name"] == "summary_system" for p in items)
    assert svc.set_prompt("summary_system", "CUSTOM-SYSTEM")
    svc.close()
    # reopen same db -> override restored into global registry
    svc2 = _service(tmp)
    from hippocampus.prompts import get_prompt
    assert get_prompt("summary_system", namespace=svc2._prompt_namespace) == "CUSTOM-SYSTEM"
    assert svc2.reset_prompt("summary_system")
    svc2.close()
    print("J prompt registry: OK")


def test_transfer_json_roundtrip_and_preview():
    tmp = tempfile.mkdtemp()
    svc = _service(tmp)
    svc.observe(session_id="s", actor_id="u", platform="test",
                channel_id="c", content="portable memory")
    from hippocampus.memory_transfer import (export_memory_json, import_memories,
                                             preview_import)
    content = export_memory_json(svc)
    payload = json.loads(content)
    assert payload["memory_count"] >= 1
    svc.close()
    # Import into a fresh DB to prove round-trip + existing-DB dedup.
    svc2 = _service(tempfile.mkdtemp())
    preview = preview_import(content, "json", service=svc2)
    assert preview["entries"] >= 1 and preview["errors"] == []
    result = import_memories(svc2, content, "json")
    assert result["imported"] >= 1, result
    result2 = import_memories(svc2, content, "json")
    assert result2["imported"] == 0 and result2["skipped"] >= 1, result2
    # derive_indexes=False path: write main table only, no embedding work.
    svc3 = _service(tempfile.mkdtemp())
    result3 = import_memories(svc3, content, "json", derive_indexes=False)
    assert result3["imported"] >= 1 and result3["embedded"] == 0, result3
    svc3.close()
    svc2.close()
    print("K transfer: OK")


def main():
    test_memory_scope_partitions_recall()
    test_prompt_registry_persists_overrides()
    test_transfer_json_roundtrip_and_preview()
    print("\nv1.76.6 smoke: ALL PASS")


if __name__ == "__main__":
    main()
