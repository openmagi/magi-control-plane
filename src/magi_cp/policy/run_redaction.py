"""D50 / D53a - payload redactor for client-visible previews.

The dashboard renders short previews of recorded evidence payloads (e.g. the
"Recent emissions" widget on /rules). Raw payloads never reach the browser -
every sample passes through `redact_payload_preview` first, which:

  1. Pulls the most informative scalar fields out of the body dict (preferring
     a small allowlist: `text`, `command`, `prompt`, `criterion`, `pattern`,
     `reasons[0]`, `step`), avoiding nested blobs or unknown keys.
  2. Runs the resulting string through `redact_text`, which masks the most
     common secret-shaped patterns (API keys, JWT-shaped tokens, long hex
     digests, emails) with a stable `[REDACTED:<kind>]` marker.
  3. Truncates to a fixed character budget so a leaked surprise (an unknown
     long opaque blob) cannot dominate the row.

The marker shape is intentionally non-secret-looking so an operator reading
the preview can tell at a glance "this used to be a token" vs "this was a
short failure reason". The `<kind>` tag distinguishes the rule that fired
(api_key / jwt / hex / email) which helps when auditing the redactor
itself.

DESIGN INTENT:
  - Fail-closed projection. Anything not on the allowlist is dropped, not
    masked. A novel future field with a secret in it never reaches the
    preview because the allowlist gate ran first.
  - Linear regex passes only (no catastrophic backtracking). Each pattern
    is anchored or bounded so a pathological input stays within a small
    multiplier of its length.
  - Pure functions, no I/O. Safe to call from any request handler. Tested
    against literal secret-shaped fixtures.

Brief constraint: every sample payload returned to the client MUST flow
through this module's redactor before serialization.
"""
from __future__ import annotations

import re
from typing import Any

# Hard cap on a single preview field. The /ledger/samples endpoint passes
# `max_chars=240` per the D53a brief; keeping the default here matches that.
DEFAULT_PREVIEW_MAX_CHARS = 240

# Allowlisted body fields, in priority order. The first one that resolves
# to a non-empty string scalar (or list-of-strings via `.[0]`) wins.
# Anything outside this list is dropped - never masked. This is the
# fail-closed projection the brief calls for.
_PREVIEW_FIELD_ALLOWLIST: tuple[str, ...] = (
    "text",
    "command",
    "prompt",
    "criterion",
    "pattern",
    "step",
)

# Linear-time regex patterns for the most common secret shapes. Each
# emits a stable `[REDACTED:<kind>]` marker so the operator can tell
# what shape was caught without seeing the bytes.
#
# IMPORTANT: each pattern is bounded (no nested unbounded `+` over `.`)
# so an adversarial input cannot push us into catastrophic backtracking.
_REDACTION_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # JWT three-segment shape. We match aggressively (any sufficiently
    # long base64url triplet) so we err on the side of redacting too
    # much in previews.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    # Vendor-prefixed API keys (Stripe, OpenAI, Anthropic, GitHub,
    # generic "sk-" / "pk-"). Bounded length to avoid backtracking.
    ("api_key", re.compile(r"\b(?:sk|pk|rk|api|key)[-_][A-Za-z0-9_\-]{16,80}\b", re.IGNORECASE)),
    # AWS-style 20-char uppercase-and-digits keys (AKIA...). Bounded.
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # GitHub PAT (`ghp_` / `ghs_` / `gho_` / `ghu_` / `ghr_`).
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,80}\b")),
    # Slack tokens (xoxb-, xoxp-, xoxa-, xoxr-, xoxo-) — bounded.
    ("slack_token", re.compile(r"\bxox[abprostu]-[A-Za-z0-9\-]{10,80}\b")),
    # Google API keys (AIza... + 35 base64url chars).
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # Hugging Face access tokens (`hf_...` + 30-40 chars).
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{30,40}\b")),
    # Long hex digests (sha256 etc) - 40+ chars catches the common
    # secret-shaped opaque blobs without flagging short ids.
    ("hex", re.compile(r"\b[0-9a-f]{40,128}\b", re.IGNORECASE)),
    # Email addresses. Loose RFC; bounded local + domain length.
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}\b")),
)


