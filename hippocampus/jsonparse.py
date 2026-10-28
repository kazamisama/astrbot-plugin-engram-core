"""Loose JSON helpers shared by the LLM-output parsers (v1.77.0).

Both hippocampus/diary_writer.py and hippocampus/summarizer.py carried an
identical private copy of these two functions -- the same bug had to be
fixed twice whenever the model wrapped its JSON in a fenced block. They
now live here, and the copies are gone.

parse_json_loose() accepts, in order:
  1. a fenced code block anywhere in the text (```json ... ```),
  2. the whole trimmed reply,
  3. the widest {...} span in the reply.
It returns None when none of them parses, which callers treat as "the
model did not produce JSON".
"""
from __future__ import annotations
import json
import re
from hippocampus.observability import note as _obs_note

_FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n?(.*?)```", re.S)
_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_json_loose(raw: str):
    text = (raw or "").strip()
    if not text:
        return None
    candidates = []
    m = _FENCE.search(text)
    if m:
        inner = m.group(1).strip()
        candidates.append(inner)
        text = inner
    candidates.append(text)
    obj = _OBJECT.search(text)
    if obj:
        candidates.append(obj.group(0))
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            _obs_note("jsonparse", "parse_json_loose")
            continue
    return None


def as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None]
    return [v]
