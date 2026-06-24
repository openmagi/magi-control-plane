"""D50 / D53a - unit tests for the payload redactor.

The sample-preview endpoint depends on this module to keep raw payloads
out of the dashboard. The tests assert the redactor:
  - masks the secret shapes the brief enumerates (JWT, API keys, hex,
    email),
  - is idempotent (re-running on a redacted string is a no-op),
  - fails closed on unknown / nested fields (drop, not mask),
  - truncates with the documented `...` marker.

These are pure-function tests; no FastAPI, no DB.
"""
from __future__ import annotations

from magi_cp.policy.run_redaction import (
    DEFAULT_PREVIEW_MAX_CHARS, redact_payload_preview, redact_text,
)


class TestRedactText:
    def test_redact_jwt_shape(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxIiwibmFtZSI6IkphbmUifQ."
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        )
        out = redact_text(f"prefix {jwt} suffix")
        assert jwt not in out
        assert "[REDACTED:jwt]" in out

    def test_redact_api_key_prefixes(self):
        for prefix in ("sk-", "pk-", "api_", "key-"):
            secret = prefix + "abcdef0123456789abcdef0123"
            out = redact_text(f"got {secret} done")
            assert secret not in out
            assert "[REDACTED:api_key]" in out

    def test_redact_github_pat(self):
        secret = "ghp_abcdef0123456789ABCDEF0123456789"
        out = redact_text(secret)
        assert secret not in out
        assert "[REDACTED:github_token]" in out

    def test_redact_aws_access_key(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        out = redact_text(f"export AWS_ACCESS_KEY_ID={secret}")
        assert secret not in out
        assert "[REDACTED:aws_key]" in out

    def test_redact_long_hex_digest(self):
        # 64-char hex (sha256-shaped) gets masked. Short hex strings
        # (commit short shas, 7-12 chars) DO NOT (they're rarely
        # secret-shaped and masking them would over-redact).
        sha = "deadbeefcafebabe0123456789abcdef0123456789abcdef0123456789abcdef"
        out = redact_text(f"sha={sha} done")
        assert sha not in out
        assert "[REDACTED:hex]" in out
        # 8-char hex is left alone (typical short commit id).
        short = "abc12345"
        out2 = redact_text(short)
        assert short in out2

    def test_redact_email(self):
        out = redact_text("notify alice@example.com when done")
        assert "alice@example.com" not in out
        assert "[REDACTED:email]" in out

    def test_idempotent(self):
        s = "alice@example.com is the user"
        once = redact_text(s)
        twice = redact_text(once)
        assert once == twice

    def test_empty_input_returns_empty(self):
        assert redact_text("") == ""

    def test_clean_input_unchanged(self):
        # No secret-shaped substring -> verbatim return.
        s = "pattern did not match: foo"
        assert redact_text(s) == s


class TestRedactPayloadPreview:
    def test_empty_body_returns_empty(self):
        assert redact_payload_preview(None) == ""
        assert redact_payload_preview({}) == ""

    def test_picks_first_allowlist_field(self):
        # `text` is first in the allowlist, so a body that contains
        # both `text` and `command` renders the `text` first.
        out = redact_payload_preview({"text": "hello", "command": "rm -rf"})
        assert "hello" in out
        # `command` is NOT picked once `text` resolved.
        assert "rm -rf" not in out

    def test_drops_unknown_fields_failclosed(self):
        # A novel field (not on the allowlist) is dropped, not echoed.
        # The brief calls for fail-closed projection so a future field
        # with a secret never leaks.
        out = redact_payload_preview({"secret_blob": "totally_clear_text"})
        # The unknown field is dropped entirely; nothing in the
        # allowlist matched -> empty preview.
        assert "totally_clear_text" not in out
        assert out == ""

    def test_redacts_secret_in_text_field(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxIiwibmFtZSI6IkphbmUifQ."
            "abcdefghijklmnopqrstuvwxyz0123456789"
        )
        out = redact_payload_preview({"text": f"hi {jwt} bye"})
        assert jwt not in out
        assert "[REDACTED:jwt]" in out

    def test_reasons_appended(self):
        out = redact_payload_preview({
            "step": "inline_regex",
            "reasons": ["pattern did not match: foo"],
        })
        # The step + the reason both show up.
        assert "inline_regex" in out
        assert "pattern did not match: foo" in out

    def test_truncation_appends_ellipsis(self):
        body = {"text": "x" * 600}
        out = redact_payload_preview(body)
        assert len(out) <= DEFAULT_PREVIEW_MAX_CHARS
        assert out.endswith("...")

    def test_collapses_whitespace(self):
        out = redact_payload_preview({"text": "hello\n\n  world"})
        assert "hello world" in out
        assert "\n" not in out

    def test_nested_dict_in_allowlist_field_is_dropped(self):
        # `text` resolves to None (not a string scalar), so the field
        # is dropped. Fail-closed: no JSON dump of the dict.
        out = redact_payload_preview({"text": {"nested": "value"}})
        assert "nested" not in out
        assert "value" not in out
