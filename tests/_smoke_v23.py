"""Smoke v1.4.x: B7 - ConfigManager (field fallback / range / type / labels).

Scope:
- ConfigManager(raw_dict).memory_config returns a fully-populated MemoryConfig
  with type coercion, range validation, and i18n label extraction.
- The 14 fields historically exposed via _conf_schema.json still flow through
  (back-compat with PluginInitializer's prior hardcode path).
- Unknown fields land in MemoryConfig.extra (forward-compat with future
  config keys that B8+ may add before the registry is updated).
- LABELS is a public dict with 67 entries, each having {zh, en}.

Tests:
- empty dict -> all 67 fields at their MemoryConfig defaults; extra is {}
- 14-field legacy dict (the dict PluginInitializer used to consume) -> exact
  same MemoryConfig that the old hardcode path produced
- type coercion: int / float / bool from strings; bool from "true"/"false"/"1"/"0"
- type-coercion failure: int from "six four" -> default + warn to stdout
- range check: embedding_dim=999999 -> default + warn
- range check passes: embedding_dim=128 stays
- extras: 2 unknown keys land in MemoryConfig.extra
- LABELS has 67 entries; embedding_dim / enable_session_filter / sqlite_path
  each have non-empty zh and en labels
- ConfigManager.get(key) returns the same value as getattr on memory_config
- None values in raw dict are treated as "use default" (no warn)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import contextlib
from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, LABELS, _FIELDS


def banner(t): print("\n=== " + t + " ===")


def test_registry_covers_all_memory_config_fields():
    banner("ConfigManager._FIELDS covers every MemoryConfig field (67)")
    import dataclasses
    cfg_fields = {f.name for f in dataclasses.fields(MemoryConfig)}
    registry_fields = set(_FIELDS.keys())
    assert cfg_fields == registry_fields, (
        f"missing in registry: {cfg_fields - registry_fields}\n"
        f"extra in registry:   {registry_fields - cfg_fields}"
    )
    assert len(registry_fields) == 85  # 67 base +5 backup +2 provider_id -4 openai +3 auto-inject +4 session-agg +3 persona +1 tokenizer +3 dedup +1 reltime
    print("  all 67 fields registered: OK")


def test_empty_dict_yields_all_defaults():
    banner("empty dict -> all 67 fields at MemoryConfig defaults")
    cm = ConfigManager({})
    cfg = cm.memory_config
    default = MemoryConfig()
    import dataclasses
    for f in dataclasses.fields(MemoryConfig):
        if f.name == "extra":
            assert getattr(cfg, f.name) == {}, f"extra should be {{}}"
        else:
            assert getattr(cfg, f.name) == getattr(default, f.name), (
                f"field {f.name!r}: {getattr(cfg, f.name)!r} "
                f"!= default {getattr(default, f.name)!r}"
            )
    print("  defaults applied: OK")


def test_legacy_14_field_dict_flows_through():
    banner("legacy 14-field dict (PluginInitializer v1.4 path) round-trip")
    raw = {
        "sqlite_path": "/tmp/test_legacy.db",
        "embedding_name": "hash",
        "llm_name": "rule",
        "embedding_dim": 128,
        "auto_rebuild_on_switch": False,
        "enable_semantic": True,
        "enable_prospective": False,
        "enable_promotion": True,
        "metamemory_enabled": False,
        "enable_episodic_semantic": True,
    }
    cm = ConfigManager(raw)
    cfg = cm.memory_config
    # every user-set field respected
    assert cfg.sqlite_path == "/tmp/test_legacy.db"
    assert cfg.embedding_name == "hash"
    assert cfg.llm_name == "rule"
    assert cfg.embedding_dim == 128
    assert cfg.auto_rebuild_on_switch is False
    assert cfg.enable_semantic is True
    assert cfg.enable_prospective is False
    assert cfg.enable_promotion is True
    assert cfg.metamemory_enabled is False
    assert cfg.enable_episodic_semantic is True
    # fields the user did NOT set still at MemoryConfig defaults
    assert cfg.pattern_separation_threshold == 0.92
    assert cfg.working_memory_capacity == 32
    assert cfg.decay_tau_base == 60 * 60 * 24 * 7.0
    print("  14-field legacy dict produces correct MemoryConfig: OK")


def test_type_coercion_int_float_bool_from_strings():
    banner("type coercion: int / float / bool parsed from string values")
    raw = {
        "embedding_dim": "256",            # str -> int
        "decay_floor": "0.1",              # str -> float
        "enable_session_filter": "true",   # str "true" -> bool True
        "auto_rebuild_on_switch": "off",   # str "off" -> bool False
        "metamemory_enabled": "1",         # str "1" -> bool True
    }
    cm = ConfigManager(raw)
    cfg = cm.memory_config
    assert cfg.embedding_dim == 256 and isinstance(cfg.embedding_dim, int)
    assert cfg.decay_floor == 0.1 and isinstance(cfg.decay_floor, float)
    assert cfg.enable_session_filter is True
    assert cfg.auto_rebuild_on_switch is False
    assert cfg.metamemory_enabled is True
    print("  int/float/bool coercion from strings: OK")


def test_type_coercion_failure_falls_back_with_warn():
    banner("type coercion failure: int from 'six four' -> default + warn")
    buf = io.StringIO()
    raw = {"embedding_dim": "six four", "enable_semantic": "definitely"}
    with contextlib.redirect_stdout(buf):
        cm = ConfigManager(raw)
    cfg = cm.memory_config
    assert cfg.embedding_dim == 64, f"expected default 64, got {cfg.embedding_dim}"
    assert cfg.enable_semantic is True
    out = buf.getvalue()
    assert "embedding_dim" in out and "type coercion" in out
    assert "enable_semantic" in out
    print("  invalid types fall back with warn: OK")


def test_range_check_falls_back_with_warn():
    banner("range check: embedding_dim=999999 out of [16, 4096] -> default + warn")
    buf = io.StringIO()
    raw = {"embedding_dim": 999999, "recall_candidate_k": -5}
    with contextlib.redirect_stdout(buf):
        cm = ConfigManager(raw)
    cfg = cm.memory_config
    assert cfg.embedding_dim == 64
    assert cfg.recall_candidate_k == 50
    out = buf.getvalue()
    assert "out of range" in out
    assert "embedding_dim" in out
    assert "recall_candidate_k" in out
    print("  out-of-range falls back with warn: OK")


def test_range_check_accepts_in_range_values():
    banner("range check accepts in-range values without warn")
    buf = io.StringIO()
    raw = {"embedding_dim": 128, "recall_candidate_k": 100,
           "pattern_separation_threshold": 0.85}
    with contextlib.redirect_stdout(buf):
        cm = ConfigManager(raw)
    cfg = cm.memory_config
    assert cfg.embedding_dim == 128
    assert cfg.recall_candidate_k == 100
    assert cfg.pattern_separation_threshold == 0.85
    assert buf.getvalue() == "", f"unexpected warn: {buf.getvalue()!r}"
    print("  in-range values preserved silently: OK")


def test_extras_collected_into_memory_config_extra():
    banner("unknown fields land in MemoryConfig.extra (forward-compat)")
    raw = {
        "sqlite_path": "/x/y.db",
        "future_field_1": 42,
        "future_field_2": "hello",
    }
    cm = ConfigManager(raw)
    cfg = cm.memory_config
    assert cfg.extra == {"future_field_1": 42, "future_field_2": "hello"}, cfg.extra
    print("  extras preserved: OK")


def test_labels_have_zh_and_en_for_every_field():
    banner("LABELS has zh + en for every field; spot-check 3 well-known fields")
    assert len(LABELS) == 85  # 67 base +5 backup +2 provider_id -4 openai +3 auto-inject +4 session-agg +3 persona +1 tokenizer +3 dedup +1 reltime
    for fname, lab in LABELS.items():
        assert "zh" in lab and "en" in lab
        assert lab["zh"] and lab["en"], f"{fname}: empty label"
    # spot-check well-known fields
    assert LABELS["embedding_dim"]["zh"] == "向量维度"
    assert LABELS["embedding_dim"]["en"] == "Embedding dimension"
    assert LABELS["enable_session_filter"]["en"].startswith("Enable ")
    assert LABELS["sqlite_path"]["zh"] == "SQLite 存储路径"
    print("  67/67 labels present + 3 spot-checks pass: OK")


def test_get_matches_getattr_on_memory_config():
    banner("ConfigManager.get(key) returns same value as getattr on memory_config")
    cm = ConfigManager({"embedding_dim": 256, "enable_session_filter": True})
    assert cm.get("embedding_dim") == 256
    assert cm.get("enable_session_filter") is True
    assert cm.get("sqlite_path") == "data/hippocampus.db"  # default
    assert cm.get("nonexistent_field", "sentinel") == "sentinel"
    print("  get/getattr consistency: OK")


def test_none_value_treated_as_default():
    banner("None value in raw dict -> use default (no warn)")
    buf = io.StringIO()
    raw = {"embedding_dim": None, "enable_semantic": None}
    with contextlib.redirect_stdout(buf):
        cm = ConfigManager(raw)
    cfg = cm.memory_config
    assert cfg.embedding_dim == 64
    assert cfg.enable_semantic is True
    assert buf.getvalue() == "", f"unexpected warn on None: {buf.getvalue()!r}"
    print("  None treated as missing (silent default): OK")


def main():
    test_registry_covers_all_memory_config_fields()
    test_empty_dict_yields_all_defaults()
    test_legacy_14_field_dict_flows_through()
    test_type_coercion_int_float_bool_from_strings()
    test_type_coercion_failure_falls_back_with_warn()
    test_range_check_falls_back_with_warn()
    test_range_check_accepts_in_range_values()
    test_extras_collected_into_memory_config_extra()
    test_labels_have_zh_and_en_for_every_field()
    test_get_matches_getattr_on_memory_config()
    test_none_value_treated_as_default()
    print("\nALL OK")


if __name__ == "__main__":
    main()
