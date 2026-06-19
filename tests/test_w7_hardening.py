"""v2.0-W7 — security hardening tests.

  - Verifier payload size cap (regex DoS defense)
  - lbox URL uses HTTPS (TLS, not plain HTTP)
  - ReDoS regression: every wired verifier's regexes complete in O(n) on
    pathological input (no catastrophic backtracking)
"""
import json
import re
import time
import tempfile

import pytest
from fastapi.testclient import TestClient


def _tmp_store():
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    f.write("[]"); f.close()
    return f.name


def _client():
    from magi_cp.cloud.app import create_app
    from magi_cp.verifier.protocol import VerifierRegistry
    from magi_cp.verifier.builtins import register_builtins
    reg = VerifierRegistry()
    register_builtins(reg)
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=_tmp_store(),
        verifier_registry=reg,
    )
    return TestClient(app)


# ── payload size cap on /verify/{step} ─────────────────────────────
class TestPayloadSizeCap:
    def test_oversize_payload_text_returns_422(self, monkeypatch):
        monkeypatch.setenv("MAGI_CP_API_KEY", "test-key")
        c = _client()
        huge = "x" * 100_000   # 100KB single field — over verifier payload cap
        r = c.post("/verify/privilege_scan",
                   headers={"X-Api-Key": "test-key"},
                   json={"payload": {"text": huge}})
        assert r.status_code == 422
        assert "payload" in r.text.lower() or "too large" in r.text.lower()

    def test_payload_just_under_cap_passes(self, monkeypatch):
        monkeypatch.setenv("MAGI_CP_API_KEY", "test-key")
        c = _client()
        # 20K - 200 buffer for JSON envelope
        ok = "x" * 19_500
        r = c.post("/verify/privilege_scan",
                   headers={"X-Api-Key": "test-key"},
                   json={"payload": {"text": ok}})
        assert r.status_code == 200


# ── lbox uses HTTPS ────────────────────────────────────────────────
class TestLboxHttps:
    def test_search_url_uses_https(self):
        from magi_cp.mcp import lbox
        assert lbox.SEARCH_URL.startswith("https://"), lbox.SEARCH_URL

    def test_service_url_uses_https(self):
        from magi_cp.mcp import lbox
        assert lbox.SERVICE_URL.startswith("https://"), lbox.SERVICE_URL


# ── ReDoS regression on all wired verifier regexes ─────────────────
class TestReDoSResistance:
    """Pathological inputs against each verifier should complete in <500ms.

    Catastrophic backtracking would blow this budget by orders of magnitude.
    The verifiers ship with linear-time patterns (no nested * or alternation
    of overlapping prefixes) — this test locks that invariant."""

    BUDGET_S = 0.5
    SIZES = [10_000]

    def test_privilege_scan_regexes(self):
        from magi_cp.verifier.builtins import PrivilegeScanVerifier
        v = PrivilegeScanVerifier()
        for n in self.SIZES:
            # pathological: many digits (RRN-shaped fragments) + soft markers
            text = ("9" * 6 + "-" + "1" * 6 + " ") * (n // 14)
            start = time.perf_counter()
            v.run({"text": text})
            dur = time.perf_counter() - start
            assert dur < self.BUDGET_S, f"privilege_scan O(n^k) on len={n}: {dur:.3f}s"

    def test_prompt_injection_regexes(self):
        from magi_cp.verifier.builtins import PromptInjectionScreenVerifier
        v = PromptInjectionScreenVerifier()
        for n in self.SIZES:
            # pathological: many partial-match prefixes
            text = "ignore prev " * (n // 12)
            start = time.perf_counter()
            v.run({"text": text})
            dur = time.perf_counter() - start
            assert dur < self.BUDGET_S, f"prompt_injection O(n^k) on len={n}: {dur:.3f}s"


# ── verifier dispatch never bypasses payload size validation ───────
class TestPayloadCapForAllSteps:
    @pytest.mark.parametrize("step", [
        "privilege_scan",
        "source_allowlist",
        "structured_output",
        "prompt_injection_screen",
    ])
    def test_oversize_rejected_for_each_step(self, step, monkeypatch):
        """The cap applies regardless of which verifier we dispatch to.
        Either MaxBodyMiddleware (413) or VerifyDispatchReq validator (422)
        rejects — both are correct fail-closed outcomes."""
        monkeypatch.setenv("MAGI_CP_API_KEY", "test-key")
        c = _client()
        # Single oversize text field — large enough to hit the per-payload
        # cap but small enough to slip past the global 256KB body cap.
        huge = {"text": "x" * 50_000}
        r = c.post(f"/verify/{step}",
                   headers={"X-Api-Key": "test-key"},
                   json={"payload": huge})
        assert r.status_code in (413, 422), r.text
