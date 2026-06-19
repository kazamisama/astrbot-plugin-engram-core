# -*- coding: utf-8 -*-
"""Slice main.py into handlers/* and rewrite main.py to the thin Star."""
from __future__ import annotations
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
HANDLERS = ROOT / "handlers"
HANDLERS.mkdir(exist_ok=True)


def read_lines() -> list:
    return MAIN.read_text(encoding="utf8").splitlines(keepends=False)


RANGES = {
    "_extract":          (22,  36),
    "render_stats":      (37,  77),
    "find_and_forget":   (78, 103),
    "emb_bridge":        (104, 163),
    "export":            (164, 200),
    "import":            (201, 248),
    "format_cluster":    (249, 282),
    "format_narrative":  (283, 323),
    "format_profile":    (324, 338),
    "format_activation": (339, 358),
    "format_graph":      (359, 391),
    "parse_search":      (392, 403),
    "format_confidence": (404, 423),
    "format_decaycurve": (424, 474),
    "banner":            (475, 490),
    "Star":              (492, 100000),
}


def slice_lines(lines, start, end):
    return lines[start - 1: end - 1]


def write_module(name, body, extra_header=""):
    text = "from __future__ import annotations\n" + extra_header + "\n".join(body) + "\n"
    (HANDLERS / (name + ".py")).write_text(text, encoding="utf8")
    print("  wrote handlers/" + name + ".py  (" + str(len(body)) + " body lines, " + str(len(text)) + " chars)")


