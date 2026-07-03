"""Policy (de)serialization + compile-with-sha + token issue helpers.

Extracted verbatim from ``app.py`` (modularization design
2026-07-03-cloud-app-modularization-design.md). Behavior-preserving: the
functions are byte-identical to their former in-``app.py`` / ``schemas.py``
form; ``app.py`` re-imports them so every route reference (and the tests that
import e.g. ``_issue_token`` / ``_synth_subject_and_hash`` from
``magi_cp.cloud.app``) keeps working unchanged. Kept free of any import from
``app.py`` / ``schemas.py`` so the route modules and schemas can import these
without a circular import (``schemas`` -> ``serialization`` is the only
direction).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException

from ..evidence import sign_token
from ..policy import (
    AnyPolicy, ContextInjectionPolicy, EvidenceAuditPolicy, EvidencePolicy,
    EvidencePreconditionPolicy, InputRewritePolicy,
    McpGatingPolicy, PermissionPolicy, RunCommandPolicy, SubagentPolicy,
    compile_to_managed_settings,
)
from .policy_store import _evidence_req_to_dict
from .constants import PROTECTED_TOKEN_FIELDS, TOKEN_TTL_SECONDS

if TYPE_CHECKING:
    # Annotation-only (never evaluated at runtime under
    # `from __future__ import annotations`), so importing under TYPE_CHECKING
    # keeps this module free of a runtime dependency on the DB / key layers.
    from .db import LedgerRepo
    from .keys import KeyStore


# ── PR2 synthesis helpers ─────────────────────────────────────────────
def _canonical_json_bytes(payload: dict) -> bytes:
    """Compact canonical JSON used ONLY for `_synth_subject_and_hash`.

    NOTE: This uses `separators=(",", ":")` (compact); the ledger's
    `_canonical` in `cloud/db.py` and the token signer's `_canonical` in
    `evidence/tokens.py` both use Python's DEFAULT separators (with
    whitespace). The byte sequences therefore differ — this hash is an
    opaque request-time tag, NOT a value you can cross-check against a
    ledger-chain hash or a token body. PR3/PR4 work that wants to verify a
    request-time payload_hash against a ledger entry must canonicalise via
    the matching helper, not this one.
    """
    import json as _json
    return _json.dumps(payload, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8")


def _synth_subject_and_hash(payload: dict | None,
                             session_id: str | None = None) -> tuple[str, str]:
    """Derive (subject, payload_hash) when neither was supplied.

    subject defaults to:
      - `session_<session_id>` when a session id is known
      - `req_<random hex>`     otherwise (one-shot opaque tag)

    Per PR2 review (issue #1 follow-up), synth output is constrained to the
    legacy `_KEY_PATTERN` charset (`[A-Za-z0-9_\\-]`). Earlier drafts used a
    colon separator (`session:<id>`), but mixing colon-bearing and legacy
    alphanumeric-only matter shapes in the ledger / HITL index produces
    silent data drift (two cohorts of identifiers with no documented
    schema). Underscore separator keeps the column shape uniform during the
    PR2→PR3 widening window AND makes the subject reachable from the
    sentinel charset `[A-Za-z0-9_\\-]+` should anyone wire it into a future
    sentinel template.

    session_id is also sanitised here: any characters outside `_KEY_PATTERN`
    are stripped. This closes the equivalent injection path that
    VerifyDispatchReq.subject explicitly rejects via regex constraint —
    without this, a hand-crafted `payload={"session_id": "...\\n..."}` would
    smuggle bad bytes into the ledger key.

    payload_hash is sha256 of the canonical_json(payload) — empty payload
    becomes sha256("{}"), which is still a stable address (a verifier
    looking at "no payload" deterministically reproduces it).
    """
    import secrets
    if session_id:
        # Strip anything outside the legacy key charset; bound the length so
        # the synthesised subject stays well under the 64-char DB column.
        safe = re.sub(r"[^A-Za-z0-9_\-]", "", session_id)[:48]
        if safe:
            subject = f"session_{safe}"
        else:
            # session_id contained nothing usable — fall back to nonce.
            subject = f"req_{secrets.token_hex(8)}"
    else:
        subject = f"req_{secrets.token_hex(8)}"
    body = payload if isinstance(payload, dict) else {}
    payload_hash = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()[:32]
    return subject, payload_hash


def _deserialize_policy_from_api(d: dict) -> AnyPolicy:
    """Discriminated deserializer.

    Issue #1 P0 (#12): route through `policy_from_dict` so PUT
    /policies/{id} can persist any archetype, not just evidence. The
    legacy EvidencePolicy shape is preserved (`type` defaults to
    `evidence`).
    """
    from ..policy.ir import policy_from_dict
    return policy_from_dict(d)


# ── ledger / token helpers ────────────────────────────────────────────
def _frame_meta_for_ledger(
    hook_event: str | None, matcher: str | None,
) -> dict[str, str]:
    """D53b follow-up: project optional frame metadata onto the
    ledger-body subset the offline dry-run replay reads.

    The replay needs `body['hook_event']` and `body['matcher']` to
    scope ledger rows to the proposed policy's (event, matcher)
    frame; without them, the replay would admit every tenant row in
    the window and over-report the matched count. We accept None for
    each field (gates that haven't rolled forward past this contract
    just omit them) and project only the values that are present, so
    a gate that supplies only `hook_event` still gets partial frame
    metadata in its rows.

    Returns an empty dict when both inputs are None. The caller folds
    the result into the ledger body via dict spread; protected ledger
    fields written by the route override on key clash.
    """
    out: dict[str, str] = {}
    if isinstance(hook_event, str) and hook_event:
        out["hook_event"] = hook_event
    if isinstance(matcher, str) and matcher:
        out["matcher"] = matcher
    return out


def _iso_ts(ts: int) -> str:
    """Format a ledger row's epoch-second `ts` as ISO-8601 UTC.

    D53a: the samples endpoint returns ISO strings (the dashboard renders
    them as relative time via the browser). `ts` is stored as an int
    epoch second in `LedgerEntry.ts`; we format with a trailing `Z` so
    the consumer doesn't have to guess at the timezone.
    """
    import datetime as _dt
    return (
        _dt.datetime.fromtimestamp(int(ts), tz=_dt.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _citations_summary(doc) -> list[dict]:
    return [
        {"ref": v.citation.ref, "case_number": v.case_number,
         "status": v.status, "reasons": v.reasons}
        for v in doc.verdicts
    ]


def _issue_token(subject: str, payload_hash: str, verdict: str, *,
                 ledger: LedgerRepo, keystore: KeyStore, kid: str,
                 step: str = "citation_verify",
                 tenant_id: str = "default",
                 extra: dict | None = None,
                 ledger_extra: dict | None = None) -> dict:
    """Issue a cloud-signed verdict token.

    PR4: legacy `matter`/`doc_hash` mirror fields removed from the signed
    body. Gates that haven't rolled forward past PR2 will no longer find
    a verifying token — operators must upgrade gate binaries before
    flipping to a PR4 cloud.

    `extra` is folded into the signed token body (and therefore into
    the ledger row body too). `ledger_extra` is written ONLY to the
    ledger row body and is NOT signed; use it for frame metadata
    (hook_event / matcher) and the runtime payload snapshot the
    offline dry-run replay reads, which the gate has no reason to
    re-verify cryptographically.
    """
    now = int(time.time())
    # L2: extras are *base*; protected fields go LAST so they always win.
    base = dict(extra) if extra else {}
    leaked = PROTECTED_TOKEN_FIELDS & base.keys()
    if leaked:
        raise HTTPException(500, f"protected field clash: {leaked}")
    body = {
        **base,
        "step": step,
        "subject": subject,
        "payload_hash": payload_hash,
        "verdict": verdict,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "issuer": os.environ.get("MAGI_CP_ISSUER", "magi-cloud-dev"),
        "kid": kid,
    }
    token = sign_token(body, keystore.load_private())
    # PR4: `ledger.append` accepts `subject=` as the canonical kwarg. The
    # underlying DB column is still named `matter` until the deeper ledger
    # rename ships — see LedgerRepo.append for that compatibility shim.
    # D53b follow-up: ledger_extra fields land in the ledger row body
    # only (not in the signed token), so frame metadata and the
    # payload snapshot can travel with the row without inflating the
    # token (which gates re-verify cryptographically on every call).
    ledger_body = body
    if ledger_extra:
        # Protected fields still win — the cryptographic identity of
        # the row is anchored on the signed body.
        ledger_body = {**ledger_extra, **body}
    entry = ledger.append(subject=subject, body=ledger_body, token=token,
                           tenant_id=tenant_id)
    return {"verdict": verdict, "token": token, "exp": body["exp"],
            "kid": kid, "ledger_h": entry.h}


# ── policy serialization / compile helpers ────────────────────────────
def _enforcement_label(policy: AnyPolicy) -> str:
    """Short human label for the enforcement character of a policy.

    Issue #1 P0 (#14): type-dispatch per archetype. The declarative
    archetypes are always `enforcing` (no verifier hop; CC consumes
    them out of managed-settings directly). EvidencePolicy keeps the
    D31 (action, event)-based mapping.
    """
    if isinstance(policy, EvidencePolicy):
        if policy.action in ("block", "ask"):
            return "deterministic-gate"
        if policy.trigger.event == "PostToolUse":
            return "observe-only"
        return "log-only"
    # Declarative archetypes: CC enforces directly via managed-settings.
    return "enforcing"


def _serialize_policy_for_api(p: AnyPolicy) -> dict:
    """Per-archetype response serializer.

    Issue #1 P0 (#14): EvidencePolicy keeps its existing JSON shape
    (sentinel_re / requires / action / ...) for back-compat. The
    P2/P3 sibling types carry their own discriminator + fields so the
    dashboard can render the right form.
    """
    if isinstance(p, EvidencePolicy):
        return {
            "type": "evidence",
            "id": p.id,
            "description": p.description,
            "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "sentinel_re": p.sentinel_re,
            "requires": [_evidence_req_to_dict(r) for r in p.requires],
            "action": p.action,
            "on_signature_invalid": p.on_signature_invalid,
            "gate_binary": p.gate_binary,
        }
    if isinstance(p, PermissionPolicy):
        return {
            "type": "permission",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "permission": p.permission,
            "pattern": p.pattern,
            "exclusive": p.exclusive,
        }
    if isinstance(p, SubagentPolicy):
        return {
            "type": "subagent",
            "id": p.id, "description": p.description, "version": p.version,
            "subagent_type": p.subagent_type,
            "tool_allowlist": list(p.tool_allowlist),
        }
    if isinstance(p, McpGatingPolicy):
        return {
            "type": "mcp_gating",
            "id": p.id, "description": p.description, "version": p.version,
            "server": p.server, "action": p.action,
            "exclusive": p.exclusive,
        }
    if isinstance(p, ContextInjectionPolicy):
        return {
            "type": "context_injection",
            "id": p.id, "description": p.description, "version": p.version,
            "event": p.event, "matcher": p.matcher, "template": p.template,
        }
    if isinstance(p, InputRewritePolicy):
        return {
            "type": "input_rewrite",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "rewriter": p.rewriter,
        }
    if isinstance(p, RunCommandPolicy):
        return {
            "type": "run_command",
            "id": p.id, "description": p.description, "version": p.version,
            "trigger": {"host": p.trigger.host, "event": p.trigger.event,
                        "matcher": p.trigger.matcher},
            "runtime": p.runtime,
            "command": p.command,
            "script_path": p.script_path,
            "args": list(p.args),
            "timeout_ms": p.timeout_ms,
            "fail_closed": p.fail_closed,
        }
    if isinstance(p, (EvidenceAuditPolicy, EvidencePreconditionPolicy)):
        # Both new archetypes round-trip through the IR serializer (incl.
        # project_scope), so a saved member is readable via GET /policies/{id}.
        from ..policy.ir import policy_to_dict
        return policy_to_dict(p)
    raise HTTPException(500, f"unserializable policy type: {type(p).__name__}")


def _compile_with_sha(policy: AnyPolicy) -> tuple[dict, str]:
    """Compile a single policy and return (managed_settings, sha256).

    Non-blocking #a fix: the sha is computed over the same byte string
    `compile_files` writes to disk (json.dumps + trailing newline) so
    the dashboard's `compiled_sha256` matches the gate's
    `active_policy_digest` (which hashes the file bytes verbatim).
    """
    import json as _json
    ms = compile_to_managed_settings([policy])
    blob = _json.dumps(ms, ensure_ascii=False,
                        indent=2, sort_keys=True) + "\n"
    return ms, hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _compile_set_with_sha(policies: list[AnyPolicy]) -> tuple[dict, str]:
    """Same as `_compile_with_sha` but for a whole resolved set — used
    by the dashboard's fleet attestation lookup (Issue #1 P0 #2)."""
    import json as _json
    ms = compile_to_managed_settings(policies)
    blob = _json.dumps(ms, ensure_ascii=False,
                        indent=2, sort_keys=True) + "\n"
    return ms, hashlib.sha256(blob.encode("utf-8")).hexdigest()


__all__ = [
    "_canonical_json_bytes",
    "_synth_subject_and_hash",
    "_deserialize_policy_from_api",
    "_frame_meta_for_ledger",
    "_iso_ts",
    "_citations_summary",
    "_issue_token",
    "_enforcement_label",
    "_serialize_policy_for_api",
    "_compile_with_sha",
    "_compile_set_with_sha",
]
