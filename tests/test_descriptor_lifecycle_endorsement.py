"""D57e P0/P1: tests for the descriptor lifecycle-endorsement gate +
saved-policy drift sweep introduced as the D57e follow-up.

Three layers are covered:

  1. `validate_policy_against_descriptors()` (Python helper that owns
     the per-policy drift detection).
  2. PUT /policies and PATCH /policies/{id}/enabled lifecycle-
     endorsement gate (refuses persistence of cross-lifecycle drift,
     surfaces it on re-arm).
  3. Boot-time drift sweep emits a structured warning per drifted
     row when `_build_production_app` walks a stale on-disk store.

These complement the existing import-time descriptor gate
(`_assert_field_checks_paths_resolve`) which protects descriptor-
authoring drift only.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.verifier.builtins import register_builtins
from magi_cp.verifier.descriptors import (
    field_checks_flat,
    validate_policy_against_descriptors,
)
from magi_cp.verifier.protocol import VerifierRegistry


API_KEY = "lc-api-key"
HITL_KEY = "lc-hitl-key"
ADMIN_KEY = "lc-admin-key"
HDR_ADMIN = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", API_KEY)
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", HITL_KEY)
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client_with_registry(tmp_path):
    """Production-like client wired to the live verifier registry so the
    new endorsement gate has real descriptors to consult."""
    ks = KeyStore(dir=str(tmp_path / "keys"))
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(
        keystore=ks,
        dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        verifier_registry=reg,
    )
    return TestClient(app)


def _policy(
    *,
    pid="p/v1",
    event,
    matcher,
    action,
    step,
):
    return {
        "id": pid,
        "description": "t",
        "version": "0.1",
        "trigger": {"host": "claude-code", "event": event, "matcher": matcher},
        "requires": [{"step": step, "verdict": "pass"}],
        "action": action,
        "on_signature_invalid": "deny",
        "gate_binary": "/usr/local/bin/magi-gate.sh",
    }


# ── legacy-shape back-compat (P2) ───────────────────────────────────
class TestFieldChecksFlatLegacyShape:
    def test_field_checks_flat_handles_legacy_list_shape(self):
        """D57e P2: a custom-verifier row or older mirror copy may
        ship `field_checks` as a flat list (the pre-D57e shape). The
        helper must return a defensive copy of the list instead of
        crashing with `AttributeError: 'list' object has no attribute
        'items'`."""
        legacy = {
            "step": "old_custom",
            "field_checks": [
                {"path": "tool_input.url", "check_description": "x"},
                {"path": "tool_input.command", "check_description": "y"},
            ],
        }
        out = field_checks_flat(legacy)  # type: ignore[arg-type]
        assert len(out) == 2
        assert out[0]["path"] == "tool_input.url"
        # Mutating the returned list does not mutate the descriptor.
        out.append({"path": "z", "check_description": "z"})
        assert len(legacy["field_checks"]) == 2

    def test_field_checks_flat_handles_grouped_shape(self):
        """D57e: the canonical grouped shape walks insertion order."""
        grouped = {
            "step": "x",
            "field_checks": {
                "PreToolUse": [
                    {"path": "tool_input.command", "check_description": "c"},
                ],
                "Stop": [
                    {"path": "final_message", "check_description": "f"},
                ],
            },
        }
        out = field_checks_flat(grouped)  # type: ignore[arg-type]
        assert [r["path"] for r in out] == ["tool_input.command", "final_message"]

    def test_field_checks_flat_handles_empty_descriptor(self):
        assert field_checks_flat({}) == []  # type: ignore[arg-type]
        assert field_checks_flat({"field_checks": {}}) == []  # type: ignore[arg-type]


# ── validate_policy_against_descriptors ──────────────────────────────
class TestValidator:
    def test_lifecycle_pruned_combination_is_flagged(self):
        """citation_verify is Stop-only; a (PostToolUse,
        citation_verify) pair is the canonical pre-D57e drift case."""
        issues = validate_policy_against_descriptors(
            policy_id="after-tool-use-cite/v1",
            trigger_event="PostToolUse",
            step_refs=["citation_verify"],
        )
        assert len(issues) == 1
        issue = issues[0]
        assert issue["policy_id"] == "after-tool-use-cite/v1"
        assert issue["step"] == "citation_verify"
        assert issue["trigger_event"] == "PostToolUse"
        assert issue["allowed_events"] == ["Stop"]
        assert issue["reason"] == "lifecycle_pruned"

    def test_endorsed_combination_is_silent(self):
        """(Stop, citation_verify) is endorsed; no issues."""
        issues = validate_policy_against_descriptors(
            policy_id="cite/v1",
            trigger_event="Stop",
            step_refs=["citation_verify"],
        )
        assert issues == []

    def test_preview_prefix_is_skipped(self):
        """preview:foo is an explicit opt-in to no-runtime-guarantees;
        the lifecycle gate does not consult descriptors for it."""
        issues = validate_policy_against_descriptors(
            policy_id="preview/v1",
            trigger_event="Stop",
            step_refs=["preview:future_check"],
        )
        assert issues == []

    def test_unknown_step_is_skipped(self):
        """A step with no registered descriptor (custom verifier,
        vendor preset whose mirror lags) falls through to the existing
        step_enforcement gate instead of generating a spurious drift
        record here."""
        issues = validate_policy_against_descriptors(
            policy_id="custom/v1",
            trigger_event="Stop",
            step_refs=["completely_unknown_verifier"],
        )
        assert issues == []

    def test_privilege_scan_walks_four_lifecycles(self):
        """privilege_scan declares PreToolUse/PostToolUse/Stop/
        UserPromptSubmit groups. Any of those endorses the row;
        SubagentStop / SessionStart drift."""
        for ev in ("PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit"):
            assert validate_policy_against_descriptors(
                policy_id="p/v1",
                trigger_event=ev,
                step_refs=["privilege_scan"],
            ) == []
        for ev in ("SubagentStop", "PreCompact", "SessionStart", "SessionEnd"):
            issues = validate_policy_against_descriptors(
                policy_id="p/v1",
                trigger_event=ev,
                step_refs=["privilege_scan"],
            )
            assert len(issues) == 1
            assert issues[0]["reason"] == "lifecycle_pruned"


# ── PUT /policies refuses lifecycle-drift bodies ────────────────────
class TestPutGate:
    def test_put_rejects_post_tool_use_citation_verify_with_422(
        self, client_with_registry,
    ):
        """The canonical pre-D57e drift case: a curl body authoring
        `(PostToolUse, citation_verify)` must 422 with the allowed
        lifecycles in the error body so the operator can remediate
        without a second round-trip."""
        body = _policy(
            event="PostToolUse",
            matcher="Bash",
            action="audit",
            step="citation_verify",
        )
        r = client_with_registry.put(
            "/policies/p/v1",
            json={"policy": body, "source": "org", "enabled": True},
            headers=HDR_ADMIN,
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert "citation_verify" in detail
        assert "PostToolUse" in detail
        assert "Stop" in detail

    def test_put_accepts_stop_citation_verify(self, client_with_registry):
        """(Stop, citation_verify) is the endorsed pairing."""
        body = _policy(
            pid="cite/v1",
            event="Stop",
            matcher="*",
            action="audit",
            step="citation_verify",
        )
        r = client_with_registry.put(
            "/policies/cite/v1",
            json={"policy": body, "source": "org", "enabled": True},
            headers=HDR_ADMIN,
        )
        assert r.status_code == 200, r.text

    def test_put_accepts_pre_tool_use_privilege_scan(self, client_with_registry):
        body = _policy(
            event="PreToolUse",
            matcher="Bash",
            action="block",
            step="privilege_scan",
        )
        r = client_with_registry.put(
            "/policies/p/v1",
            json={"policy": body, "source": "org", "enabled": True},
            headers=HDR_ADMIN,
        )
        assert r.status_code == 200, r.text


# ── PATCH /enabled re-arm refuses drift with 409 ────────────────────
class TestPatchEnabledGate:
    def test_re_enable_drift_returns_409_with_allowed_list(
        self, tmp_path,
    ):
        """A row authored before D57e with `(PostToolUse,
        citation_verify)` can be seeded directly on disk (bypassing
        the PUT gate). PATCH /enabled re-arm must refuse with 409 +
        the allowed lifecycles."""
        store_path = tmp_path / "policies.json"
        # Seed pre-D57e drift directly. The store format mirrors what
        # /policy/resolved.PolicyOverride round-trips through
        # _evidence_req_to_dict; here we hand-write the JSON.
        store_path.write_text(json.dumps([
            {
                "policy": {
                    "type": "evidence",
                    "id": "drifted/v1",
                    "description": "pre-D57e row",
                    "version": "0.1",
                    "trigger": {
                        "host": "claude-code",
                        "event": "PostToolUse",
                        "matcher": "Bash",
                    },
                    "requires": [
                        {"kind": "step", "step": "citation_verify",
                         "verdict": "pass"},
                    ],
                    "action": "audit",
                    "on_signature_invalid": "deny",
                    "gate_binary": "/usr/local/bin/magi-gate.sh",
                },
                "source": "org",
                "enabled": False,
                # Pre-D57e on-disk rows already carry an enforcement
                # label (the lifecycle gate didn't exist yet).
                "enforcement": "enforcing",
            },
        ]))
        ks = KeyStore(dir=str(tmp_path / "keys"))
        reg = VerifierRegistry()
        register_builtins(reg)
        app = create_app(
            keystore=ks,
            dsn="sqlite:///:memory:",
            policy_store_path=str(store_path),
            verifier_registry=reg,
        )
        client = TestClient(app)
        r = client.patch(
            "/policies/drifted/v1/enabled",
            json={"enabled": True},
            headers=HDR_ADMIN,
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert "citation_verify" in detail
        assert "PostToolUse" in detail
        assert "Stop" in detail


# ── boot-time drift sweep emits structured warnings ────────────────
class TestBootSweep:
    def test_warn_on_saved_policy_lifecycle_drift_logs_per_row(
        self, tmp_path, monkeypatch, caplog,
    ):
        """The boot sweep walks PolicyStore.load() and emits a
        structured warning per drifted (event, step). Each warning
        names the policy id, the step, the trigger event, and the
        descriptor's currently-allowed lifecycles."""
        import logging
        from magi_cp.cloud.app import (
            _warn_on_saved_policy_lifecycle_drift,
        )

        store_path = tmp_path / "policies.json"
        store_path.write_text(json.dumps([
            {
                "policy": {
                    "type": "evidence",
                    "id": "drifted/v1",
                    "description": "pre-D57e drift",
                    "version": "0.1",
                    "trigger": {
                        "host": "claude-code",
                        "event": "PostToolUse",
                        "matcher": "Bash",
                    },
                    "requires": [
                        {"kind": "step", "step": "citation_verify",
                         "verdict": "pass"},
                    ],
                    "action": "audit",
                    "on_signature_invalid": "deny",
                    "gate_binary": "/usr/local/bin/magi-gate.sh",
                },
                "source": "org",
                "enabled": True,
                "enforcement": "enforcing",
            },
        ]))
        monkeypatch.setenv("MAGI_CP_POLICY_STORE_PATH", str(store_path))
        # Capture warnings emitted by the dedicated logger.
        caplog.set_level(
            logging.WARNING,
            logger="magi_cp.policy.lifecycle_drift",
        )
        # Pass a stub `app` — the helper does not use it (we keep the
        # parameter for future surface attachment).
        _warn_on_saved_policy_lifecycle_drift(app=None)  # type: ignore[arg-type]
        # At least one structured warning carrying the drift fields
        # was emitted.
        text = caplog.text
        assert "policy_lifecycle_drift" in text
        assert "'drifted/v1'" in text
        assert "'citation_verify'" in text
        assert "'PostToolUse'" in text
