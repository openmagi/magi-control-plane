"""Owner-applied edits over a shared run view (range trim / hide / redact).

A share link is created from the WHOLE session; the owner then trims it from the
dashboard. Edits are stored separately and applied at read time over the already
public-redacted view, so they are non-destructive (the full export is retained
and edits can be widened again).

Edits shape (all optional)::

    {
      "range": [start, end],   # inclusive transcript-index window to keep
      "hidden": [int, ...],    # transcript indices to drop
      "redactions": [str, ...] # literal substrings to blank to [redacted]
    }

Pure + defensive: malformed edits degrade to a no-op, never raise.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["normalize_edits", "apply_share_edits", "REDACTION_PLACEHOLDER"]

REDACTION_PLACEHOLDER = "[redacted]"
_MAX_REDACTIONS = 50
_MAX_REDACTION_LEN = 200
_MAX_HIDDEN = 5000


def _short_tool(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return name.rsplit("__", 1)[-1] if "__" in name else name


def normalize_edits(raw: object) -> dict:
    """Coerce an untrusted edits object into a safe, bounded canonical form.

    Returns ``{}`` (no-op) for anything unparseable. ``range`` becomes a sorted
    ``[lo, hi]`` of non-negative ints (or absent); ``hidden`` a sorted unique
    int list; ``redactions`` a capped list of non-empty, length-bounded strings.
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict = {}

    rng = raw.get("range")
    if isinstance(rng, Sequence) and not isinstance(rng, str) and len(rng) == 2:
        a, b = rng[0], rng[1]
        if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool):
            lo, hi = (a, b) if a <= b else (b, a)
            out["range"] = [max(0, lo), max(0, hi)]

    hidden = raw.get("hidden")
    if isinstance(hidden, Sequence) and not isinstance(hidden, str):
        ints = sorted({h for h in hidden if isinstance(h, int) and not isinstance(h, bool) and h >= 0})
        if ints:
            out["hidden"] = ints[:_MAX_HIDDEN]

    reds = raw.get("redactions")
    if isinstance(reds, Sequence) and not isinstance(reds, str):
        clean = []
        for r in reds:
            if isinstance(r, str) and r.strip():
                clean.append(r[:_MAX_REDACTION_LEN])
            if len(clean) >= _MAX_REDACTIONS:
                break
        if clean:
            out["redactions"] = clean

    return out


def _is_visible(i: int, rng: list[int] | None, hidden: set[int]) -> bool:
    if i in hidden:
        return False
    if rng is not None and (i < rng[0] or i > rng[1]):
        return False
    return True


def _redact_str(value: str, terms: list[str]) -> str:
    for t in terms:
        if t and t in value:
            value = value.replace(t, REDACTION_PLACEHOLDER)
    return value


def _redact_deep(value: object, terms: list[str]) -> object:
    if isinstance(value, str):
        return _redact_str(value, terms)
    if isinstance(value, Mapping):
        return {k: _redact_deep(v, terms) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_deep(v, terms) for v in value]
    return value


def apply_share_edits(view: Mapping[str, object], edits: object) -> dict:
    """Return a copy of ``view`` with the owner's edits applied.

    Trims/hides transcript items, drops governance + sources whose tool no longer
    appears in the visible transcript (so the policy panel stays consistent), and
    blanks any literal redaction terms across all free text. A no-op edits object
    returns the view unchanged (shallow-copied).
    """
    norm = normalize_edits(edits)
    out = dict(view)
    if not norm:
        return out

    rng = norm.get("range")
    hidden = set(norm.get("hidden", []))
    terms = norm.get("redactions", [])

    transcript = view.get("transcript")
    transcript = list(transcript) if isinstance(transcript, Sequence) and not isinstance(transcript, str) else []

    # 1) range + hidden over transcript indices
    kept = [item for i, item in enumerate(transcript) if _is_visible(i, rng, hidden)]

    # 2) which tools survive -> keep only matching governance + sources
    visible_tools = {
        _short_tool(item.get("name"))
        for item in kept
        if isinstance(item, Mapping) and item.get("kind") == "tool"
    }
    visible_tools.discard("")

    def _tool_visible(name: object) -> bool:
        short = _short_tool(name)
        return short == "" or short in visible_tools

    governance = view.get("governance")
    governance = [g for g in governance if isinstance(g, Mapping) and _tool_visible(g.get("name"))] \
        if isinstance(governance, Sequence) and not isinstance(governance, str) else []

    sources = view.get("sources")
    sources = [s for s in sources if isinstance(s, Mapping) and _tool_visible(s.get("tool"))] \
        if isinstance(sources, Sequence) and not isinstance(sources, str) else []

    # 3) literal redactions across all free text
    if terms:
        kept = [_redact_deep(i, terms) for i in kept]
        governance = [_redact_deep(g, terms) for g in governance]
        sources = [_redact_deep(s, terms) for s in sources]
        summary = view.get("summary")
        if isinstance(summary, Mapping):
            out["summary"] = _redact_deep(summary, terms)
        # Also cover the top-level trace + results free text: without this an
        # owner-supplied redaction term still appeared in the public response's
        # trace/results, defeating the hide the owner asked for (SHARE-2).
        for extra_key in ("trace", "results"):
            if extra_key in out:
                out[extra_key] = _redact_deep(out.get(extra_key), terms)

    out["transcript"] = kept
    out["governance"] = governance
    out["sources"] = sources

    counts = view.get("counts")
    new_counts = dict(counts) if isinstance(counts, Mapping) else {}
    new_counts["stepCount"] = sum(1 for i in kept if isinstance(i, Mapping) and i.get("kind") == "tool")
    new_counts["sourceCount"] = len(sources)
    new_counts["governanceCount"] = len(governance)
    out["counts"] = new_counts

    return out
