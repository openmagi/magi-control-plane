"""Input-rewriter DSL implementations (D57f-2).

Tiny, bounded DSL the cloud applies SERVER-SIDE before handing the result
to CC via `updatedInput`. The gate shim NEVER interprets a rewriter spec
on its own — it forwards the raw `tool_input` to the cloud, the cloud
runs the rewriter, and the cloud returns the new `tool_input` shape the
gate echoes back to CC as `hookSpecificOutput.updatedInput`.

Security boundary (CRITICAL):
  - The rewriter is a small, well-known set of operations. There is NO
    code-eval, NO jinja, NO arbitrary template. A leaked policy file
    must NOT translate into arbitrary mutation of a tool's input.
  - The rewriter only touches a single named field in the tool input
    (typically `command` for Bash, `url` for WebFetch, `file_path` for
    Read/Write/Edit). The field name is part of the policy spec; the
    rewriter never walks the whole payload.
  - The rewriter is total — on any error (bad regex, missing field, type
    mismatch) it returns the original input unchanged. Failing closed by
    blocking the tool is the EvidencePolicy lane's job; an input_rewrite
    policy is a no-op on failure (the operator authored it as a
    convenience; refusing the tool over a config typo would be hostile).

Supported kinds (v1):

  prefix_strip      Strip a literal prefix from the named text field.
                    Example: strip "sudo " from a Bash `command`.

                    Config:
                      {"field": "command", "prefix": "sudo ",
                       "strip_repeat": false}
                    `strip_repeat=true` peels every consecutive occurrence
                    of the prefix (e.g. `sudo sudo ls` → `ls`).

  scheme_force      Force a URL field to a target scheme. Used to
                    upgrade `http://` to `https://` on WebFetch URLs.

                    Config:
                      {"field": "url", "from": "http://",
                       "to": "https://"}
                    The `from` value is a literal scheme prefix (we do
                    NOT parse with urllib because we want
                    byte-deterministic output that doesn't normalize
                    case/port/etc.).

  regex_substitute  Python re.sub on the named text field, with a
                    bounded replacement template.

                    Config:
                      {"field": "command", "pattern": "<re>",
                       "replacement": "<repl>", "count": 1}
                    Replacement supports backreferences (\\1, \\g<name>)
                    but NO arbitrary code. Pattern is bounded to 2000
                    chars (matches EvidenceReq.kind=regex) and replacement
                    to 2000 chars.

All three kinds:
  - take a `field` naming exactly one key in the tool_input dict
    (string-typed; any other type → no-op);
  - return a new tool_input dict (shallow copy) with the field replaced
    OR the original dict unchanged when the rewrite is a no-op / errored;
  - never raise (PolicyError translates to no-op).

`apply_rewriter(spec, tool_input)` is the single seam the cloud calls.
"""
from __future__ import annotations

import re
from typing import Any, Literal


# Kinds the v1 DSL recognizes. Adding a new kind requires a code change
# here AND a matrix entry; we deliberately avoid an open-set kind table
# so a stale policy file can't unlock an unaudited operation.
RewriterKindLiteral = Literal["prefix_strip", "scheme_force", "regex_substitute"]
REWRITER_KINDS: frozenset[str] = frozenset({
    "prefix_strip", "scheme_force", "regex_substitute",
})


