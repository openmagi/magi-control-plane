"""P8: step IR fail-closed resolver.

The REST surface (`PUT /policies/{id}` + `POST /policies/compile`) used to
accept ANY string as a `requires[].step` and stamp `enforcement="missing"`
post hoc — a marker, not a gate. Authors could ship a policy that referenced
a verifier that didn't exist (typos, vendor preview steps, retired
verifiers), and the dashboard would happily list it as `missing` without
ever blocking the PUT. At gate time the runtime would 404 on the unknown
step, silently fail open in some configurations.

This module resolves step references at authoring time with three outcomes:

  - active wired step                 → enforcement="enforcing"
  - explicit `preview:` prefix        → enforcement="preview"   (stamped)
  - vendor-catalog preset, not wired  → 422 "verifier {name} is not active"
  - unknown                           → 422 "verifier {name} not in catalog"

Non-step kinds (regex / llm_critic / shacl) are always "enforcing": the
gate evaluates them inline, there is no notion of "wired vs preview" for
inline conditions.

Policy-level enforcement is `"preview"` if ANY req is preview, else
`"enforcing"`. The label is computed at PUT time and persisted in the
policy store so the dashboard does not need to re-resolve it on every
read (and so an operator who later activates a vendor preset sees a
stable record of what was authored, not what is now wired).

Registry tolerance: when no `VerifierRegistry` is supplied to the cloud
factory (library / hermetic-test path), step validation is skipped — we
return `"enforcing"` deterministically so existing fixtures don't churn.
The strict path engages only when a registry is wired in, which is the
production path (`_build_production_app`).

Production safety (P8 fix-cycle #2): the "no registry → enforcing" branch
is a deploy regression hazard — if production ever boots `create_app`
without a registry (env regression, import error, ops mistake), every
PUT silently passes with `"enforcing"` stamped on a step that does not
exist. The factory enforces a startup invariant: in production, a
registry must be wired or the operator must explicitly set
`MAGI_CP_ALLOW_NO_REGISTRY=1`. Test/library callers pass the registry
they want (or omit it; the env var is unset in test runs so the lenient
path stays a deliberate opt-in, not a silent default).
"""
from __future__ import annotations


from .ir import Policy


PREVIEW_PREFIX = "preview:"
# Hard upper bound on the preview-prefixed step name so a malicious
# author can't round-trip a 20KB suffix through the policy store /
# managed-settings / token bodies. The catalog'd step names cap out
# well under this; the limit is fail-loud (422 at the REST layer) so an
# operator hitting it knows immediately what to trim.
MAX_STEP_NAME_LEN = 128


class StepResolutionError(ValueError):
    """Raised when a step name fails authoring-time resolution.

    Carries `step` (the offending name) and `reason` ("inactive" or
    "unknown") so REST handlers can render distinct 422 messages without
    re-parsing the exception text.
    """

    def __init__(self, step: str, reason: str, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.reason = reason


def _vendor_step_names(vendor_catalog_fn) -> set[str]:
    """Build the set of step names known to the vendor catalog.

    Vendor catalog ids are hyphen-form slugs (`"answer-quality"`); the
    Verifier protocol uses snake_case step names (`"answer_quality"`).
    We accept either form on the wire — authors who copied a vendor id
    verbatim from `/verifiers` and authors who wrote the step name
    directly both hit the same "inactive" branch.
    """
    out: set[str] = set()
    for vp in vendor_catalog_fn():
        out.add(vp.id)
        out.add(vp.id.replace("-", "_"))
    return out


def resolve_step_enforcement(
    step: str,
    *,
    registry,
    vendor_catalog_fn,
) -> str:
    """Resolve a single `requires[].step` name to its enforcement tier.

    Returns `"enforcing"` or `"preview"`. Raises `StepResolutionError` on
    422-worthy inputs (catalog'd-but-inactive, or unknown without
    `preview:` prefix).

    `registry` may be None (hermetic tests / library use); in that case
    every non-preview step resolves to `"enforcing"` and no catalog
    lookup is attempted.
    """
    if not isinstance(step, str) or not step:
        raise StepResolutionError(
            step, "unknown",
            "step name required",
        )
    # P8 fix-cycle non-blocking #3: cap the step name length so a
    # `preview:` prefix author can't smuggle 20KB of garbage through
    # the policy store / managed-settings / token bodies. PolicyIn
    # already caps individual fields at the pydantic boundary, but the
    # IR-level step is unbounded today; this is the canonical limit.
    if len(step) > MAX_STEP_NAME_LEN:
        raise StepResolutionError(
            step, "unknown",
            f"step name too long ({len(step)} > {MAX_STEP_NAME_LEN})",
        )
    # `preview:` prefix is an explicit opt-in to in-development verifiers.
    # The prefix is preserved on the wire / IR so a later reader can tell
    # this row was authored as preview (not retroactively downgraded).
    if step.startswith(PREVIEW_PREFIX):
        return "preview"
    if registry is None:
        # Library / hermetic-test path: no catalog to check against. Treat
        # every non-preview step as enforcing — matches pre-P8 behaviour
        # for fixtures that never registered builtins.
        return "enforcing"
    if registry.get_by_step(step) is not None:
        return "enforcing"
    # Not wired. Is it a known vendor-catalog preset that just hasn't
    # been activated under /presets?
    vendor_steps = _vendor_step_names(vendor_catalog_fn)
    if step in vendor_steps:
        raise StepResolutionError(
            step, "inactive",
            f"verifier {step!r} is not active; either activate it under "
            f"/presets or use 'preview:{step}' prefix to author against it "
            f"while the verifier is in development",
        )
    raise StepResolutionError(
        step, "unknown",
        f"verifier {step!r} not in catalog; pick a step from /verifiers "
        f"or use 'preview:{step}' prefix to author against an "
        f"in-development verifier",
    )


def resolve_policy_enforcement(
    policy: Policy,
    *,
    registry,
    vendor_catalog_fn,
) -> str:
    """Aggregate per-req enforcement into a single policy label.

    Rules:
      - any req resolves to "preview" → policy label is "preview"
      - all reqs resolve to "enforcing" → policy label is "enforcing"
      - empty requires (emit-signal archetype) → policy label is
        "enforcing" (the trigger itself is the deterministic signal;
        the policy IS the contract)

    Raises StepResolutionError on the first req that fails resolution
    — the REST handler converts to 422 with the original step reason
    intact.

    Non-step kinds (regex / llm_critic / shacl) always count as
    "enforcing": the gate evaluates them inline at runtime with no
    registry dependency.
    """
    has_preview = False
    for req in policy.requires:
        if req.kind != "step":
            continue
        tier = resolve_step_enforcement(
            req.step, registry=registry,
            vendor_catalog_fn=vendor_catalog_fn,
        )
        if tier == "preview":
            has_preview = True
    return "preview" if has_preview else "enforcing"


__all__ = [
    "MAX_STEP_NAME_LEN",
    "PREVIEW_PREFIX",
    "StepResolutionError",
    "resolve_step_enforcement",
    "resolve_policy_enforcement",
]
