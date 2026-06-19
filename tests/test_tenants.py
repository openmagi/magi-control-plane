"""v2.0-W6a — multi-tenant model + per-tenant API keys.

Schema:
  tenants(id PK, status, plan, created_at, expires_at, suspended_reason)
  api_keys(id PK, tenant_id FK, hashed_key UNIQUE, prefix, created_at,
           last_used_at NULL, revoked_at NULL)

Wire-key shape: `mcp_<24-char-base32>` — base32 makes prefix display
unambiguous; the 24 chars provide ~120 bits of entropy. Stored as SHA-256
hash; never write the cleartext to the DB or to logs.

Backwards compat: a request carrying the legacy MAGI_CP_API_KEY env value
maps to a synthetic "default" tenant so existing tests + single-tenant
deployments keep working.
"""
import hashlib
import os
import tempfile

import pytest


# ── basics ─────────────────────────────────────────────────────────
def test_create_tenant(tmp_path):
    from magi_cp.cloud.db import make_engine, init_schema
    from magi_cp.cloud.tenants import TenantRepo
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    repo = TenantRepo(engine)
    t = repo.create(tenant_id="user_abc", plan="pro")
    assert t.id == "user_abc"
    assert t.status == "active"
    assert t.plan == "pro"


def test_tenant_id_unique():
    from magi_cp.cloud.db import make_engine, init_schema
    from magi_cp.cloud.tenants import TenantRepo
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    repo = TenantRepo(engine)
    repo.create(tenant_id="user_abc", plan="pro")
    with pytest.raises(Exception):   # IntegrityError
        repo.create(tenant_id="user_abc", plan="starter")


def test_tenant_status_lifecycle():
    from magi_cp.cloud.db import make_engine, init_schema
    from magi_cp.cloud.tenants import TenantRepo
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    repo = TenantRepo(engine)
    repo.create(tenant_id="user_abc", plan="pro")
    repo.suspend("user_abc", reason="payment_failed")
    assert repo.get("user_abc").status == "suspended"
    repo.reactivate("user_abc")
    assert repo.get("user_abc").status == "active"


def test_get_returns_none_for_missing():
    from magi_cp.cloud.db import make_engine, init_schema
    from magi_cp.cloud.tenants import TenantRepo
    engine = make_engine("sqlite:///:memory:")
    init_schema(engine)
    repo = TenantRepo(engine)
    assert repo.get("ghost") is None


# ── API keys ────────────────────────────────────────────────────────
class TestApiKeys:
    def _setup(self):
        from magi_cp.cloud.db import make_engine, init_schema
        from magi_cp.cloud.tenants import ApiKeyRepo, TenantRepo
        engine = make_engine("sqlite:///:memory:")
        init_schema(engine)
        TenantRepo(engine).create(tenant_id="user_abc", plan="pro")
        return engine, ApiKeyRepo(engine)

    def test_issue_returns_cleartext_once(self):
        _, keys = self._setup()
        issued = keys.issue(tenant_id="user_abc")
        # cleartext + prefix
        assert issued.cleartext.startswith("mcp_")
        assert len(issued.cleartext) > 20
        assert issued.prefix == issued.cleartext[:8]
        assert issued.tenant_id == "user_abc"

    def test_issued_key_is_hashed_in_storage(self):
        engine, keys = self._setup()
        issued = keys.issue(tenant_id="user_abc")
        # The cleartext is NOT stored — only the SHA-256 hash.
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT hashed_key FROM api_keys WHERE tenant_id = :tid"
            ), {"tid": "user_abc"}).first()
        assert row[0] != issued.cleartext
        assert row[0] == hashlib.sha256(issued.cleartext.encode("utf-8")).hexdigest()

    def test_authenticate_returns_tenant_id_on_valid_key(self):
        _, keys = self._setup()
        issued = keys.issue(tenant_id="user_abc")
        auth = keys.authenticate(issued.cleartext)
        assert auth is not None
        assert auth.tenant_id == "user_abc"
        assert auth.status == "active"

    def test_authenticate_returns_none_on_unknown_key(self):
        _, keys = self._setup()
        assert keys.authenticate("mcp_unknown_key_123") is None

    def test_authenticate_returns_none_on_revoked_key(self):
        _, keys = self._setup()
        issued = keys.issue(tenant_id="user_abc")
        keys.revoke(issued.id)
        assert keys.authenticate(issued.cleartext) is None

    def test_authenticate_returns_none_on_suspended_tenant(self):
        engine, keys = self._setup()
        issued = keys.issue(tenant_id="user_abc")
        from magi_cp.cloud.tenants import TenantRepo
        TenantRepo(engine).suspend("user_abc", reason="payment_failed")
        assert keys.authenticate(issued.cleartext) is None

    def test_multiple_keys_per_tenant(self):
        _, keys = self._setup()
        k1 = keys.issue(tenant_id="user_abc")
        k2 = keys.issue(tenant_id="user_abc")
        assert k1.cleartext != k2.cleartext
        assert keys.authenticate(k1.cleartext).tenant_id == "user_abc"
        assert keys.authenticate(k2.cleartext).tenant_id == "user_abc"

    def test_list_keys_for_tenant_returns_prefix_not_cleartext(self):
        _, keys = self._setup()
        keys.issue(tenant_id="user_abc")
        keys.issue(tenant_id="user_abc")
        listed = keys.list_for_tenant("user_abc")
        assert len(listed) == 2
        for k in listed:
            # Display shows prefix only; no cleartext or full hash in the
            # ListedKey dataclass (it has no `hashed_key` field at all).
            assert k.prefix.startswith("mcp_")
            assert not hasattr(k, "hashed_key")


# ── env-key backward compat: legacy MAGI_CP_API_KEY = "default" tenant ──
class TestLegacyEnvKey:
    """Single-tenant deployments + existing tests keep working."""

    def test_env_key_authenticates_as_default_tenant(self, monkeypatch):
        monkeypatch.setenv("MAGI_CP_API_KEY", "single-tenant-key")
        from magi_cp.cloud.db import make_engine, init_schema
        from magi_cp.cloud.tenants import authenticate_request
        engine = make_engine("sqlite:///:memory:")
        init_schema(engine)
        auth = authenticate_request(engine, "single-tenant-key")
        assert auth is not None
        assert auth.tenant_id == "default"
        assert auth.status == "active"

    def test_wrong_env_key_does_not_authenticate(self, monkeypatch):
        monkeypatch.setenv("MAGI_CP_API_KEY", "single-tenant-key")
        from magi_cp.cloud.db import make_engine, init_schema
        from magi_cp.cloud.tenants import authenticate_request
        engine = make_engine("sqlite:///:memory:")
        init_schema(engine)
        assert authenticate_request(engine, "different") is None

    def test_multi_tenant_db_key_also_works_alongside_env(self, monkeypatch):
        """Both auth paths coexist — env key + DB-issued key both authenticate."""
        monkeypatch.setenv("MAGI_CP_API_KEY", "env-key")
        from magi_cp.cloud.db import make_engine, init_schema
        from magi_cp.cloud.tenants import ApiKeyRepo, TenantRepo, authenticate_request
        engine = make_engine("sqlite:///:memory:")
        init_schema(engine)
        TenantRepo(engine).create(tenant_id="user_abc", plan="pro")
        issued = ApiKeyRepo(engine).issue(tenant_id="user_abc")
        assert authenticate_request(engine, "env-key").tenant_id == "default"
        assert authenticate_request(engine, issued.cleartext).tenant_id == "user_abc"
