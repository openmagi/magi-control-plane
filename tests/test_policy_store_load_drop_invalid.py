"""D59 follow-up (#1 P1). PolicyStore.load drops D58 to D59 narrowed rows.

D58 widened ContextInjectionPolicy.event to all 30 CC hook events. D59
narrowed it back to 26 (Elicitation / ElicitationResult / WorktreeCreate
/ MessageDisplay are excluded because their hookSpecificOutput shape is
specialized and CC silently ignores additionalContext at runtime).

A ContextInjectionPolicy persisted between D58 and D59 on any of those
four events now refuses to construct in `policy_from_dict` because
`validate()` raises. Without per-item recovery in `PolicyStore.load`,
the loader would abort the whole tenant's policy file on the next cloud
reboot, dropping every OTHER policy in the file too (cascading
fail-closed).

These tests pin the recovery semantics:

  - The offending row is dropped with a structured log warning.
  - Every other row in the file keeps loading.
  - The recovery is narrow: only the D59-specific ValueError shape
    (ContextInjectionPolicy on one of the four excluded events)
    triggers a drop. Other policy_from_dict failures (malformed
    JSON, illegal matrix triples, unknown policy types) still fail
    the loader so author-time bugs stay loud.
"""
from __future__ import annotations

import json
import logging

import pytest

from magi_cp.cloud.policy_store import PolicyStore


def _good_evidence_row(policy_id: str = "ok-row") -> dict:
    """A pre-D58 EvidencePolicy that survives the D59 narrowing."""
    return {
        "source": "user", "enabled": True,
        "policy": {
            "id": policy_id, "description": "good", "version": "0.1",
            "trigger": {
                "host": "claude-code",
                "event": "PreToolUse",
                "matcher": "Bash",
            },
            "sentinel_re": (
                r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_"
                r"(?P<doc_id>[A-Za-z0-9]+)"
            ),
            "requires": [{"step": "citation_verify", "verdict": "pass"}],
            "action": "block",
            "on_signature_invalid": "deny",
            "gate_binary": "/usr/local/bin/magi-gate.sh",
        },
    }


def _d58_context_injection_row(
    event: str, policy_id: str = "ctx-stale/v1",
) -> dict:
    """A ContextInjectionPolicy persisted under D58 on a D59-excluded event."""
    return {
        "source": "user", "enabled": True,
        "policy": {
            "type": "context_injection",
            "id": policy_id, "description": "persisted under D58", "version": "0.1",
            "event": event,
            "template": "stale template body",
            "matcher": "*",
        },
    }


@pytest.mark.parametrize("event", [
    "Elicitation", "ElicitationResult",
    "WorktreeCreate", "MessageDisplay",
])
def test_load_drops_d59_narrowed_row_and_keeps_the_rest(
    tmp_path, caplog, event,
):
    """The offending row is dropped; every other row keeps loading."""
    rows = [
        _good_evidence_row("good-a"),
        _d58_context_injection_row(event=event, policy_id=f"ctx-{event}/v1"),
        _good_evidence_row("good-b"),
    ]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(rows))
    store = PolicyStore(path=str(p))

    with caplog.at_level(logging.WARNING, logger="magi_cp.cloud.policy_store"):
        loaded = store.load()

    # Both good rows survive; the stale ContextInjectionPolicy is gone.
    ids = sorted(o.policy.id for o in loaded)
    assert ids == ["good-a", "good-b"]

    # The structured warning names the offending event and policy id so
    # the operator can re-author from the log.
    msgs = "\n".join(r.message for r in caplog.records)
    assert event in msgs
    assert f"ctx-{event}/v1" in msgs
    # The warning points at the two recovery paths the operator has.
    assert "EvidencePolicy audit" in msgs
    assert "PreToolUse" in msgs or "SessionStart" in msgs


