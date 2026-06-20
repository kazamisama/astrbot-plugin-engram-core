"""Smoke v1.12: relative-time labels in auto-inject.

Covers:
  - reltime.relative_label boundaries (刚刚 / 分钟 / 小时 / 天 / 月 / 年)
    + empty for missing / non-positive; future clamps to 刚刚
  - config field auto_inject_relative_time registered, default True
  - the 6 flipped defaults are in effect
"""
import sys, os


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.reltime import relative_label
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS


def banner(m):
    print(chr(10) + "=== " + m + " ===")


def test_relative_label():
    banner("relative_label boundaries")
    n = 1_000_000_000.0
    assert relative_label(n - 10, n) == "刚刚"
    assert relative_label(n - 120, n) == "2 分钟前"
    assert relative_label(n - 3 * 3600, n) == "3 小时前"
    assert relative_label(n - 2 * 86400, n) == "2 天前"
    assert relative_label(n - 40 * 86400, n) == "1 个月前"
    assert relative_label(n - 400 * 86400, n) == "1 年前"
    assert relative_label(0, n) == ""
    assert relative_label(None, n) == ""
    assert relative_label(n + 50, n) == "刚刚"
    print("  boundaries OK")


def test_config_field_and_defaults():
    banner("auto_inject_relative_time field + flipped defaults")
    assert "auto_inject_relative_time" in _FIELDS
    assert "auto_inject_relative_time" in LABELS
    cfg = ConfigManager({}).memory_config
    assert cfg.auto_inject_relative_time is True
    # the 6 flipped defaults
    assert cfg.auto_inject_enabled is True
    assert cfg.session_aggregate_enabled is True
    assert cfg.session_aggregate_min_chars == 0
    assert cfg.tokenizer_mode == "jieba"
    assert cfg.dedup_enabled is True
    print("  field + defaults OK")


def main():
    test_relative_label()
    test_config_field_and_defaults()
    print(chr(10) + "v1.12 smoke: ALL PASS")


if __name__ == "__main__":
    main()
