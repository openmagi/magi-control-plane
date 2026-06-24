"""D56e: Checks catalog — built-in verifiers + custom verifiers + inline checks.

The Rules page reorganizes into three semantically distinct tabs:

  Policies → compositions (PolicyOverride entries the operator edits).
             Sentinel patterns + tool matchers (the policy *targeting*
             info the deprecated Conditions tab surfaced) live on each
             policy's detail card, not here.
  Checks   → pure functions: built-in verifiers, custom verifiers,
             plus inline regex / llm_critic / shacl bodies pulled out
             of policies. This module builds that flat list.
  Evidence → catalog of evidence record types (see evidence_catalog.py).

Scope note: this catalog covers *pure functions* the runtime evaluates
(verifier bodies + inline check bodies). It deliberately does NOT
include sentinel_re patterns or tool matchers — those are *policy
targeting* (which hook event / tool the policy fires on), surfaced on
the Policies tab per-policy. Merging them under "Checks" would blur the
distinction between a check (what is verified) and a target (where the
verification fires).

A "check" is one row on the new Checks tab. Each row carries:

  id              stable identifier (verifier step / verifier name /
                  `<policy_id>:requires[<idx>]:<kind>` for inline)
  name            display label (same as id for built-ins/custom;
                  a short summary for inline rows)
  kind            one of:
                    builtin              — verifier registered via
                                           register_builtins()
                    custom               — tenant-scoped row from
                                           /custom-verifiers
                    inline-regex         — kind=regex inside a policy
                    inline-llm-critic    — kind=llm_critic inside a policy
                    inline-shacl         — kind=shacl inside a policy
  source          "built-in" for builtins, "custom" for custom,
                  the originating policy id for inline rows.
  description     one-line summary suitable for the row card.
  field_checks    optional tree of (path, check_description) pairs.
                  Present on builtins (from descriptors) and on
                  custom (from author input). Empty list for inline.
  used_by_policies  policy ids referencing this check (builtins +
                    customs by name; inline rows are always single-source
                    and the source is already encoded in `source`).
  body            for inline rows only — the raw body the operator
                  authored (regex pattern / llm_critic criterion /
                  shacl shape, truncated for shacl).

Pure-derivation: no separate storage. Entries appear and disappear as
the underlying policies / custom rows are edited.
"""
from __future__ import annotations

from typing import Any

# Inline body cap so a 16k SHACL shape does not pump bytes through the
# catalog endpoint. The dashboard renders this as a code block; the
# full body lives in the originating policy IR.
_INLINE_BODY_PREVIEW_CHARS = 200


def _inline_summary(kind: str, body: str) -> str:
    """One-line operator-facing summary for an inline check row. Kept
    short so the catalog card stays readable on narrow screens."""
    if kind == "regex":
        return "Inline regex pattern matched against the payload text."
    if kind == "llm_critic":
        return "Inline LLM-judged criterion evaluated by the configured reviewer."
    if kind == "shacl":
        return "Inline SHACL shape validated against the payload graph."
    return "Inline policy check."


def _truncate(body: str, cap: int = _INLINE_BODY_PREVIEW_CHARS) -> str:
    body = (body or "").strip()
    if len(body) <= cap:
        return body
    return body[:cap] + " ..."


