"""v1.1-PA — Verifier protocol + Verdict spec + Registry.

The shape that every verifier (current 5, future 36) must conform to.
Mirrors magi-agent's evidence-contract shape but for out-of-loop terminal-gate
semantics: each verifier returns a Verdict that the cloud signs into a token.
"""
import pytest

from magi_cp.verifier.protocol import (
    Verdict, VerifierInput, Verifier, Enforcement,
    VerifierRegistry,
)


# ── Verdict ──────────────────────────────────────────────────────────
def test_verdict_pass_is_terminal_ok():
    v = Verdict(status="pass", reasons=[])
    assert v.status == "pass"
    assert not v.reasons


def test_verdict_review_carries_reasons():
    v = Verdict(status="review", reasons=["misquote suspected"])
    assert v.status == "review"
    assert "misquote" in v.reasons[0]


def test_verdict_deny_locks_terminal():
    v = Verdict(status="deny", reasons=["hallucinated case"])
    assert v.status == "deny"


def test_verdict_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        Verdict(status="maybe", reasons=[])


# ── Enforcement label (4-tier from magi-agent preset_map) ────────────
def test_enforcement_labels_are_canonical():
    """preset_map.enforcement_for 4-tier mirror."""
    assert Enforcement.enforcing.value == "enforcing"
    assert Enforcement.always_on.value == "always-on"
    assert Enforcement.preview.value == "preview"
    assert Enforcement.capability.value == "capability"


# ── Verifier registry ────────────────────────────────────────────────
class _StubVerifier:
    name = "stub_v1"
    step = "stub_check"
    category = "FACT"
    enforcement = Enforcement.enforcing
    description = "test stub"
    input_schema = {"type": "object"}

    def run(self, payload: dict) -> Verdict:
        return Verdict(status="pass", reasons=[])


class _AnotherStub:
    name = "stub_v2"
    step = "another_check"
    category = "OUTPUT"
    enforcement = Enforcement.preview
    description = "preview-only stub"
    input_schema = {"type": "object"}

    def run(self, payload: dict) -> Verdict:
        return Verdict(status="review", reasons=["needs review"])


def test_registry_register_and_lookup():
    r = VerifierRegistry()
    r.register(_StubVerifier())
    assert r.get("stub_v1").step == "stub_check"
    assert r.get("ghost") is None


def test_registry_lookup_by_step_for_policy_ir():
    """Policy IR `requires[].step` looks up the verifier by step name."""
    r = VerifierRegistry()
    r.register(_StubVerifier())
    v = r.get_by_step("stub_check")
    assert v is not None and v.name == "stub_v1"


def test_registry_rejects_duplicate_name():
    r = VerifierRegistry()
    r.register(_StubVerifier())
    with pytest.raises(ValueError, match="duplicate"):
        r.register(_StubVerifier())


def test_registry_rejects_duplicate_step():
    """Two verifiers cannot claim the same `step` (Policy IR ambiguity)."""
    r = VerifierRegistry()
    r.register(_StubVerifier())
    class _Clone:
        name = "different_name"
        step = "stub_check"   # SAME step
        category = "FACT"
        enforcement = Enforcement.enforcing
        description = ""
        input_schema = {"type": "object"}
        def run(self, payload): return Verdict("pass", [])
    with pytest.raises(ValueError, match="step"):
        r.register(_Clone())


def test_registry_list_all_in_registration_order():
    r = VerifierRegistry()
    r.register(_StubVerifier())
    r.register(_AnotherStub())
    names = [v.name for v in r.all()]
    assert names == ["stub_v1", "stub_v2"]


def test_registry_filter_by_enforcement():
    r = VerifierRegistry()
    r.register(_StubVerifier())
    r.register(_AnotherStub())
    enforcing = list(r.filter_by_enforcement(Enforcement.enforcing))
    previews = list(r.filter_by_enforcement(Enforcement.preview))
    assert len(enforcing) == 1 and enforcing[0].name == "stub_v1"
    assert len(previews) == 1 and previews[0].name == "stub_v2"


def test_registry_filter_by_category():
    r = VerifierRegistry()
    r.register(_StubVerifier())
    r.register(_AnotherStub())
    fact = list(r.filter_by_category("FACT"))
    assert len(fact) == 1 and fact[0].name == "stub_v1"


# ── Verifier protocol structural compliance ─────────────────────────
def test_verifier_protocol_attrs_required():
    """A class without `step` cannot register."""
    r = VerifierRegistry()
    class _Missing:
        name = "missing_step"
        # step missing
        category = "FACT"
        enforcement = Enforcement.enforcing
        description = ""
        input_schema = {"type": "object"}
        def run(self, payload): return Verdict("pass", [])
    with pytest.raises(TypeError):
        r.register(_Missing())   # type: ignore[arg-type]


# ── stronger shape validation (reviewer follow-up) ─────────────────
def test_register_rejects_empty_name():
    r = VerifierRegistry()
    class _Empty:
        name = ""
        step = "x"
        category = "FACT"
        enforcement = Enforcement.enforcing
        description = ""
        input_schema = {"type": "object"}
        def run(self, payload): return Verdict("pass", [])
    with pytest.raises(TypeError, match="name"):
        r.register(_Empty())


def test_register_rejects_none_step():
    r = VerifierRegistry()
    class _NoneStep:
        name = "v"
        step = None
        category = "FACT"
        enforcement = Enforcement.enforcing
        description = ""
        input_schema = {"type": "object"}
        def run(self, payload): return Verdict("pass", [])
    with pytest.raises(TypeError, match="step"):
        r.register(_NoneStep())   # type: ignore[arg-type]


def test_register_rejects_non_enum_enforcement():
    r = VerifierRegistry()
    class _BadEnf:
        name = "v"
        step = "x"
        category = "FACT"
        enforcement = "enforcing"   # string, not Enforcement
        description = ""
        input_schema = {"type": "object"}
        def run(self, payload): return Verdict("pass", [])
    with pytest.raises(TypeError, match="enforcement"):
        r.register(_BadEnf())   # type: ignore[arg-type]
