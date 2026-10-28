"""One place for "we swallowed an exception on purpose".

Why this exists
---------------
The plugin is defensively written: most optional features are wrapped so
that a failure degrades the feature instead of breaking the reply. That
choice is right, but the implementation often discarded the exception
with a bare "except Exception: pass". A real bug in one of those paths
(the graph indexer silently dying, a diary flush failing, a persona
lookup raising every turn) looked exactly like "the feature is quiet".

The 2026-10-27 audit found 92 except-block bodies that were only
pass/continue. Every one of them is now routed through note(), which:

- prints a single line to stderr: [hippocampus:<scope>] <where>:
  <ExcType>: <message>
- prints each DISTINCT failure once per process. Hot paths hit the same
  expected failure on every message; without de-duplication this would
  spam the log and get filtered out mentally, which is the same as
  silence. A new failure still shows up immediately.
- never raises and never changes control flow -- callers still fall back
  exactly as before.

Scopes are the module stem (service, storage, dual_route, ...) and
"where" is the enclosing function, so the line points at the code that
gave up without a debugger.
"""

import sys

# Distinct (scope, where, type, message) tuples already reported. Capped:
# if a pathological caller raises a fresh message every time, de-duplication
# would otherwise grow without bound.
_SEEN: set = set()
_MAX_SEEN = 500
_MAX_MESSAGE = 300


def note(scope, where="", exc=None):
    """Report a swallowed exception once, then stay quiet.

    exc may be passed explicitly; when omitted the active exception
    (sys.exc_info) is used, which is what an "except ... as exc" block
    has. Called outside an except block with no exc, it records the fact
    that this fallback was taken without an exception attached.
    """
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        key = (scope, where, "")
        text = "[hippocampus:" + str(scope) + "] " + (str(where) or "?") + ": fallback taken"
    else:
        msg = str(exc)
        if len(msg) > _MAX_MESSAGE:
            msg = msg[:_MAX_MESSAGE] + "..."
        # De-duplicate per SITE and exception type, not per message: a
        # per-row loop that fails on every row usually differs only by the
        # row id embedded in the message, and logging 500 of those buries
        # the first one just as effectively as printing nothing.
        key = (scope, where, type(exc).__name__)
        text = ("[hippocampus:" + str(scope) + "] " + (str(where) or "?")
                + ": " + type(exc).__name__ + ": " + msg)
    if key in _SEEN:
        return
    if len(_SEEN) >= _MAX_SEEN:
        _SEEN.clear()
    _SEEN.add(key)
    try:
        print(text, file=sys.stderr, flush=True)
    except Exception:
        pass


def seen_count():
    """Number of distinct failures reported so far (tests use this)."""
    return len(_SEEN)


def reset():
    """Forget what was reported (tests use this)."""
    _SEEN.clear()
