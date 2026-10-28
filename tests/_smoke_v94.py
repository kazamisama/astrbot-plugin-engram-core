"""Smoke v1.77.0: schema/config coherence + generated help.

Why this test exists
--------------------
Two defects motivated it, both of the same shape -- a hand-maintained
second copy of a fact that had silently drifted:

1. _conf_schema.json advertised memory_settings.diary_message_batch_size
   and diary_message_flush_interval_seconds, but neither was a
   MemoryConfig field, so ConfigManager dumped them into
   MemoryConfig.extra and every reader's getattr() fell back to a
   hard-coded default. The knobs did nothing.
2. /mem help was a hand-written blob (v1.4.x header, missing commands,
   a literal backslash-n). It is now generated from
   engram_command_registry plus i18n strings.

Tests:
- every leaf of _conf_schema.json is a MemoryConfig field, except
  bot_language (read raw by the initializer before MemoryConfig exists)
- every MemoryConfig field is exposed in _conf_schema.json, except the
  three internal ones (extra / embedding_name / llm_name). This is the
  C6 guard: adding a field without a UI entry now fails the suite
  instead of leaving a knob that is only reachable by hand-editing JSON
- every schema type is one AstrBot can build a default for (int / float /
  bool / string / text / list / file / object / template_list); "dict"
  and friends raise at plugin load, so a typo must not reach production
- the schema's own defaults, fed back through ConfigManager, reproduce
  MemoryConfig's defaults exactly (a wrong default in the JSON is caught
  here instead of silently shadowing the dataclass)
- every MemoryConfig field is exposed in _conf_schema.json, except the
  three internal ones (extra / embedding_name / llm_name) -- the C6 guard:
  adding a field without a UI entry now fails here instead of leaving a
  knob reachable only by hand-editing JSON
- every schema type is one AstrBot can build a default for; anything else
  (e.g. "dict") raises at plugin load, so a typo must not reach production
- the schema defaults, fed back through ConfigManager, reproduce
  MemoryConfig defaults exactly (a wrong default cannot silently shadow
  the dataclass any more)
- the two diary knobs round-trip through ConfigManager as real fields
  (value on the field, NOT in cfg.extra)
- every @filter.command in main.py has a router/help entry and vice versa
- every help command has usage + description in both zh and en
- the rendered help contains every command, the package version, and no
  literal backslash-n
"""
import sys, os, json, re, dataclasses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, _FIELDS
from engram_command_registry import COMMAND_TABLE, DIRECT_COMMANDS, help_command_names


def banner(t): print(chr(10) + "=== " + t + " ===")


def _schema_leaves(node, prefix=""):
    # A nested object whose key is a dict-valued config field
    # (metamemory_weights) is a VALUE, not a group: stop there, otherwise
    # its inner keys look like config fields of their own.
    out = []
    for key, value in node.items():
        if not isinstance(value, dict):
            continue
        typ = value.get("type")
        children = value.get("items")
        spec = _FIELDS.get(key)
        is_value_dict = spec is not None and spec.py_type is dict
        if typ == "object" and isinstance(children, dict) and not is_value_dict:
            out.extend(_schema_leaves(children, prefix + key + "."))
        else:
            out.append(prefix + key)
    return out


def test_schema_leaves_are_real_config_fields():
    banner("_conf_schema.json leaves are MemoryConfig fields (except bot_language)")
    with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    leaves = _schema_leaves(schema)
    assert leaves, "schema has no leaves?"
    fields = {f.name for f in dataclasses.fields(MemoryConfig)}
    # bot_language is consumed raw by PluginInitializer._init_service before
    # MemoryConfig is built (it selects the i18n language), by design.
    allowed = {"bot_language"}
    orphans = [k for k in leaves if k.split(".")[-1] not in fields | allowed]
    assert not orphans, "schema keys with no config field: " + repr(orphans)
    leaf_names = {k.split(".")[-1] for k in leaves}
    assert "diary_message_batch_size" in leaf_names
    assert "diary_message_flush_interval_seconds" in leaf_names
    print("  " + str(len(leaves)) + " schema leaves all backed by fields: OK")


# Fields that intentionally stay out of the UI:
#   extra            -- catch-all for unknown keys; not a user knob
#   embedding_name   -- runtime-resolved ("hash" until a provider answers)
#   llm_name         -- runtime-resolved ("rule" until a provider answers)
INTERNAL_FIELDS = {"extra", "embedding_name", "llm_name"}

SUPPORTED_SCHEMA_TYPES = {
    "int", "float", "bool", "string", "text", "list", "file",
    "object", "template_list",
}


def _load_schema():
    with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as f:
        return json.load(f)


def test_every_config_field_is_exposed():
    banner("every MemoryConfig field has a UI entry (C6 guard)")
    schema = _load_schema()
    leaf_names = {k.split(".")[-1] for k in _schema_leaves(schema)}
    fields = {f.name for f in dataclasses.fields(MemoryConfig)}
    missing = sorted(fields - leaf_names - INTERNAL_FIELDS)
    assert not missing, (
        "config fields with no _conf_schema.json entry: " + repr(missing)
        + " -- add them to _conf_schema.json (or to INTERNAL_FIELDS with a reason)")
    print("  " + str(len(leaf_names & fields)) + "/"
          + str(len(fields) - len(INTERNAL_FIELDS))
          + " fields exposed; internal: " + ", ".join(sorted(INTERNAL_FIELDS)))


