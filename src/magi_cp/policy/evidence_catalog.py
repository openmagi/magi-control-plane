"""D56e: Evidence record types catalog.

The new Rules → Evidence tab is the operator-facing catalog of every
kind of evidence record the system can emit to the ledger. It answers
"what shapes of ledger rows will I see, and which check is each one
authored by?" without forcing the operator to read source code.

One entry per record type. Entries come from:

  built-in verifiers
      Each registered verifier declares its output evidence schema in
      `magi_cp.verifier.descriptors`. The record id is the verifier's
      step name. Verdict set comes from the descriptor.

  inline check kinds in active policies
      The runtime /verify_inline route writes
      `body['step'] = inline_<kind>` for every inline regex /
      llm_critic / shacl evaluation. We emit one catalog row per
      inline kind that at least one stored policy references; the
      generic shape is documented inline (the runtime does not vary
      the body fields by policy).

  custom verifiers (preview)
      Authored via /verifiers/new. Custom verifiers do not declare an
      `emits_shape` today; the catalog row uses the generic verdict
      envelope plus the operator's description so the row is not
      empty. Marked `preview=True` so the dashboard can render the
      "no runtime binding" notice.

This is a sibling module to check_catalog.py — together they back the
two new derived tabs on the Rules page. /ledger is unchanged; it
already filters by step name, which is the same key as `id` here.
"""
from __future__ import annotations

from typing import Any


# ── shared envelope ──────────────────────────────────────────────────
# Every signed verdict the cloud writes to the ledger carries this
# common envelope. Kept in sync with verifier.descriptors._COMMON_OUTPUT_FIELDS
# by intent; we re-declare it here so this module stays standalone-importable
# (descriptor import is lazy to avoid cycles in test fixtures).
_COMMON_ENVELOPE: list[dict] = [
    {"path": "step", "type": "str", "description": "Verifier step name. Same value the policy IR binds via requires[].step."},
    {"path": "subject", "type": "str", "description": "Canonical subject the verdict is bound to."},
    {"path": "payload_hash", "type": "str", "description": "SHA-256 of the input payload the verifier ran against."},
    {"path": "verdict", "type": "str", "description": "Verdict label — one of pass / review / deny / needs_review / not_applicable."},
    {"path": "reasons", "type": "list", "description": "Human-readable reasons collected during the run."},
]


# Static catalog of generic inline-kind evidence shapes. These are the
# fields the /verify_inline route writes to the ledger for each inline
# kind; the actual evaluation body (pattern / criterion / shape) lives
# in the originating policy, not in the ledger row.
_INLINE_EVIDENCE_SHAPES: dict[str, dict] = {
    "inline_regex": {
        "id": "inline_regex",
        "name": "Inline regex check",
        "origin": "inline",
        "kind": "inline-regex",
        "description": (
            "Emitted by /verify_inline for every kind=regex requires entry "
            "evaluated at runtime. One row per evaluation."
        ),
        "verdict_set": ["pass", "fail"],
        "payload_schema": [
            *_COMMON_ENVELOPE,
            {"path": "pattern", "type": "str", "description": "The regex pattern that was evaluated."},
            {"path": "matched", "type": "bool", "description": "True when the pattern matched the payload text."},
        ],
        "preview": False,
    },
    "inline_llm_critic": {
        "id": "inline_llm_critic",
        "name": "Inline LLM critic check",
        "origin": "inline",
        "kind": "inline-llm-critic",
        "description": (
            "Emitted by /verify_inline for every kind=llm_critic requires "
            "entry. Verdict is computed by the configured reviewer LLM."
        ),
        "verdict_set": ["pass", "fail"],
        "payload_schema": [
            *_COMMON_ENVELOPE,
            {"path": "criterion", "type": "str", "description": "The natural-language criterion the LLM judged."},
            {"path": "model", "type": "str", "description": "Reviewer model that produced the verdict."},
        ],
        "preview": False,
    },
    "inline_shacl": {
        "id": "inline_shacl",
        "name": "Inline SHACL shape check",
        "origin": "inline",
        "kind": "inline-shacl",
        "description": (
            "Emitted by /verify_inline for every kind=shacl requires entry. "
            "Verdict comes from pySHACL conformance check on the payload graph."
        ),
        "verdict_set": ["pass", "fail"],
        "payload_schema": [
            *_COMMON_ENVELOPE,
            {"path": "shape_ttl_head", "type": "str", "description": "First 200 chars of the SHACL shape evaluated."},
            {"path": "conforms", "type": "bool", "description": "True when the SHACL report is conformant."},
            {"path": "violations", "type": "list", "description": "Per-focus-node violations, empty on conformant."},
        ],
        "preview": False,
    },
}


