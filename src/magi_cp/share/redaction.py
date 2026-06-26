"""Public-link redaction for the run-share path.

VENDORED from magi-agent (openmagi/magi-agent @ 1eb6a68c):
  - ``magi_agent/ops/safety.py``: ``MAX_PUBLIC_TEXT_CHARS``, ``UNSAFE_TEXT_RE``,
    ``redact_private_text`` (the canonical linear kernel scrub).
  - ``magi_agent/evidence/run_redaction.py``: ``redact_public_text`` +
    ``build_public_run_view`` (the allowlist fail-closed projection).
magi-control-plane does not import magi-agent (vendoring convention, cf.
``cloud/presets_catalog.py``), so the redaction logic is copied here. Keep in
sync if the upstream kernel/patterns change. Local additions vs upstream: the
allowlist also passes the Claude-Code producer's ``summary.title`` and top-level
``results`` (PR links), both scrubbed.

The kernel scrub (``redact_private_text`` / ``UNSAFE_TEXT_RE``) is comprehensive
and LINEAR: provider token shapes (GitHub / OpenAI / Stripe / AWS / Google /
Slack / Telegram), JWTs, PEM private keys, storage URIs, private filesystem
paths. ``redact_public_text`` adds the gaps it leaves on a PUBLIC surface
(quoted/unquoted credential values, opaque-token assignments,
``scheme://user:pass@`` URL creds, and public-only PII: cluster hostnames,
RFC1918 IPs, emails), each pattern LINEAR by construction.

Known residuals (defense-in-depth, NOT fully closed): a bare high-entropy token
with no key prefix and no provider brand, an opaque ``/``-bearing value under a
non-credential key, and IPv6 (ULA) can still pass. The public-link UX must pair
this with a "review before making public" confirmation, not treat it as a sole
guarantee.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

__all__ = [
    "MAX_PUBLIC_TEXT_CHARS",
    "redact_private_text",
    "redact_public_text",
    "build_public_run_view",
]

# --- vendored kernel: magi_agent/ops/safety.py ------------------------------

MAX_PUBLIC_TEXT_CHARS = 200

UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    # --- auth headers (longest first) ---
    r"author" + r"ization\s*:\s*[^\n\r,;}\"']+|author" + r"ization\s*:|"
    r"bearer\s+\S+|basic\s+[A-Za-z0-9._~+/=-]+|"
    r"coo" + r"kie\s*:|set-coo" + r"kie\s*:|sid=|"
    # --- credential-shaped assignments (consume the assigned value too) ---
    r"(?:sess" + r"ion(?:[_-]?(?:key|id)|key|id))\s*[:=]\s*[^\s,;}\"']+|"
    r"sess" + r"ion\s*=\s*[^\s,;}\"']+|"
    r"(?:pass" + r"word|api[_-]?key|auth[_-]?key|sess" + r"ion[_-]?key|priv"
    + r"ate[_-]?key|connector[_-]?to" + r"ken|se" + r"cret|credential|to"
    + r"ken|signature)\s*[:=]\s*[^\s,;}\"']*|"
    # --- cloud-storage signed-URL / signature markers + storage URIs ---
    r"x-amz-signature|x-goog-signature|sig=|signed[_-]?url|"
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|vault)"
    r"://[^\s,;}\"']+|"
    # --- provider token shapes ---
    r"\bsk[-_](?:live|test|proj)?[-_]?[A-Za-z0-9._-]+|"
    r"\brk_(?:live|test)_[A-Za-z0-9._=-]+|"
    r"\bgh[opusr]_[A-Za-z0-9_]+|"
    r"\bAKIA[0-9A-Z]{8,}|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]?-[A-Za-z0-9-]+|xox[a-z]-[A-Za-z0-9._-]+|"
    r"AIza[A-Za-z0-9_-]+|"
    # --- Telegram bot token (numeric-id:secret) ---
    r"(?:\b|bot)\d{5,}:[A-Za-z0-9_-]{8,}|"
    # --- JWT triple-segment (eyJ-anchored + generic base64url triple) ---
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])|"
    # --- PEM private-key block (full block, inline DOTALL) ---
    r"(?s:-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----.*?-----END [A-Z ]*PRI"
    + r"VATE KEY-----)|"
    r"-----BEGIN [A-Z ]*PRI" + r"VATE KEY-----|"
    # --- "raw / hidden / chain-of-thought" private-material phrasing ---
    r"raw[_ -]?(?:pro" + r"mpt|output|tool|child|transcript|log|result|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"priv" + r"ate[_ -]?reasoning|raw[_ -]?tool[_ -]?output|"
    # --- private filesystem paths (consume the whole path tail) ---
    r"/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|/var/lib(?:/[^\s,;}\"']*)?|"
    r"/private/var(?:/[^\s,;}\"']*)?|/var/folders(?:/[^\s,;}\"']*)?|"
    r"pvc-[A-Za-z0-9-]+|"
    r"(?:^|/)\.(?:ssh|kube|aws|config)(?:/|$)"
    r")",
    re.IGNORECASE,
)


def redact_private_text(text: str, *, max_chars: int | None = MAX_PUBLIC_TEXT_CHARS) -> str:
    """Fail-open scrub: replace every ``UNSAFE_TEXT_RE`` match with ``[redacted]``
    and optionally clip to ``max_chars``. Never raises."""
    if not isinstance(text, str) or not text:
        return ""
    scrubbed = UNSAFE_TEXT_RE.sub("[redacted]", text)
    if max_chars is not None and len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars]
    return scrubbed


# --- vendored public redactor: magi_agent/evidence/run_redaction.py ---------

_REDACTED = "[redacted]"
# Hard cap on how much of a single value we scan/scrub (latency backstop).
_MAX_SCAN_CHARS = 16_384

_CRED_BOUNDARY = r"(?<![A-Za-z0-9])"
_CRED_KEY = (
    r"(?:pass(?:word|phrase|wd)?|pwd"
    r"|secret[_-]?key|secret"
    r"|service[_-]?role[_-]?key"
    r"|api[_-]?key|x-api-key|auth[_-]?key|access[_-]?key"
    r"|access[_-]?token|client[_-]?secret|private[_-]?key"
    r"|credentials?|token|pat)"
)
_CRED_PREFIX = rf"({_CRED_BOUNDARY}(?i:{_CRED_KEY})[\"']?\s*[:=]\s*)"
_QUOTED_CRED_RE = re.compile(
    rf"{_CRED_PREFIX}([\"'])(?:\\.|(?!\2).){{0,2048}}\2"
)
_UNQUOTED_CRED_RE = re.compile(rf"{_CRED_PREFIX}([^\s,;}}\"']{{1,2048}})")
_OPAQUE_SAFE_KEYS = (
    r"(?:commit|sha|hash|digest|checksum|request[_-]?id|trace[_-]?id|run[_-]?id"
    r"|build|etag|version|revision|ref|id)"
)
_OPAQUE_TOKEN_ASSIGN_RE = re.compile(
    rf"((?<![A-Za-z0-9])(?!{_OPAQUE_SAFE_KEYS}\s*[:=])"
    r"[A-Za-z0-9_-]{1,64}\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9+_-]{24,1024}={0,2})"
    r"(?=[\"']?(?:[\s,;}]|$))",
    re.IGNORECASE,
)
_URL_USERINFO_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9+.-]{0,31}:/{1,2})[^/\s:@]{1,256}:[^/\s@]{1,256}@"
)
_CLUSTER_HOST_RE = re.compile(
    r"(?:[A-Za-z0-9-]{1,63}\.){1,10}svc\.cluster\.local\b", re.IGNORECASE
)
_RFC1918_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b"
)


def redact_public_text(
    value: str, *, max_chars: int | None = MAX_PUBLIC_TEXT_CHARS
) -> str:
    """Scrub a free-text string for a PUBLIC share surface. Fail-open, linear."""
    if not isinstance(value, str) or not value:
        return ""
    if len(value) > _MAX_SCAN_CHARS:
        value = value[:_MAX_SCAN_CHARS]
    scrubbed = _QUOTED_CRED_RE.sub(rf"\1\2{_REDACTED}\2", value)
    scrubbed = _UNQUOTED_CRED_RE.sub(rf"\1{_REDACTED}", scrubbed)
    scrubbed = _OPAQUE_TOKEN_ASSIGN_RE.sub(rf"\1{_REDACTED}", scrubbed)
    scrubbed = _URL_USERINFO_RE.sub(rf"\1{_REDACTED}@", scrubbed)
    scrubbed = redact_private_text(scrubbed, max_chars=None)
    scrubbed = _CLUSTER_HOST_RE.sub(_REDACTED, scrubbed)
    scrubbed = _RFC1918_RE.sub(_REDACTED, scrubbed)
    scrubbed = _EMAIL_RE.sub(_REDACTED, scrubbed)
    if max_chars is not None and len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars]
    return scrubbed


def _redact_nested(value: object) -> object:
    """Recursively scrub free text inside a small structure (keys AND values)."""
    if isinstance(value, str):
        return redact_public_text(value, max_chars=None)
    if isinstance(value, Mapping):
        return {
            redact_public_text(str(k), max_chars=None): _redact_nested(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_nested(v) for v in value]
    return value


# Local addition: ``title`` (Claude-Code producer) is free text, scrubbed.
_SUMMARY_KEYS = ("goal", "result", "status", "model", "usage", "costUsd", "title")
_SUMMARY_FREE_TEXT = frozenset({"goal", "result", "title"})
_STEP_KEYS = (
    "turnId",
    "toolCallId",
    "activityType",
    "name",
    "status",
    "reason",
    "durationMs",
    "actor",
    "spawnDepth",
    "argsSummary",
    "resultSummary",
)
_STEP_FREE_TEXT = frozenset({"argsSummary", "resultSummary", "name", "reason"})
_GOV_KEYS = ("turnId", "name", "status", "reason", "kind")
_GOV_FREE_TEXT = frozenset({"name", "reason"})
# Ordered transcript: each item is either prose (`text`) or a tool call (reusing
# the step shape). Free-text fields are scrubbed; `kind`/`status`/ids pass.
_TRANSCRIPT_TOOL_KEYS = ("kind", "toolCallId", "activityType", "name", "status", "argsSummary")
_TRANSCRIPT_TOOL_FREE_TEXT = frozenset({"name", "argsSummary"})
# Local addition: ``resultCount`` (Claude-Code producer counts).
_COUNT_KEYS = (
    "stepCount", "turnCount", "receiptCount", "governanceCount", "resultCount", "sourceCount",
)
# Local addition: top-level ``results`` (PR links) -> allowlist {prNumber, prUrl}.
_RESULT_KEYS = ("prNumber", "prUrl")
_RESULT_FREE_TEXT = frozenset({"prUrl"})
# Local addition: top-level ``sources`` (research evidence) -> {tool, ref, isUrl,
# credibility (an LLM-judge verdict on the source)}.
_SOURCE_KEYS = ("tool", "ref", "isUrl", "credibility")
_SOURCE_FREE_TEXT = frozenset({"ref", "credibility"})


def _scrub_opt(value: object) -> object:
    return redact_public_text(value, max_chars=None) if isinstance(value, str) else value


def _public_summary(summary: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _SUMMARY_KEYS:
        if key not in summary:
            continue
        value = summary[key]
        if key in _SUMMARY_FREE_TEXT:
            out[key] = redact_public_text(str(value), max_chars=None)
        elif key == "model" and isinstance(value, Mapping):
            out[key] = {
                "label": _scrub_opt(value.get("label")),
                "provider": _scrub_opt(value.get("provider")),
            }
        elif key == "model" and isinstance(value, str):
            out[key] = _scrub_opt(value)
        elif key == "model":
            # Unexpected model shape (list/etc): scrub rather than passthrough,
            # so this fail-closed boundary holds regardless of the caller.
            out[key] = _redact_nested(value)
        elif key == "usage" and isinstance(value, Mapping):
            out[key] = {
                "inputTokens": value.get("inputTokens"),
                "outputTokens": value.get("outputTokens"),
            }
        else:
            out[key] = value
    return out


def _public_step(step: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _STEP_KEYS:
        if key not in step:
            continue
        out[key] = _redact_nested(step[key]) if key in _STEP_FREE_TEXT else step[key]
    return out


def _public_transcript_item(item: Mapping[str, object]) -> dict | None:
    """Project one ordered-transcript item (prose or tool call), fail-closed.

    A `text` item keeps only its scrubbed prose; a `tool` item keeps the
    allowlisted step fields (name / argsSummary scrubbed). Unknown kinds drop.
    """
    kind = item.get("kind")
    if kind == "text":
        return {"kind": "text", "text": redact_public_text(str(item.get("text", "")), max_chars=None)}
    if kind == "tool":
        out: dict[str, object] = {}
        for key in _TRANSCRIPT_TOOL_KEYS:
            if key not in item:
                continue
            out[key] = _redact_nested(item[key]) if key in _TRANSCRIPT_TOOL_FREE_TEXT else item[key]
        return out
    return None


def _public_gov(entry: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _GOV_KEYS:
        if key not in entry:
            continue
        value = entry[key]
        out[key] = redact_public_text(str(value), max_chars=None) if key in _GOV_FREE_TEXT else value
    return out


def _public_result(entry: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _RESULT_KEYS:
        if key not in entry:
            continue
        value = entry[key]
        if key in _RESULT_FREE_TEXT:
            out[key] = redact_public_text(str(value), max_chars=None)
        elif key == "prNumber":
            # PR numbers are ints; the producer copies the field verbatim from a
            # model-influenceable transcript event, so coerce fail-closed rather
            # than pass an arbitrary string/object through to the public link.
            out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
        else:
            out[key] = value
    return out


def _public_source(entry: Mapping[str, object]) -> dict:
    out: dict[str, object] = {}
    for key in _SOURCE_KEYS:
        if key not in entry:
            continue
        value = entry[key]
        out[key] = (
            redact_public_text(str(value), max_chars=None)
            if key in _SOURCE_FREE_TEXT
            else value
        )
    return out


def build_public_run_view(view: Mapping[str, object]) -> dict:
    """Allowlist fail-closed projection of a run view for a PUBLIC link.

    Only known keys survive; every free-text value is scrubbed via
    :func:`redact_public_text`. Numeric/enum/id fields pass through. Unknown
    keys are dropped.
    """
    summary = view.get("summary")
    trace = view.get("trace")
    transcript = view.get("transcript")
    governance = view.get("governance")
    results = view.get("results")
    sources = view.get("sources")
    counts = view.get("counts")

    out_counts: dict[str, object] = {}
    if isinstance(counts, Mapping):
        out_counts = {k: counts[k] for k in _COUNT_KEYS if k in counts}

    return {
        "schemaVersion": view.get("schemaVersion"),
        # Client-controlled; scrub like every other free-text field (fail-closed).
        "sessionId": _scrub_opt(view.get("sessionId")),
        "summary": _public_summary(summary) if isinstance(summary, Mapping) else None,
        "results": [
            _public_result(r)
            for r in (results if isinstance(results, Sequence) and not isinstance(results, str) else [])
            if isinstance(r, Mapping)
        ],
        "sources": [
            _public_source(s)
            for s in (sources if isinstance(sources, Sequence) and not isinstance(sources, str) else [])
            if isinstance(s, Mapping)
        ],
        "trace": [
            _public_step(s)
            for s in (trace if isinstance(trace, Sequence) and not isinstance(trace, str) else [])
            if isinstance(s, Mapping)
        ],
        "transcript": [
            ti
            for item in (transcript if isinstance(transcript, Sequence) and not isinstance(transcript, str) else [])
            if isinstance(item, Mapping)
            for ti in (_public_transcript_item(item),)
            if ti is not None
        ],
        "governance": [
            _public_gov(g)
            for g in (governance if isinstance(governance, Sequence) and not isinstance(governance, str) else [])
            if isinstance(g, Mapping)
        ],
        "counts": out_counts,
    }
