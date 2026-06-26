"""D54: prebuilt policy templates exposed on the Policies tab.

A verifier (citation_verify, privilege_scan, source_allowlist,
structured_output, prompt_injection_screen) is a pure function: given
inputs, it computes a verdict. A policy is the composition: when verdict
X arrives on the (event, matcher) the policy binds, take action Y.

The dashboard's pre-D54 Verifiers tab leaked this distinction by carrying
policy-decision language ("hard gate", "deny on missing", "advisory") and
an "enforcing" status pill onto the verifier card. The card now sticks to
the algorithm; the sensible-default *policies* that pair each verifier
with a typical (event, matcher, action) live here.

Why this is data, not code:

  - The 5 entries are templates the operator REVIEWS, edits if needed,
    and saves through the regular /policies POST. They are NOT
    auto-installed. The "Use this" button on the dashboard links to
    /policies/new?mode=advanced&draft=<encoded JSON> so the
    PolicyBuilder picks the prefill up like any other draft.
  - Keeping the data here (rather than baking it into the verifier
    classes) preserves the verifier = function invariant. A verifier can
    be paired with a different (event, matcher, action) by another
    policy without touching the verifier description.

Matrix constraints (policy/matrix.py::LEGAL_COMBINATIONS) bound the
templates:

  - Stop event accepts wildcard matcher + audit action only. The
    citation_verify template therefore lands as `audit` at Stop. The
    operator who wants a stronger gate can flip the action in the
    PolicyBuilder before saving (and accept that the runtime gate will
    refuse the combination because Stop fires after the response is
    composed; this is the matrix telling the truth, not a UI
    limitation).
  - PostToolUse accepts tool/mcp_tool matchers + audit action only. The
    structured_output and prompt_injection_screen templates land on
    specific tools.
  - PreToolUse accepts block / ask / audit on a specific tool. The
    privilege_scan template lands as `audit` on Bash and the
    source_allowlist template lands as `block` on WebFetch.

These choices match the intent the brief sketches while staying inside
the matrix. The templates compile cleanly through the existing
Policy.validate() + matrix.validate_combination() path; the test
fixtures in tests/test_policy_prebuilt.py assert the round-trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from .ir import EvidencePolicy, EvidenceReq, Trigger, policy_to_dict


class PrebuiltPolicy(TypedDict, total=False):
    """One prebuilt policy entry.

    `id`              : short stable slug. With D60 this also doubles as
                        the saved policy id when the operator flips the
                        toggle (enable/disable round-trips against the
                        prebuilt id directly).
    `title`           : operator-facing label, short.
    `summary`         : one-sentence "what this policy does in practice".
                        Plain English; mirrors the i18n strings on the
                        Policies tab.
    `verifier_step`   : the step name of the verifier this policy binds.
    `ir`              : the policy IR as a dict (Policy to policy_to_dict).
                        Same shape the PolicyBuilder accepts as a draft.
    `enabled`         : D60 — true when a saved policy with this prebuilt
                        id is currently in the store AND its enabled flag
                        is on. The dashboard renders the toggle from this
                        bit and POSTs to /policies/prebuilt/{id}/enable to
                        flip it.
    `setup_required`  : D60 — true when the prebuilt's IR references
                        verifier knobs that the operator MUST configure
                        before the policy does anything useful (e.g.
                        `source_allowlist` needs an allowlist,
                        `citation_verify` needs a corpus override). The
                        dashboard surfaces an inline "needs setup"
                        callout before letting the operator enable.
    `setup_hint`      : D60 — short plain-English hint shown next to the
                        callout, telling the operator WHICH knob to set.
                        Empty string when `setup_required` is False.
    """

    id: str
    title: str
    summary: str
    verifier_step: str
    ir: dict
    enabled: bool
    setup_required: bool
    setup_hint: str


@dataclass(frozen=True)
class _PrebuiltSpec:
    """Authoring-time tuple. Converted to PrebuiltPolicy at request time.

    The dataclass holds the parts that vary per row; the
    `_build_evidence_policy` helper assembles the IR with the shared
    defaults (sentinel_re=None, on_signature_invalid="deny",
    gate_binary=DEFAULT, version="0.1").

    `setup_required` defaults to False; only the two verifiers whose
    IR carries operator-supplied knobs (source allowlist, citation
    corpus override) flip it on. `setup_hint` is the short hint copy
    rendered next to the inline "needs setup" callout."""

    id: str
    title: str
    summary: str
    description: str
    event: str
    matcher: str
    action: str
    verifier_step: str
    setup_required: bool = False
    setup_hint: str = ""


_PREBUILT_SPECS: tuple[_PrebuiltSpec, ...] = (
    _PrebuiltSpec(
        id="prebuilt/citation-verify-at-final",
        title="Audit citations on final answer",
        summary=(
            "Check legal citations against the source corpus once "
            "before the agent finishes its reply, and write the "
            "verdict to the audit ledger. The Stop hook fires after "
            "the response is composed, so this template records "
            "rather than blocking the response. Pair with "
            "/verify_inline if you need to block before the response "
            "is composed."
        ),
        description=(
            "Verify legal citations against the source corpus on the "
            "agent's final answer and record the verdict to the ledger."
        ),
        event="Stop",
        matcher="*",
        action="audit",
        verifier_step="citation_verify",
    ),
    _PrebuiltSpec(
        id="prebuilt/privilege-scan-bash",
        title="Audit privilege-scan hits on Bash",
        summary=(
            "Scan the command body of every Bash invocation for "
            "attorney-client privilege markers, work-product flags, "
            "and Korean RRN patterns. Records the verdict to the "
            "audit ledger without blocking the tool run. Switch the "
            "action to 'block' in the editor if your environment "
            "treats these as hard policy violations rather than "
            "review-only signals."
        ),
        description=(
            "Scan Bash command bodies for privilege markers, work "
            "product flags, and Korean RRN patterns. Record the "
            "verdict to the ledger."
        ),
        event="PreToolUse",
        matcher="Bash",
        action="audit",
        verifier_step="privilege_scan",
    ),
    _PrebuiltSpec(
        id="prebuilt/source-allowlist-webfetch",
        title="Block fetch to non-allowlist domains",
        summary=(
            "Check every WebFetch URL against the configured allowlist "
            "before the request fires. Blocks the tool call when the "
            "host (or its parent domain) is not on the list."
        ),
        description=(
            "Block WebFetch when the destination host is not in the "
            "configured source allowlist."
        ),
        event="PreToolUse",
        matcher="WebFetch",
        action="block",
        verifier_step="source_allowlist",
        setup_required=True,
        setup_hint=(
            "Provide the allowlist of domains the agent may fetch "
            "from. With an empty allowlist this policy blocks every "
            "WebFetch call, which is rarely what you want."
        ),
    ),
    _PrebuiltSpec(
        id="prebuilt/structured-output-at-final",
        title="Audit malformed structured final answers",
        summary=(
            "Validate the agent's final answer against the configured "
            "JSON-Schema subset. The Stop hook fires after the reply "
            "is composed, so this template records the verdict to the "
            "audit ledger when the structure does not match."
        ),
        description=(
            "Validate the agent's final answer against the configured "
            "JSON-Schema subset and record the verdict to the ledger."
        ),
        event="Stop",
        matcher="*",
        action="audit",
        verifier_step="structured_output",
    ),
    _PrebuiltSpec(
        id="prebuilt/prompt-injection-webfetch",
        title="Audit prompt-injection attempts in fetched content",
        summary=(
            "Scan every WebFetch response for prompt-injection "
            "attempts (override verbs, role-tag injection, jailbreak "
            "markers) before the body joins the agent's context. "
            "Records the verdict to the audit ledger."
        ),
        description=(
            "Scan WebFetch responses for prompt-injection attempts "
            "and record the verdict to the ledger."
        ),
        event="PostToolUse",
        matcher="WebFetch",
        action="audit",
        verifier_step="prompt_injection_screen",
    ),
)


def _build_evidence_policy(spec: _PrebuiltSpec) -> EvidencePolicy:
    """Assemble an EvidencePolicy from one spec. Construction calls
    Policy.validate(), so an illegal trigger × matcher × action triple
    fails at import time instead of at the request boundary. That is
    the gate we want: a prebuilt that can't load is a bug, not a
    runtime surprise.
    """
    return EvidencePolicy(
        id=spec.id,
        description=spec.description,
        trigger=Trigger(host="claude-code", event=spec.event, matcher=spec.matcher),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step=spec.verifier_step, verdict="pass")],
        action=spec.action,  # type: ignore[arg-type]
        on_signature_invalid="deny",
        gate_binary="/usr/local/bin/magi-gate.sh",
        version="0.1",
    )


def all_prebuilt_policies(
    enabled_ids: set[str] | None = None,
) -> list[PrebuiltPolicy]:
    """Return the 5 prebuilt policy entries in stable order.

    Order matches the brief: citation, privilege, source allowlist,
    structured output, prompt injection. The dashboard renders them in
    the order returned so an operator scanning the section sees the
    same ordering each visit.

    `enabled_ids` is the set of policy ids that are currently saved
    AND enabled in the tenant policy store. D60 — the toggle on each
    card reads from this bit. Pass None (default) when only the
    catalog metadata matters (e.g. catalog tests); the cloud route
    passes the live store's enabled-id set so the dashboard renders
    the right toggle state without a second round-trip.
    """
    ids = enabled_ids or set()
    out: list[PrebuiltPolicy] = []
    for spec in _PREBUILT_SPECS:
        policy = _build_evidence_policy(spec)
        out.append({
            "id": spec.id,
            "title": spec.title,
            "summary": spec.summary,
            "verifier_step": spec.verifier_step,
            "ir": policy_to_dict(policy),
            "enabled": spec.id in ids,
            "setup_required": spec.setup_required,
            "setup_hint": spec.setup_hint,
        })
    return out


def prebuilt_spec_by_id(prebuilt_id: str) -> _PrebuiltSpec | None:
    """Lookup helper for the enable/disable endpoints. Returns None if
    the requested id is not in the catalog so the cloud can 404 cleanly
    rather than synthesizing a policy from an unknown spec."""
    for spec in _PREBUILT_SPECS:
        if spec.id == prebuilt_id:
            return spec
    return None


def build_prebuilt_evidence_policy(prebuilt_id: str) -> EvidencePolicy | None:
    """Materialize the prebuilt's EvidencePolicy by id. D60 — the cloud
    POST /policies/prebuilt/{id}/enable route calls this to obtain the
    policy to persist in the tenant's policy store. None when the id
    is not in the catalog."""
    spec = prebuilt_spec_by_id(prebuilt_id)
    if spec is None:
        return None
    return _build_evidence_policy(spec)


def _assert_all_validate() -> None:
    """Module-import-time guard. Constructing every spec exercises
    Policy.validate() + matrix.validate_combination(). If a future
    matrix tweak makes one of the templates illegal we want to know at
    boot, not at the first dashboard render.

    D60 follow-up: also exercise
    `validate_policy_against_descriptors`, which is the lifecycle
    endorsement check the cloud's enable handler runs at request
    time. The two checks catch different classes of bug:

      - `Policy.validate()` covers structural / matrix correctness.
      - `validate_policy_against_descriptors` covers
        (trigger.event, requires[].step) lifecycle endorsement
        against the descriptor surface — i.e. "does this verifier
        actually fire on this event?".

    Without the second check, a future spec whose verifier
    descriptor stops endorsing the spec's lifecycle (descriptor
    mirror lag, deliberate decommission, etc.) imports cleanly and
    silently 422s on the operator's first toggle click in
    production. Boot-time is the right time to surface it.
    """
    # Local import to keep prebuilt.py importable from contexts that
    # haven't initialized verifier descriptors yet (the descriptor
    # module is import-cheap, but ordering matters under reload).
    from ..verifier.descriptors import (
        validate_policy_against_descriptors,
    )
    for spec in _PREBUILT_SPECS:
        policy = _build_evidence_policy(spec)
        issues = validate_policy_against_descriptors(
            policy_id=policy.id,
            trigger_event=policy.trigger.event,
            step_refs=[
                req.step
                for req in policy.requires
                if req.kind == "step"
                and isinstance(req.step, str)
            ],
        )
        if issues:
            first = issues[0]
            raise RuntimeError(
                f"prebuilt {spec.id!r}: verifier "
                f"{first['step']!r} does not fire on "
                f"{first['trigger_event']!r}; allowed: "
                f"{first['allowed_events']!r}"
            )


_assert_all_validate()


__all__ = [
    "PrebuiltPolicy",
    "all_prebuilt_policies",
    "prebuilt_spec_by_id",
    "build_prebuilt_evidence_policy",
]