def redact_text(s: str) -> str:
    """Mask common secret-shaped substrings in `s` with `[REDACTED:<kind>]`.

    Linear scan: every pattern runs `re.sub` once over the string. No
    pattern alternates over `.*` so the total work is bounded by
    `len(s) * len(_REDACTION_PATTERNS)`.

    A string with nothing secret-shaped is returned verbatim. The
    function is idempotent: re-running it produces the same output
    (the `[REDACTED:<kind>]` marker matches none of the patterns).
    """
    if not s:
        return s
    out = s
    for kind, pat in _REDACTION_PATTERNS:
        out = pat.sub(f"[REDACTED:{kind}]", out)
    return out


def _scalar_to_string(v: Any) -> str | None:
    """Coerce an allowlisted field to a single preview string.

    Returns None for non-renderable shapes (dict, deep nesting, None).
    A list of strings is joined by a single space so a `reasons` list
    of short strings still produces a useful preview.
    """
    if isinstance(v, str):
        return v if v else None
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, list):
        # Only render lists of scalars. A list with nested dicts gets
        # dropped (fail-closed) so we never accidentally flatten a
        # nested secret blob into the preview.
        parts: list[str] = []
        for item in v[:5]:  # bounded so a runaway list cannot dominate
            if isinstance(item, str) and item:
                parts.append(item)
            elif isinstance(item, (int, float, bool)):
                parts.append(str(item))
            else:
                # mixed nested shape → bail
                return None
        return " ".join(parts) if parts else None
    return None


def redact_payload_preview(
    body: dict | None,
    *,
    max_chars: int = DEFAULT_PREVIEW_MAX_CHARS,
) -> str:
    """Produce a redacted single-line preview of `body`.

    Steps:
      1. Walk the allowlist; pick the first field that has a renderable
         scalar (or list-of-scalars).
      2. Optionally append the first entry of `reasons` if it is a list
         of short strings - this catches the deny-reason rows where
         `text` is empty but `reasons[0]` is the audit-worthy fragment.
      3. Run the resulting string through `redact_text`.
      4. Collapse internal whitespace + trim to `max_chars`; if we cut
         anything append `...` (matches the brief's contract).

    Empty / unknown / non-dict input returns the empty string. A body
    where the allowlist hits nothing also returns the empty string;
    we deliberately do NOT fall back to `json.dumps(body)` because that
    would defeat the fail-closed allowlist (a novel future field with a
    secret would leak through).
    """
    if not isinstance(body, dict) or not body:
        return ""

    chunks: list[str] = []
    for key in _PREVIEW_FIELD_ALLOWLIST:
        if key not in body:
            continue
        rendered = _scalar_to_string(body[key])
        if rendered:
            chunks.append(rendered)
            # One field is enough for the row preview; the rest are
            # informational only and bounded below.
            break

    # `reasons[0]` is special-cased because deny/review rows often carry
    # the human-meaningful fragment there rather than in `text`.
    if "reasons" in body:
        first_reason = _scalar_to_string(body["reasons"])
        if first_reason and first_reason not in chunks:
            chunks.append(first_reason)

    # Join with a plain hyphen (the repo style forbids em-dash in any
    # user-visible copy; previews are user-visible).
    raw = " - ".join(chunks).strip()
    if not raw:
        return ""

    # Collapse internal whitespace so a multi-line `text` field renders
    # on one line. Tabs and CR also go.
    collapsed = re.sub(r"\s+", " ", raw)
    redacted = redact_text(collapsed)

    if len(redacted) <= max_chars:
        return redacted
    # Truncate and append the ellipsis marker (3 dots, not the unicode
    # ellipsis char, so it survives ASCII-only downstream renderers).
    cut = max_chars - 3
    if cut < 0:
        cut = 0
    truncated = redacted[:cut]
    # Guard against splitting a `[REDACTED:<kind>]` marker mid-token.
    # A trailing open bracket without a matching close would produce
    # `...some text [REDACTED:` at the boundary, which looks like the
    # start of leaked content to a downstream audit script. Trim back
    # to before the unterminated marker so the preview ends cleanly.
    open_idx = truncated.rfind("[REDACTED:")
    if open_idx != -1 and "]" not in truncated[open_idx:]:
        truncated = truncated[:open_idx].rstrip()
    return truncated + "..."