def test_load_drop_warning_explicitly_mentions_alternate_hook_options(
    tmp_path, caplog,
):
    """The drop-warning log enumerates the alternate-hook recovery so an
    operator reading the cloud log (not the dashboard) knows their
    options."""
    rows = [_d58_context_injection_row("Elicitation")]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(rows))
    store = PolicyStore(path=str(p))

    with caplog.at_level(logging.WARNING, logger="magi_cp.cloud.policy_store"):
        store.load()

    msgs = "\n".join(r.message for r in caplog.records)
    # The three canonical additionalContext-bearing hooks must appear
    # so the operator can pivot without round-tripping through the docs.
    for token in ("PreToolUse", "SessionStart", "UserPromptSubmit"):
        assert token in msgs


def test_load_does_not_drop_unrelated_validation_errors(tmp_path):
    """Recovery is NARROW. An EvidencePolicy with an illegal
    (event, matcher, action) triple must still fail the loader so
    author-time bugs stay loud."""
    bad = [{
        "source": "user", "enabled": True,
        "policy": {
            "id": "bad", "description": "", "version": "0.1",
            "trigger": {
                "host": "claude-code",
                "event": "PostToolUse",
                "matcher": "Bash",
            },
            "sentinel_re": (
                r"FILE_COURT_(?P<matter>[A-Za-z0-9]+)_"
                r"(?P<doc_id>[A-Za-z0-9]+)"
            ),
            "requires": [{"step": "citation_verify", "verdict": "pass"}],
            # D82d — PostToolUse + Bash + block is now LEGAL as the
            # retry-feedback channel; ask stays illegal on post-tool
            # events (no interactive surface after the tool ran).
            "action": "ask",
            "on_signature_invalid": "deny",
            "gate_binary": "/usr/local/bin/magi-gate.sh",
        },
    }]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match=r"item 0.*illegal combination"):
        PolicyStore(path=str(p)).load()


def test_load_does_not_drop_unknown_policy_type(tmp_path):
    """An unknown `type` discriminator must still fail the loader."""
    bad = [{
        "source": "user", "enabled": True,
        "policy": {
            "type": "this_type_does_not_exist",
            "id": "wat", "description": "", "version": "0.1",
        },
    }]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="unknown policy type"):
        PolicyStore(path=str(p)).load()


def test_load_does_not_drop_legal_context_injection_row(tmp_path):
    """A ContextInjectionPolicy on a still-legal event (e.g. PreToolUse)
    must round-trip cleanly, no drop, no warning."""
    rows = [{
        "source": "user", "enabled": True,
        "policy": {
            "type": "context_injection",
            "id": "ctx-pretool/v1", "description": "fine",
            "version": "0.1",
            "event": "PreToolUse",
            "template": "hello",
            "matcher": "Bash",
        },
    }]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(rows))
    loaded = PolicyStore(path=str(p)).load()
    assert len(loaded) == 1
    assert loaded[0].policy.id == "ctx-pretool/v1"


def test_load_drops_only_offending_row_when_two_d58_stale_rows_present(
    tmp_path, caplog,
):
    """Multiple D58-era ContextInjectionPolicy rows on different
    excluded events all get dropped individually; the rest of the store
    keeps loading."""
    rows = [
        _good_evidence_row("good-a"),
        _d58_context_injection_row("Elicitation", policy_id="ctx-e/v1"),
        _good_evidence_row("good-b"),
        _d58_context_injection_row("MessageDisplay", policy_id="ctx-m/v1"),
        _good_evidence_row("good-c"),
    ]
    p = tmp_path / "policies.json"
    p.write_text(json.dumps(rows))
    store = PolicyStore(path=str(p))

    with caplog.at_level(logging.WARNING, logger="magi_cp.cloud.policy_store"):
        loaded = store.load()

    ids = sorted(o.policy.id for o in loaded)
    assert ids == ["good-a", "good-b", "good-c"]
    msgs = "\n".join(r.message for r in caplog.records)
    assert "ctx-e/v1" in msgs
    assert "ctx-m/v1" in msgs
