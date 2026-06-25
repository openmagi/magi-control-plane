"""Shared payload projection for regex evaluation.

Three surfaces evaluate regex against CC hook payloads:

  - `cloud/app.py` /verify_inline — the runtime path. Receives the live
    payload from the gate.
  - `policy/dry_run.py` — offline replay over historical ledger rows
    (`body['__payload_snapshot__']`).
  - `policy/test_runner.py` — synthetic CC hook payload simulator
    (D77 "Test this policy" panel).

Before this module, each surface had its own `_payload_text` flavor and
they disagreed on which fields counted as projectable text. An operator
who authored an `EvidencePolicy + regex` whose pattern targeted
`tool_response.output` would see DIFFERENT verdicts at the simulator
versus the runtime because the simulator scanned `tool_response` and
the runtime only saw `payload.text` (or a JSON dump).

This module collapses both projections into one function:

  - `project_payload_for_regex(payload)` — the WHOLE-payload projection
    used when the requires entry has no `field_path`. Matches the
    runtime /verify_inline behavior byte-for-byte (try `text`, fall
    back to JSON dump) so every offline surface produces the same
    answer the live gate would.
  - `resolve_field_for_regex(payload, field_path)` — the SCOPED
    projection used when the requires entry has a `field_path`.
    Defers to `payload_schemas._resolve_dotted_path` +
    `_format_value_for_prompt` (the same helpers /verify_inline calls)
    so dict / list / int leaves render the same JSON the runtime saw.

A contract test in `tests/test_policy_payload_projection.py` asserts
byte-equal output across `verify_inline`, `dry_run`, and `test_runner`
so a future drift fires loudly instead of silently.
"""
from __future__ import annotations

import json
from typing import Any

# `_MISSING` is reused from payload_schemas so the sentinel identity is
# stable across surfaces. Callers compare with `is` rather than `==` to
# distinguish "field absent" from "field is None".
from .payload_schemas import (
    _MISSING,
    _format_value_for_prompt,
    _resolve_dotted_path,
)


# Cap mirrored from cloud/app.py /verify_inline (8000 chars) so an
# adversarial fixture cannot pin the CPU under a worst-case regex.
PROJECTION_MAX_CHARS = 8000


def project_payload_for_regex(payload: Any) -> str:
    """Project the WHOLE payload to a string for regex.search.

    Mirrors cloud/app.py /verify_inline:1334-1341 exactly:
      - if `payload['text']` is a string, return it (capped).
      - else JSON-dump the whole payload (capped).

    Returns an empty string when the payload is not a dict at all
    (callers treat empty as "no scannable text" / indeterminate).
    """
    if not isinstance(payload, dict):
        return ""
    txt = payload.get("text")
    if isinstance(txt, str):
        return txt[:PROJECTION_MAX_CHARS]
    try:
        return json.dumps(payload, ensure_ascii=False)[:PROJECTION_MAX_CHARS]
    except (TypeError, ValueError):
        return ""


def project_snapshot_for_regex(snapshot: Any) -> str:
    """Project a `body['__payload_snapshot__']` to a string for regex.

    Snapshots written by /verify_inline are EITHER:
      - a scoped string (the resolved field value, when the original
        verify_inline call used field_path scoping), or
      - the whole-payload JSON dump (back-compat with pre-D82c rows
        and the non-field_path branch).

    For a scoped string we return it verbatim (capped). For a dict
    we delegate to `project_payload_for_regex`. Anything else returns
    empty so the caller can surface "indeterminate" rather than
    falsely claim the action would have fired.
    """
    if isinstance(snapshot, str):
        return snapshot[:PROJECTION_MAX_CHARS]
    if isinstance(snapshot, dict):
        return project_payload_for_regex(snapshot)
    return ""


# Sentinel returned by `resolve_field_for_regex` when the field is
# absent on the payload. Callers MUST distinguish "field absent" (deny
# with a clear reason — the runtime path) from "field present, empty"
# (regex did not match because the value is empty). We re-export
# `_MISSING` from payload_schemas so a single `is` comparison works
# across files.
FIELD_MISSING = _MISSING


def resolve_field_for_regex(
    payload: Any, field_path: str,
) -> str | object:
    """Resolve a dotted-path scope for regex.search.

    Mirrors cloud/app.py /verify_inline:1361-1397: walk the dotted
    path, render the leaf via `_format_value_for_prompt` (so dicts /
    lists / bools / ints get formatted into a regex-scannable string),
    cap to `PROJECTION_MAX_CHARS`.

    Returns:
      - a (possibly empty) string when the field resolves.
      - `FIELD_MISSING` (the `_MISSING` sentinel) when any dotted
        segment is absent. Callers MUST handle this distinctly from
        an empty string so the operator sees the same "field absent"
        reason the runtime emits.
    """
    if not field_path:
        return project_payload_for_regex(payload)
    val = _resolve_dotted_path(payload, field_path)
    if val is _MISSING:
        return FIELD_MISSING
    return _format_value_for_prompt(val)[:PROJECTION_MAX_CHARS]


__all__ = [
    "FIELD_MISSING",
    "PROJECTION_MAX_CHARS",
    "project_payload_for_regex",
    "project_snapshot_for_regex",
    "resolve_field_for_regex",
]