def test_schema_types_are_astrbot_supported():
    banner("every schema type can be turned into a default by AstrBot")
    schema = _load_schema()

    def walk(node, path):
        typ = node.get("type")
        assert typ in SUPPORTED_SCHEMA_TYPES, (path, typ)
        if typ == "object":
            assert isinstance(node.get("items"), dict), path + ": object without items"
            for key, child in node["items"].items():
                walk(child, path + "." + key)

    for group, spec in schema.items():
        walk(spec, group)
    print("  types all legal: OK")


def _schema_defaults(schema):
    # Replica of AstrBotConfig._config_schema_to_default_config: object
    # recurses into items, everything else uses "default" (or the type map).
    type_map = {"int": 0, "float": 0.0, "bool": False, "string": "",
                "text": "", "list": [], "file": [], "object": {},
                "template_list": []}

    def _parse(spec, out):
        for key, node in spec.items():
            default = node["default"] if "default" in node else type_map[node["type"]]
            if node["type"] == "object":
                out[key] = {}
                _parse(node.get("items") or {}, out[key])
            else:
                out[key] = default

    out = {}
    _parse(schema, out)
    return out


def test_schema_defaults_match_memory_defaults():
    banner("schema defaults reproduce MemoryConfig defaults")
    schema = _load_schema()
    cfg = ConfigManager(_schema_defaults(schema)).memory_config
    base = MemoryConfig()
    bad = []
    for f in dataclasses.fields(MemoryConfig):
        if f.name in INTERNAL_FIELDS:
            continue  # extra collects bot_language by design; the two
            # *_name fields are runtime-resolved, not schema-backed
        got = getattr(cfg, f.name)
        want = getattr(base, f.name)
        if isinstance(got, float) and isinstance(want, float):
            if abs(got - want) > 1e-9:
                bad.append((f.name, got, want))
        elif got != want:
            bad.append((f.name, got, want))
    assert not bad, "schema default disagrees with MemoryConfig: " + repr(bad)
    print("  " + str(len(dataclasses.fields(MemoryConfig))) + " defaults agree: OK")


def test_diary_knobs_round_trip():
    banner("diary_message_batch_size / flush interval reach the config for real")
    assert "diary_message_batch_size" in _FIELDS
    assert "diary_message_flush_interval_seconds" in _FIELDS
    cm = ConfigManager({
        "diary_message_batch_size": 7,
        "diary_message_flush_interval_seconds": 12.5,
    })
    cfg = cm.memory_config
    assert cfg.diary_message_batch_size == 7, cfg.diary_message_batch_size
    assert cfg.diary_message_flush_interval_seconds == 12.5
    assert "diary_message_batch_size" not in cfg.extra, cfg.extra
    assert "diary_message_flush_interval_seconds" not in cfg.extra, cfg.extra
    print("  values land on the fields, extra is clean: OK")


def _main_py_commands():
    path = os.path.join(ROOT, "main.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    return set(re.findall(r'@filter\.command\("([^"]+)"\)', src))


def test_command_table_matches_decorators():
    banner("registry <-> @filter.command decorators (both directions)")
    declared = _main_py_commands()
    registered = set(COMMAND_TABLE) | set(DIRECT_COMMANDS)
    missing_in_registry = declared - registered
    missing_in_main = registered - declared
    assert not missing_in_registry, (
        "commands with a decorator but no router/help entry: "
        + repr(sorted(missing_in_registry)))
    assert not missing_in_main, (
        "commands in the registry but no @filter.command decorator: "
        + repr(sorted(missing_in_main)))
    print("  " + str(len(declared)) + " commands agree: OK")


def _load_help_module():
    import importlib.util as ilu
    path = os.path.join(ROOT, "handlers", "help_text.py")
    spec = ilu.spec_from_file_location("help_text_under_test_v94", path)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_help_is_complete_in_both_languages():
    banner("generated help: every command, both languages, no stale markers")
    from hippocampus.i18n_backend import init
    mod = _load_help_module()
    init("zh")
    zh = mod.get_help_text()
    init("en")
    en = mod.get_help_text()
    init("zh")

    for name in help_command_names():
        assert "/" + name in zh, "zh help missing /" + name
        assert "/" + name in en, "en help missing /" + name
        assert "_t(" not in zh and "[i18n-missing" not in zh, zh
        assert "[i18n-missing" not in en, en

    assert "\\n" not in zh and "\\n" not in en, "literal backslash-n in help"
    from hippocampus import __version__
    assert __version__ in zh and __version__ in en, "version missing from header"
    assert "--mode=vector|fts|hybrid|dual" in zh
    assert "recall related memories" in en
    assert zh != en
    print("  " + str(len(help_command_names())) + " commands rendered in zh + en: OK")


def main():
    test_schema_leaves_are_real_config_fields()
    test_every_config_field_is_exposed()
    test_schema_types_are_astrbot_supported()
    test_schema_defaults_match_memory_defaults()
    test_diary_knobs_round_trip()
    test_command_table_matches_decorators()
    test_help_is_complete_in_both_languages()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()