def build_evidence_catalog(
    *,
    policy_store: Any,
    verifier_registry: Any | None,
    custom_verifier_store: Any | None,
    tenant_id: str,
) -> list[dict]:
    """Build the Evidence record-types catalog for one tenant."""
    rows: list[dict] = []

    # Track which builtins / customs / inline kinds are actually
    # referenced by a stored policy so we can stamp used_by_policies.
    #
    # Dedup: keep policy-id sets here so a policy with multiple inline
    # requires entries of the same kind (e.g. two requires[].kind ==
    # "regex" entries) stamps the inline_<kind> row once, not twice.
    # The dashboard renders these as React `<span key={pid}>` lists;
    # duplicates trigger duplicate-key warnings and confuse operators
    # ("used by p-A, p-A").
    used_by_step: dict[str, set[str]] = {}
    used_by_inline: dict[str, set[str]] = {}
    for entry in policy_store.load():
        policy = entry.policy
        requires = getattr(policy, "requires", None) or []
        for req in requires:
            kind = getattr(req, "kind", "step")
            if kind == "step":
                step = getattr(req, "step", "")
                if step:
                    used_by_step.setdefault(step, set()).add(policy.id)
            elif kind in ("regex", "llm_critic", "shacl"):
                used_by_inline.setdefault(
                    f"inline_{kind}", set(),
                ).add(policy.id)

    # 1) Built-in verifier evidence rows.
    if verifier_registry is not None:
        from ..verifier.descriptors import get_descriptor
        for v in verifier_registry.all():
            descriptor = get_descriptor(v.step)
            output_evidence = list(_COMMON_ENVELOPE)
            verdict_set: list[str] = ["pass", "review", "deny"]
            if descriptor is not None:
                output_evidence = [
                    {
                        "path": f.get("path", ""),
                        "type": f.get("type", "str"),
                        "description": f.get("description", ""),
                    }
                    for f in descriptor.get("output_evidence", []) or _COMMON_ENVELOPE
                ]
                vs = descriptor.get("verdict_set", None)
                if vs:
                    verdict_set = list(vs)
            rows.append({
                "id": v.step,
                "name": v.step,
                "origin": "builtin",
                "kind": "builtin",
                "description": v.description,
                "verdict_set": verdict_set,
                "payload_schema": output_evidence,
                "used_by_policies": sorted(used_by_step.get(v.step, set())),
                "preview": False,
            })

    # 2) Custom verifier evidence rows (preview — no runtime binding).
    if custom_verifier_store is not None:
        for cv in custom_verifier_store.list_for_tenant(tenant_id):
            rows.append({
                "id": cv.name,
                "name": cv.name,
                "origin": "custom",
                "kind": "custom",
                "description": cv.description,
                "verdict_set": list(cv.verdict_set),
                # Custom verifiers do not declare an emits_shape today
                # — they are preview-only. Surface the common envelope
                # plus a note so the catalog row is not empty.
                "payload_schema": list(_COMMON_ENVELOPE),
                "used_by_policies": sorted(used_by_step.get(cv.name, set())),
                "preview": True,
            })

    # 3) Inline kind rows, one per kind that any stored policy uses.
    for inline_step in sorted(used_by_inline.keys()):
        template = _INLINE_EVIDENCE_SHAPES.get(inline_step)
        if template is None:
            continue
        row = dict(template)
        row["used_by_policies"] = sorted(used_by_inline[inline_step])
        rows.append(row)

    return rows


__all__ = ["build_evidence_catalog"]
