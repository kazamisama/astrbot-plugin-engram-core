"""Public cross-plugin coordination helpers for ``extra_user_content_parts``.

v1.73: extracted from ``InjectHandler._strip_prior_engram_blocks`` so that
other AstrBot plugins writing to ``req.extra_user_content_parts`` can run
the same re-injection defense engram-core has used since v1.67.2.

Usage (external plugin, e.g. xml_structured_output)::

    from engram_core_helpers import strip_injected_blocks

    strip_injected_blocks(parts_list, root_tag="xml-extra",
                          inner_labels=("memo-block",))
    parts_list.append(TextPart(text="<xml-extra ...>...</xml-extra>",
                               type="text").mark_as_temp())

Convention (see README "多插件注入协调"):
  1. every external plugin wraps its blocks in one unique XML root tag
     (do NOT reuse ``<engram-context>``);
  2. the root tag carries stable inner sub-labels used for the
     re-injection defense match;
  3. known external root tags are listed in the
     ``external_plugin_root_tags`` config whitelist for auditing.
"""
from __future__ import annotations


def _normalise_root_tag(root_tag: str) -> str:
    """Accept "xml-extra", "<xml-extra>" or "</xml-extra>" -> "xml-extra"."""
    tag = (root_tag or "").strip()
    if tag.endswith(">"):
        tag = tag[:-1]
    if tag.startswith("</"):
        tag = tag[2:]
    elif tag.startswith("<"):
        tag = tag[1:]
    return tag.strip()


def strip_injected_blocks(parts_list, *, root_tag, inner_labels=()) -> int:
    """Remove prior injected blocks matching (root_tag, inner_labels) in place.

    A part counts as a prior injected block when its ``.text`` (stripped):
      - opens with ``<root_tag>`` or ``<root_tag ...>`` (attributes allowed,
        e.g. ``<xml-extra scope="shirley" plugin="..." version="...">``),
      - closes with ``</root_tag>``,
      - and, when ``inner_labels`` is given, contains at least one of the
        labels.  Pass an empty tuple to match on the root tag alone (safe
        when the tag is unique to your plugin).

    Parts without a string ``.text`` attribute are always kept.

    Returns the number of parts removed.  Call this right before appending
    fresh blocks on every ``on_llm_request`` firing, otherwise your blocks
    accumulate linearly across turns and eventually dominate the LLM
    context window (the bug engram-core fixed in v1.67.2).
    """
    if not parts_list:
        return 0
    tag = _normalise_root_tag(root_tag)
    if not tag:
        return 0
    open_bare = "<" + tag + ">"
    open_attr = "<" + tag + " "
    close = "</" + tag + ">"
    labels = tuple(inner_labels or ())
    kept = []
    removed = 0
    for part in parts_list:
        text = getattr(part, "text", None)
        if not isinstance(text, str):
            kept.append(part)
            continue
        stripped = text.strip()
        if ((stripped.startswith(open_bare) or stripped.startswith(open_attr))
                and stripped.endswith(close)
                and (not labels or any(label in stripped for label in labels))):
            removed += 1
            continue
        kept.append(part)
    if removed:
        parts_list[:] = kept
    return removed