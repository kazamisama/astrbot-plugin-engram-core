"""Smoke v1.77.0: swallowed exceptions stay visible (C7 guard).

Why this test exists
--------------------
The audit found 92 except blocks whose entire body was pass or continue.
Each one is a place where a real failure (a dead graph indexer, a diary
flush that never happens, a persona lookup raising every turn) looks
exactly like "that feature is quiet". They now all call
hippocampus.observability.note(), which prints one line to stderr per
(call site, exception type) and never re-raises.

Tests:
- note() prints exactly one line for repeated failures at the same site,
  a second line for a different site, and never raises
- every BROAD except handler (Exception / BaseException / bare) in
  production code whose body is only pass or continue calls _obs_note, so
  the pattern cannot silently come back. Typed handlers are exempt: a
  handler for one specific expected failure (missing optional table, a
  parse of user-supplied JSON) documents its assumption by naming it.
- note() survives an exception with an enormous message (truncated) and
  keeps working after reset()
"""
import ast
import glob
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hippocampus.observability import note, reset, seen_count


def banner(t):
    print(chr(10) + "=== " + t + " ===")


def test_note_dedupes_per_site():
    banner("note(): one line per site, never raises")
    import contextlib
    reset()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        for _ in range(5):
            try:
                raise ValueError("boom")
            except Exception:
                note("demo", "same_site")
        try:
            raise ValueError("boom")
        except Exception:
            note("demo", "other_site")
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2, lines
    assert "demo" in lines[0] and "ValueError" in lines[0], lines[0]
    assert seen_count() == 2, seen_count()

    # An exception outside any except block is still recorded, not raised.
    with contextlib.redirect_stderr(io.StringIO()):
        note("demo", "no_active_exception")
    assert seen_count() == 3

    # A huge message must not blow up the formatter.
    reset()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            raise RuntimeError("x" * 5000)
        except Exception:
            note("demo", "big")
    out = buf.getvalue()
    assert len(out) < 1000, len(out)
    assert out.endswith("...\n") or "..." in out, out[-60:]
    reset()
    print("  dedupe + truncation + no-raise: OK")


def test_no_silent_except_handlers():
    banner("no BROAD except handler is silent (C7 guard)")
    paths = (glob.glob(os.path.join(ROOT, "hippocampus", "**", "*.py"), recursive=True)
             + glob.glob(os.path.join(ROOT, "handlers", "**", "*.py"), recursive=True))
    silent = []
    for path in paths:
        if os.path.basename(path) == "observability.py":
            # note() itself cannot log its own logging failure; that one
            # except is the only legitimate silent one in the tree.
            continue
        with io.open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if len(node.body) != 1:
                continue
            if not isinstance(node.body[0], (ast.Pass, ast.Continue)):
                continue
            if node.type is None:
                broad = True
            elif isinstance(node.type, ast.Name):
                broad = node.type.id in ("Exception", "BaseException")
            else:
                broad = False
            if broad:
                silent.append((os.path.relpath(path, ROOT), node.lineno))
    assert not silent, (
        "silent except handlers (call observability.note instead): "
        + repr(silent[:12]))
    print("  " + str(len(paths)) + " modules scanned, 0 silent handlers: OK")


def main():
    test_note_dedupes_per_site()
    test_no_silent_except_handlers()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()
