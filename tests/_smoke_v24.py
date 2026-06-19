"""Smoke v1.4.x: B8 - i18n backend + help_text translation.

Scope:
- hippocampus.i18n_backend loads hippocampus/i18n/{zh,en}.json
- t(key) walks dot-path, falls back zh -> [i18n-missing:key]
- t(key, **kwargs) formats template
- t_list(key) returns list, [] on missing
- init(language) supports zh + en; unknown -> zh
- ConfigManager.LABELS (B7) is merged into both langs at init time
  under the synthetic `config.<field_name>` namespace
- handlers/help_text.py: HELP_TEXT is a string (back-compat) +
  get_help_text() returns the current language translation

Tests:
- init(zh) -> t(help.full_text) contains 中文 HELP_TEXT marker
- init(en) -> t(help.full_text) contains English HELP_TEXT marker
- t format kwargs work
- config.<field> resolves to LABELS for both langs
- missing key returns [i18n-missing:...] sentinel
- init(unknown_lang) falls back to zh silently
- t_list returns list
- help_text module: HELP_TEXT still importable as string
- get_help_text() respects init() state
- deep-copy isolation: switching init(zh)->init(en)->init(zh) does not
  pollute earlier language
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import i18n_backend as ib
from hippocampus.i18n_backend import (
    init, t, t_list, current_language, SUPPORTED_LANGS,
)


def banner(t_): print(chr(10) + "=== " + t_ + " ===")


def test_supported_langs():
    banner("SUPPORTED_LANGS is exactly (zh, en)")
    assert SUPPORTED_LANGS == ("zh", "en"), SUPPORTED_LANGS
    print("  SUPPORTED_LANGS=(zh, en): OK")


def test_init_zh_loads_chinese_help():
    banner("init(zh) -> t(help.full_text) contains Chinese marker")
    init("zh")
    assert current_language() == "zh"
    s = t("help.full_text")
    # Chinese HELP_TEXT must contain a known Chinese phrase
    assert "召回" in s, f"zh help missing 召回: {s[:80]!r}"
    print("  zh help contains 召回: OK")


def test_init_en_loads_english_help():
    banner("init(en) -> t(help.full_text) contains English marker")
    init("en")
    assert current_language() == "en"
    s = t("help.full_text")
    # English HELP_TEXT must contain a known English phrase
    assert "recall related memories" in s, (
        f"en help missing English marker: {s[:80]!r}")
    print("  en help contains 'recall related memories': OK")


def test_format_kwargs():
    banner("t(key, **kwargs) formats templates")
    init("en")
    s = t("replay.ok", m=1, p=2, a=3, r=4)
    assert s == "replay: merged=1 promoted=2 archived=3 replayed=4", s
    s2 = t("recall.no_hit", mode="dual", query="foo")
    assert s2 == "[dual] no hit for: foo", s2
    print("  replay.ok + recall.no_hit format: OK")


def test_config_labels_resolve_for_both_langs():
    banner("config.<field> resolves from ConfigManager.LABELS")
    init("zh")
    assert t("config.embedding_dim") == "向量维度"
    init("en")
    assert t("config.embedding_dim") == "Embedding dimension"
    print("  config.embedding_dim zh/en: OK")


def test_missing_key_returns_sentinel():
    banner("missing key returns [i18n-missing:...] sentinel")
    init("en")
    s = t("this.key.does.not.exist")
    assert s == "[i18n-missing:this.key.does.not.exist]", s
    print("  sentinel for missing: OK")


def test_unknown_lang_falls_back_to_zh():
    banner("init(unknown) falls back to zh silently")
    init("ja")
    assert current_language() == "zh"
    # zh help marker should still be there
    assert "召回" in t("help.full_text")
    print("  unknown lang -> zh: OK")


def test_t_list_returns_list():
    banner("t_list returns list, [] on missing")
    init("en")
    # we use a string key; t_list wraps it in a 1-element list
    out = t_list("recall.related_item")
    assert isinstance(out, list), out
    assert len(out) == 1 and "{summary}" in out[0], out
    # missing -> []
    assert t_list("nope.nada.nothing") == []
    print("  t_list behavior: OK")


def test_help_text_module_still_importable():
    banner("handlers/help_text.py: HELP_TEXT still importable as string")
    # Load help_text.py directly to avoid triggering handlers/__init__.py
    # (which pulls in format.py and the astrbot.api stub chain).
    import importlib.util as _ilu
    import os as _os
    _ht_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "handlers", "help_text.py")
    _spec = _ilu.spec_from_file_location("help_text_under_test", _ht_path)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    HELP_TEXT = _mod.HELP_TEXT
    get_help_text = _mod.get_help_text
    assert isinstance(HELP_TEXT, str)
    # HELP_TEXT is the default (zh) loaded eagerly at import
    init("zh")
    zh = get_help_text()
    init("en")
    en = get_help_text()
    assert zh != en, "zh and en help should differ"
    assert "召回" in zh
    assert "recall related memories" in en
    print("  HELP_TEXT + get_help_text(): OK")


def test_deep_copy_isolation():
    banner("init(zh)->init(en)->init(zh) does not pollute earlier")
    init("zh")
    zh_emb = t("config.embedding_dim")
    assert zh_emb == "向量维度"
    init("en")
    en_emb = t("config.embedding_dim")
    assert en_emb == "Embedding dimension"
    # switch back; zh must still be its own
    init("zh")
    zh_emb2 = t("config.embedding_dim")
    assert zh_emb2 == "向量维度", (
        f"zh label got polluted by en: {zh_emb2!r}")
    print("  zh/en/zh round-trip preserves both labels: OK")


def main():
    test_supported_langs()
    test_init_zh_loads_chinese_help()
    test_init_en_loads_english_help()
    test_format_kwargs()
    test_config_labels_resolve_for_both_langs()
    test_missing_key_returns_sentinel()
    test_unknown_lang_falls_back_to_zh()
    test_t_list_returns_list()
    test_help_text_module_still_importable()
    test_deep_copy_isolation()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()
