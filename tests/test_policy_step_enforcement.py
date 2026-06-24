"""P8: step IR fail-closed resolver unit tests.

Boundary behaviour (REST surface) is exercised in test_policies_api.py;
this file pins the resolver's individual decision matrix so a future
refactor that changes the catalog walk semantics fails fast at the unit
level."""
from __future__ import annotations

import pytest

from magi_cp.cloud.presets_catalog import vendor_catalog
from magi_cp.policy.ir import EvidenceReq, Policy, Trigger
from magi_cp.policy.step_enforcement import (
    PREVIEW_PREFIX,
    StepResolutionError,
    resolve_policy_enforcement,
    resolve_step_enforcement,
)
from magi_cp.verifier.builtins import register_builtins
from magi_cp.verifier.protocol import VerifierRegistry


@pytest.fixture
def registry() -> VerifierRegistry:
    reg = VerifierRegistry()
    register_builtins(reg)
    return reg


def _policy_with_step(step: str) -> Policy:
    return Policy(
        id="x", description="",
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        sentinel_re=None,
        requires=[EvidenceReq(kind="step", step=step, verdict="pass")],
        action="block",
    )


# ── resolve_step_enforcement ─────────────────────────────────────────
def test_active_wired_step_resolves_to_enforcing(registry):
    out = resolve_step_enforcement(
        "citation_verify", registry=registry,
        vendor_catalog_fn=vendor_catalog,
    )
    assert out == "enforcing"


def test_preview_prefix_resolves_to_preview(registry):
    out = resolve_step_enforcement(
        f"{PREVIEW_PREFIX}my_future_check",
        registry=registry, vendor_catalog_fn=vendor_catalog,
    )
    assert out == "preview"


def test_preview_prefix_wins_over_registry_lookup(registry):
    """Even when the bare name IS in the registry, preview prefix means
    the author wants it treated as preview (e.g., dogfooding the prefix
    on a step that just landed in main but not in their build). The
    semantics are author-declared, not derived."""
    out = resolve_step_enforcement(
        f"{PREVIEW_PREFIX}citation_verify",
        registry=registry, vendor_catalog_fn=vendor_catalog,
    )
    assert out == "preview"


def test_vendor_catalog_only_step_raises_inactive(registry):
    with pytest.raises(StepResolutionError) as ei:
        resolve_step_enforcement(
            "answer_quality", registry=registry,
            vendor_catalog_fn=vendor_catalog,
        )
    assert ei.value.reason == "inactive"
    assert "not active" in str(ei.value)
    assert "preview:" in str(ei.value)


def test_vendor_hyphen_form_also_recognized_as_inactive(registry):
    """Authors who copy the id verbatim from /verifiers (hyphen form)
    hit the same inactive branch as authors who type the snake_case
    step name — both paths show the same actionable error."""
    with pytest.raises(StepResolutionError) as ei:
        resolve_step_enforcement(
            "answer-quality", registry=registry,
            vendor_catalog_fn=vendor_catalog,
        )
    assert ei.value.reason == "inactive"


def test_unknown_step_raises_unknown(registry):
    with pytest.raises(StepResolutionError) as ei:
        resolve_step_enforcement(
            "this_step_does_not_exist_anywhere",
            registry=registry, vendor_catalog_fn=vendor_catalog,
        )
    assert ei.value.reason == "unknown"
    assert "not in catalog" in str(ei.value)


def test_no_registry_skips_strict_validation():
    """Hermetic / library path — when no registry is wired, every
    non-preview step resolves to enforcing. The strict gate only fires
    in production (where a registry IS wired)."""
    out = resolve_step_enforcement(
        "definitely_not_real", registry=None,
        vendor_catalog_fn=vendor_catalog,
    )
    assert out == "enforcing"


def test_empty_step_name_rejected_as_unknown():
    with pytest.raises(StepResolutionError) as ei:
        resolve_step_enforcement("", registry=None,
                                  vendor_catalog_fn=vendor_catalog)
    assert ei.value.reason == "unknown"


# ── resolve_policy_enforcement ───────────────────────────────────────
def test_policy_with_all_enforcing_reqs_is_enforcing(registry):
    p = _policy_with_step("citation_verify")
    assert resolve_policy_enforcement(
        p, registry=registry, vendor_catalog_fn=vendor_catalog,
    ) == "enforcing"


def test_policy_with_any_preview_req_is_preview(registry):
    p = Policy(
        id="mixed", description="",
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        sentinel_re=None,
        requires=[
            EvidenceReq(kind="step", step="citation_verify", verdict="pass"),
            EvidenceReq(kind="step", step=f"{PREVIEW_PREFIX}future", verdict="pass"),
        ],
        action="block",
    )
    assert resolve_policy_enforcement(
        p, registry=registry, vendor_catalog_fn=vendor_catalog,
    ) == "preview"


def test_policy_propagates_step_resolution_error(registry):
    """A single bad req fails the whole resolve so the REST handler
    can turn it into a 422 — partial-success would let a typo land."""
    p = Policy(
        id="bad", description="",
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        sentinel_re=None,
        requires=[
            EvidenceReq(kind="step", step="citation_verify", verdict="pass"),
            EvidenceReq(kind="step", step="answer_quality", verdict="pass"),
        ],
        action="block",
    )
    with pytest.raises(StepResolutionError) as ei:
        resolve_policy_enforcement(
            p, registry=registry, vendor_catalog_fn=vendor_catalog,
        )
    assert ei.value.step == "answer_quality"
    assert ei.value.reason == "inactive"


def test_policy_with_only_non_step_reqs_skips_resolution(registry):
    """regex / llm_critic / shacl don't bind to a verifier — the
    aggregator returns enforcing for the empty (non-step) case so the
    REST layer can fall back to its (action, event) label."""
    p = Policy(
        id="non-step", description="",
        trigger=Trigger(host="claude-code", event="PreToolUse", matcher="Bash"),
        sentinel_re=None,
        requires=[EvidenceReq(kind="regex", pattern=r"\bsecret\b")],
        action="block",
    )
    out = resolve_policy_enforcement(
        p, registry=registry, vendor_catalog_fn=vendor_catalog,
    )
    assert out == "enforcing"


def test_policy_with_empty_requires_is_enforcing(registry):
    """Emit-signal archetype (audit + empty requires) — the trigger
    itself is the contract; nothing to be preview about."""
    p = Policy(
        id="emit", description="",
        trigger=Trigger(host="claude-code", event="PostToolUse", matcher="Bash"),
        sentinel_re=None,
        requires=[],
        action="audit",
    )
    out = resolve_policy_enforcement(
        p, registry=registry, vendor_catalog_fn=vendor_catalog,
    )
    assert out == "enforcing"