def _eval_str_concat(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _eval_str_concat(node.left) + _eval_str_concat(node.right)
    return ast.unparse(node)


def extract_help_text(src_text):
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cmd_mem_help":
            for child in ast.walk(node):
                if isinstance(child, ast.Yield) and isinstance(child.value, ast.Call):
                    call = child.value
                    if getattr(call.func, "attr", "") == "plain_result" and call.args:
                        return _eval_str_concat(call.args[0])
    raise RuntimeError("help text not found")


def main():
    lines = read_lines()
    src_text = "\n".join(lines) + "\n"
    print("main.py: " + str(len(lines)) + " lines")

    fmt_extra = (
        "import json, math, time\n"
        "from typing import TYPE_CHECKING\n"
        "from astrbot.api.event import AstrMessageEvent\n"
        "from hippocampus import EXPORT_FORMAT_VERSION, __version__ as HIPPO_VERSION\n"
        "if TYPE_CHECKING:\n"
        "    from hippocampus import MemoryService\n"
    )
    fmt_body = []
    for fn in ("_extract", "banner", "render_stats", "find_and_forget",
               "export", "import", "format_cluster", "format_narrative",
               "format_profile", "format_activation", "format_graph",
               "parse_search", "format_confidence", "format_decaycurve"):
        s, e = RANGES[fn]
        fmt_body.extend(slice_lines(lines, s, e))
        fmt_body.append("")
    fmt_body.append("def format_stats(service):")
    fmt_body.append("    return render_stats(service)")
    fmt_body.append("")
    write_module("format", fmt_body, extra_header=fmt_extra)

    rb_extra = "import asyncio\n"
    write_module("recall", slice_lines(lines, *RANGES["emb_bridge"]), extra_header=rb_extra)

    init_text = (
        "from .format import (\n"
        "    _extract,\n"
        "    banner_text,\n"
        "    export_engrams,\n"
        "    find_and_forget,\n"
        "    format_activation,\n"
        "    format_cluster,\n"
        "    format_confidence,\n"
        "    format_decaycurve,\n"
        "    format_graph,\n"
        "    format_narrative,\n"
        "    format_profile,\n"
        "    format_stats,\n"
        "    import_engrams,\n"
        "    parse_search_args,\n"
        "    render_stats,\n"
        ")\n"
        "from .recall import emb_bridge_for_context\n"
        "from .help_text import HELP_TEXT\n"
        "\n"
        "__all__ = [\n"
        '    "_extract",\n'
        '    "banner_text",\n'
        '    "export_engrams",\n'
        '    "find_and_forget",\n'
        '    "format_activation",\n'
        '    "format_cluster",\n'
        '    "format_confidence",\n'
        '    "format_decaycurve",\n'
        '    "format_graph",\n'
        '    "format_narrative",\n'
        '    "format_profile",\n'
        '    "format_stats",\n'
        '    "import_engrams",\n'
        '    "parse_search_args",\n'
        '    "render_stats",\n'
        '    "emb_bridge_for_context",\n'
        '    "HELP_TEXT",\n'
        "]\n"
    )
    (HANDLERS / "__init__.py").write_text(init_text, encoding="utf8")
    print("  wrote handlers/__init__.py")

    help_text = extract_help_text(src_text)
    Q = chr(34)
    BS = chr(92)
    safe = help_text.replace(BS + BS, BS + BS + BS + BS)
    safe = safe.replace(Q + Q + Q, BS + Q + BS + Q + BS + Q)
    body = (
        Q + Q + Q + "Help text shown by /mem help. Extracted from main.py cmd_mem_help." + Q + Q + Q + chr(10)
        + "HELP_TEXT = " + Q + Q + Q
        + safe
        + Q + Q + Q + chr(10)
    )
    (HANDLERS / "help_text.py").write_text(body, encoding="utf8")
    print("  wrote handlers/help_text.py  (" + str(len(help_text)) + " chars)")

    # Rewrite main.py: header + Star class only
    Q3 = Q + Q + Q
    header_lines = [
        Q3 + "astrbot_plugin_engram entry.",
        "",
        "AstrBot loads via: from main import <registered class>",
        "so this file must be importable when astrbot.api is on path.",
        "Split v1.3: rendering helpers moved to handlers/ package; this file",
        "keeps only the Star class wiring commands to the service.",
        Q3,
        "from __future__ import annotations",
        "import asyncio",
        "import json",
        "import time",
        "from typing import Any",
        "",
        "from astrbot.api.star import Star, register, Context",
        "from astrbot.api.event import filter, AstrMessageEvent",
        "",
        "# core package lives next to this file (self-contained plugin layout)",
        "from hippocampus import (MemoryService, MemoryConfig, Cue,",
        "                         ProxyEmbeddingProvider, ProxyLLMProvider,",
        "                         __version__ as HIPPO_VERSION,",
        "                         EXPORT_FORMAT_VERSION)",
        "",
        "# rendering + tooling split out of this file",
        "from handlers import (",
        "    _extract,",
        "    banner_text,",
        "    emb_bridge_for_context,",
        "    export_engrams,",
        "    find_and_forget,",
        "    format_activation,",
        "    format_cluster,",
        "    format_confidence,",
        "    format_decaycurve,",
        "    format_graph,",
        "    format_narrative,",
        "    format_profile,",
        "    HELP_TEXT,",
        "    import_engrams,",
        "    parse_search_args,",
        "    render_stats,",
        ")",
        "",
    ]
    star_body = slice_lines(lines, *RANGES["Star"])
    new_star = []
    in_help = False
    for ln in star_body:
        if not in_help and "async def cmd_mem_help" in ln:
            new_star.append(ln)
            new_star.append("        yield event.plain_result(HELP_TEXT)")
            in_help = True
            continue
        if in_help:
            stripped = ln.strip()
            if stripped.startswith("@filter.command") or stripped.startswith("async def ") or stripped.startswith("def "):
                in_help = False
                new_star.append(ln)
            continue
        new_star.append(ln)
    new_main = "\n".join(header_lines) + "\n".join(new_star) + "\n"
    MAIN.write_text(new_main, encoding="utf8")
    print("  wrote main.py  (" + str(len(new_main)) + " chars, " + str(len(new_star)) + " star body lines)")


if __name__ == "__main__":
    main()