def build_check_catalog(
    *,
    policy_store: Any,
    verifier_registry: Any | None,
    custom_verifier_store: Any | None,
    tenant_id: str,
) -> list[dict]:
    """Build the merged Checks list for one tenant.

    Order:
      1. built-in verifiers (registry insertion order, stable)
      2. custom verifiers (tenant-scoped, stable by created order)
      3. inline checks per policy (policy id, then requires[] index)

    used_by_policies for built-ins and customs is computed from
    `requires[].step` references to the verifier's step / name; both
    keys are unique within their respective registries by construction.
    """
    rows: list[dict] = []

    # 1) Built-in verifier rows.
    builtin_steps: set[str] = set()
    if verifier_registry is not None:
        # Lazy import — keeps this module standalone-importable from
        # tests that mock the registry.
        from ..verifier.descriptors import get_descriptor
        for v in verifier_registry.all():
            descriptor = get_descriptor(v.step)
            field_checks: list[dict] = []
            if descriptor is not None:
                for fc in descriptor.get("field_checks", []) or []:
                    field_checks.append({
                        "path": fc.get("path", ""),
                        "check_description": fc.get("check_description", ""),
                    })
            # D57c: surface the descriptor's (input_assembly,
            # caller_assembly_hint) pair on the catalog row so the
            # dashboard expander can render the notice without a
            # second descriptor lookup. Defaults to cc_stdin + blank
            # hint when the descriptor is missing (custom-built-ins
            # the cloud has not yet documented).
            input_assembly = "cc_stdin"
            caller_assembly_hint = ""
            if descriptor is not None:
                input_assembly = descriptor.get("input_assembly", "cc_stdin")
                caller_assembly_hint = descriptor.get(
                    "caller_assembly_hint", "",
                )
            rows.append({
                "id": v.step,
                "name": v.step,
                "kind": "builtin",
                "source": "built-in",
                "description": v.description,
                "field_checks": field_checks,
                "used_by_policies": [],
                "body": None,
                "input_assembly": input_assembly,
                "caller_assembly_hint": caller_assembly_hint,
            })
            builtin_steps.add(v.step)

    # 2) Custom verifier rows.
    custom_names: set[str] = set()
    if custom_verifier_store is not None:
        for cv in custom_verifier_store.list_for_tenant(tenant_id):
            rows.append({
                "id": cv.name,
                "name": cv.name,
                "kind": "custom",
                "source": "custom",
                "description": cv.description,
                "field_checks": [
                    {
                        "path": fc.path,
                        "check_description": fc.check_description,
                    }
                    for fc in cv.field_checks
                ],
                "used_by_policies": [],
                "body": None,
                # D57c: forward the author-supplied (input_assembly,
                # caller_assembly_hint) pair so the dashboard catalog
                # row renders the same notice it does for built-ins.
                "input_assembly": cv.input_assembly,
                "caller_assembly_hint": cv.caller_assembly_hint,
            })
            custom_names.add(cv.name)

    # Walk every policy once: stamp used_by on the builtins/customs
    # whose step is referenced, and emit one inline row per inline
    # requires[] entry.
    #
    # Dedup: a single policy can declare multiple requires[].step
    # entries pointing at the same step (e.g. two regex requires of
    # the same kind, or two separate requires both binding citation_verify
    # under different verdict conditions). Stamp each policy id at most
    # once per step so used_by_policies stays a true set — duplicates
    # leak into JSX `key=` and confuse operators.
    inline_rows: list[dict] = []
    used_by: dict[str, set[str]] = {}
    for entry in policy_store.load():
        policy = entry.policy
        # Skip archetypes that have no `requires` (permission /
        # mcp_gating / subagent / context_injection). They never carry
        # inline checks and the policy_store loader does not gate the
        # field, so guard with hasattr to keep this catalog generic.
        requires = getattr(policy, "requires", None) or []
        for idx, req in enumerate(requires):
            kind = getattr(req, "kind", "step")
            if kind == "step":
                step = getattr(req, "step", "")
                if step:
                    used_by.setdefault(step, set()).add(policy.id)
                continue
            if kind == "regex":
                body = getattr(req, "pattern", "") or ""
                row_kind = "inline-regex"
                summary = _inline_summary("regex", body)
            elif kind == "llm_critic":
                body = getattr(req, "criterion", "") or ""
                row_kind = "inline-llm-critic"
                summary = _inline_summary("llm_critic", body)
            elif kind == "shacl":
                body = getattr(req, "shape_ttl", "") or ""
                row_kind = "inline-shacl"
                summary = _inline_summary("shacl", body)
            else:
                # Unknown kind — skip. The IR validate() will refuse it
                # at PUT time, but the catalog should be defensive
                # against partially-loaded historical rows.
                continue
            inline_rows.append({
                "id": f"{policy.id}:requires[{idx}]:{kind}",
                "name": _truncate(body, cap=80) or row_kind,
                "kind": row_kind,
                "source": policy.id,
                "description": summary,
                "field_checks": [],
                "used_by_policies": [policy.id],
                "body": _truncate(body),
                # D57c: inline rows have no input-assembly contract
                # (the check IS the body, no verifier seam). Stamp
                # default cc_stdin + blank hint so the row schema
                # stays consistent across kinds for the catalog
                # consumers (TS type + dashboard renderer).
                "input_assembly": "cc_stdin",
                "caller_assembly_hint": "",
            })

    # Stamp used_by on built-in / custom rows. Sorted for deterministic
    # output (set iteration is otherwise hash-order). Tests and the
    # dashboard both prefer stable ordering.
    for row in rows:
        if row["kind"] == "builtin":
            row["used_by_policies"] = sorted(used_by.get(row["id"], set()))
        elif row["kind"] == "custom":
            row["used_by_policies"] = sorted(used_by.get(row["id"], set()))

    rows.extend(inline_rows)
    return rows


__all__ = ["build_check_catalog"]