# Bounds. Match EvidenceReq.kind=regex pattern cap so the wizard's
# author-time validation can reuse the same numeric ceiling.
_MAX_PATTERN_LEN = 2_000
_MAX_REPLACEMENT_LEN = 2_000
_MAX_LITERAL_LEN = 2_000
_MAX_FIELD_LEN = 64
# Field name grammar: identifier shape. Matches the keys CC actually
# delivers in tool_input (command, url, file_path, content, ...).
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def validate_rewriter_spec(spec: dict) -> None:
    """Validate a rewriter spec at authoring time.

    Raises ValueError on any structural problem. Called from
    `InputRewritePolicy.validate()` so the dashboard surfaces the error
    at PUT time, before any policy bytes are persisted.

    Does NOT execute the rewriter. The pattern is `re.compile`d to catch
    invalid regexes early; the resulting Pattern object is discarded.
    """
    if not isinstance(spec, dict):
        raise ValueError(f"rewriter spec must be a dict, got {type(spec).__name__}")
    kind = spec.get("kind")
    if kind not in REWRITER_KINDS:
        raise ValueError(
            f"rewriter kind must be one of {sorted(REWRITER_KINDS)}; got {kind!r}"
        )
    cfg = spec.get("config")
    if not isinstance(cfg, dict):
        raise ValueError("rewriter spec.config must be a dict")
    field = cfg.get("field")
    if not isinstance(field, str) or not _FIELD_NAME_RE.match(field):
        raise ValueError(
            f"rewriter config.field must match {_FIELD_NAME_RE.pattern} "
            f"(got {field!r})"
        )
    if len(field) > _MAX_FIELD_LEN:
        raise ValueError(f"rewriter config.field too long (>{_MAX_FIELD_LEN})")

    if kind == "prefix_strip":
        prefix = cfg.get("prefix")
        if not isinstance(prefix, str) or not prefix:
            raise ValueError("prefix_strip requires non-empty `prefix` string")
        if len(prefix) > _MAX_LITERAL_LEN:
            raise ValueError(
                f"prefix_strip prefix too long (>{_MAX_LITERAL_LEN})"
            )
        if "strip_repeat" in cfg and not isinstance(cfg["strip_repeat"], bool):
            raise ValueError("prefix_strip strip_repeat must be a bool")
    elif kind == "scheme_force":
        from_ = cfg.get("from")
        to = cfg.get("to")
        for k, v in (("from", from_), ("to", to)):
            if not isinstance(v, str) or not v:
                raise ValueError(f"scheme_force requires non-empty `{k}` string")
            if len(v) > _MAX_LITERAL_LEN:
                raise ValueError(
                    f"scheme_force `{k}` too long (>{_MAX_LITERAL_LEN})"
                )
    elif kind == "regex_substitute":
        pattern = cfg.get("pattern")
        replacement = cfg.get("replacement", "")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("regex_substitute requires non-empty `pattern` string")
        if len(pattern) > _MAX_PATTERN_LEN:
            raise ValueError(
                f"regex_substitute pattern too long (>{_MAX_PATTERN_LEN})"
            )
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(
                f"regex_substitute pattern does not compile: {e}"
            ) from e
        if not isinstance(replacement, str):
            raise ValueError("regex_substitute replacement must be a string")
        if len(replacement) > _MAX_REPLACEMENT_LEN:
            raise ValueError(
                f"regex_substitute replacement too long (>{_MAX_REPLACEMENT_LEN})"
            )
        count = cfg.get("count", 0)
        if not isinstance(count, int) or count < 0 or count > 1_000:
            raise ValueError(
                "regex_substitute count must be an int in [0, 1000] "
                "(0 = replace all)"
            )


def apply_rewriter(spec: dict, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Apply a validated rewriter spec to a tool_input dict.

    Returns a NEW dict on a real rewrite; returns the original on any
    no-op (missing field, non-string value, no match, post-rewrite value
    identical to pre, errored spec).

    This function is total — it never raises. A bad spec or hostile
    payload degrades to "return the input unchanged", which the gate then
    forwards to CC without `updatedInput`.
    """
    if not isinstance(tool_input, dict):
        return tool_input if isinstance(tool_input, dict) else {}
    try:
        validate_rewriter_spec(spec)
    except ValueError:
        return tool_input
    kind = spec["kind"]
    cfg = spec["config"]
    field = cfg["field"]
    original = tool_input.get(field)
    if not isinstance(original, str):
        return tool_input

    new_value: str
    if kind == "prefix_strip":
        prefix: str = cfg["prefix"]
        repeat: bool = bool(cfg.get("strip_repeat", False))
        cur = original
        if repeat:
            while cur.startswith(prefix):
                cur = cur[len(prefix):]
        elif cur.startswith(prefix):
            cur = cur[len(prefix):]
        new_value = cur
    elif kind == "scheme_force":
        from_: str = cfg["from"]
        to: str = cfg["to"]
        if original.startswith(from_):
            new_value = to + original[len(from_):]
        else:
            new_value = original
    elif kind == "regex_substitute":
        pattern: str = cfg["pattern"]
        replacement: str = cfg.get("replacement", "")
        count: int = int(cfg.get("count", 0))
        try:
            new_value = re.sub(pattern, replacement, original, count=count)
        except re.error:
            return tool_input
    else:
        # Unreachable — validate_rewriter_spec already gated the kind.
        return tool_input

    if new_value == original:
        return tool_input
    out = dict(tool_input)
    out[field] = new_value
    return out


__all__ = [
    "REWRITER_KINDS",
    "RewriterKindLiteral",
    "apply_rewriter",
    "validate_rewriter_spec",
]
