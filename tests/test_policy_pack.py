"""D75: policy pack tests.

A pack is a named group of policy ids with a single toggle that cascades
to every member. Built-in packs ship 5 entries; user packs persist as a
JSON file under `policy_store_dir/packs.json`. Pack status (all /
partial / none) is computed against the live policy store.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_cp.cloud.app import create_app
from magi_cp.cloud.keys import KeyStore
from magi_cp.cloud.pack_store import (
    PackStore, UserPackRow, slugify_name, validate_user_slug,
)
from magi_cp.policy.pack import (
    all_builtin_packs, builtin_pack_spec_by_id, compute_status,
    inline_policy_for, user_pack_to_dict,
)


ADMIN_KEY = "p-admin-key"
ADMIN_HEADERS = {"X-Admin-Api-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAGI_CP_API_KEY", "p-api-key")
    monkeypatch.setenv("MAGI_CP_HITL_API_KEY", "p-hitl-key")
    monkeypatch.setenv("MAGI_CP_ADMIN_API_KEY", ADMIN_KEY)


@pytest.fixture
def client(tmp_path):
    ks = KeyStore(dir=str(tmp_path / "keys"))
    app = create_app(
        keystore=ks, dsn="sqlite:///:memory:",
        policy_store_path=str(tmp_path / "policies.json"),
        pack_store_path=str(tmp_path / "packs.json"),
    )
    return TestClient(app)


# ── unit: catalog shape ───────────────────────────────────────────


def test_builtin_packs_returns_five() -> None:
    packs = all_builtin_packs(locale="en")
    assert len(packs) == 5
    ids = [p["id"] for p in packs]
    assert ids == [
        "pack/research-mode",
        "pack/coding-safety",
        "pack/compliance-audit",
        "pack/permissive-observe",
        "pack/strict-block",
    ]


def test_builtin_pack_locales() -> None:
    en = {p["id"]: p for p in all_builtin_packs(locale="en")}
    ko = {p["id"]: p for p in all_builtin_packs(locale="ko")}
    # English name carries an ASCII word, Korean name carries Hangul.
    assert "Research" in en["pack/research-mode"]["name"]
    assert any("가" <= ch <= "힣"
               for ch in ko["pack/research-mode"]["name"])
    # Descriptions diverge across locales (both non-empty).
    assert en["pack/strict-block"]["description"] \
        != ko["pack/strict-block"]["description"]


def test_builtin_pack_member_ids_are_prebuilts_or_inline() -> None:
    for pack in all_builtin_packs(locale="en"):
        for mid in pack["policy_ids"]:
            assert (
                mid.startswith("prebuilt/")
                or mid.startswith("pack/strict-block/")
            ), f"unexpected member id {mid!r} in {pack['id']!r}"


def test_strict_block_has_inline_policies() -> None:
    spec = builtin_pack_spec_by_id("pack/strict-block")
    assert spec is not None
    assert len(spec.inline_policies) == 3
    for mid, policy in spec.inline_policies:
        assert mid == policy.id
        assert policy.action == "block"
        assert inline_policy_for("pack/strict-block", mid) is not None
    assert inline_policy_for("pack/strict-block", "not-a-real-id") is None


# ── unit: status compute ──────────────────────────────────────────


def test_compute_status_all_partial_none() -> None:
    members = ["a", "b", "c"]
    assert compute_status(members, {"a", "b", "c"}) == ("all", 3)
    assert compute_status(members, {"a"}) == ("partial", 1)
    assert compute_status(members, set()) == ("none", 0)
    assert compute_status([], set()) == ("none", 0)


def test_user_pack_to_dict_status_derived() -> None:
    p = user_pack_to_dict(
        "user-pack/test", "Test", "desc", ["a", "b"], {"a"},
    )
    assert p["status"] == "partial"
    assert p["enabled_count"] == 1
    assert p["member_count"] == 2
    assert p["source"] == "user"


# ── unit: store round-trip ────────────────────────────────────────


def test_pack_store_roundtrip(tmp_path) -> None:
    path = str(tmp_path / "packs.json")
    store = PackStore(path=path)
    assert store.load() == []
    store.save([
        UserPackRow(
            id="user-pack/a", name="A", description="da",
            policy_ids=["p1", "p2"],
        ),
        UserPackRow(
            id="user-pack/b", name="B", description="db",
            policy_ids=[],
        ),
    ])
    rows = store.load()
    assert [r.id for r in rows] == ["user-pack/a", "user-pack/b"]
    assert rows[0].policy_ids == ["p1", "p2"]


def test_pack_store_save_is_byte_stable(tmp_path) -> None:
    path = str(tmp_path / "packs.json")
    store = PackStore(path=path)
    # Save in non-id order; on-disk shape must be id-sorted, so a
    # repeated save with the same content is byte-stable.
    store.save([
        UserPackRow(id="user-pack/z", name="Z", description="",
                    policy_ids=["m1"]),
        UserPackRow(id="user-pack/a", name="A", description="",
                    policy_ids=["m2"]),
    ])
    body1 = open(path, encoding="utf-8").read()
    # Re-load + re-save with the same payload (different in-mem order)
    # must yield the same bytes.
    rows = store.load()
    store.save(list(reversed(rows)))
    body2 = open(path, encoding="utf-8").read()
    assert body1 == body2


def test_pack_store_rejects_non_user_prefix(tmp_path) -> None:
    """A pack-store row whose id does not start with `user-pack/` is a
    bug — built-in pack metadata lives in the catalog, not the store.
    The loader 422s on this so a corrupt file fails loudly rather than
    silently ranking a built-in id under the user namespace.
    """
    path = str(tmp_path / "packs.json")
    open(path, "w", encoding="utf-8").write(
        '[{"id": "pack/research-mode", "name": "x", "description": "", '
        '"policy_ids": []}]\n'
    )
    store = PackStore(path=path)
    with pytest.raises(ValueError):
        store.load()


# ── unit: slug helpers ────────────────────────────────────────────


def test_validate_user_slug_round_trips() -> None:
    assert validate_user_slug("research") == "research"
    assert validate_user_slug("my-pack_1") == "my-pack_1"
    with pytest.raises(ValueError):
        validate_user_slug("")
    with pytest.raises(ValueError):
        validate_user_slug("HasUpper")
    with pytest.raises(ValueError):
        validate_user_slug("-leading")
    with pytest.raises(ValueError):
        validate_user_slug("trailing-")
    with pytest.raises(ValueError):
        validate_user_slug("white space")
    with pytest.raises(ValueError):
        validate_user_slug("a" * 81)


def test_slugify_name() -> None:
    assert slugify_name("Research Mode") == "research-mode"
    assert slugify_name("  Coding Safety  ") == "coding-safety"
    assert slugify_name("리서치") == "pack"  # non-ascii → fallback
    assert slugify_name("") == "pack"


# ── endpoint: list ────────────────────────────────────────────────


def test_list_packs_returns_builtins(client) -> None:
    r = client.get("/policy-packs", headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    builtin_ids = [
        p["id"] for p in body["items"] if p["source"] == "builtin"
    ]
    assert builtin_ids == [
        "pack/research-mode",
        "pack/coding-safety",
        "pack/compliance-audit",
        "pack/permissive-observe",
        "pack/strict-block",
    ]


def test_list_packs_requires_admin_key(client) -> None:
    r = client.get("/policy-packs")
    assert r.status_code == 401


def test_list_packs_status_all_after_full_enable(client) -> None:
    r = client.post(
        "/policy-packs/pack/research-mode/enable",
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "all"
    assert body["enabled_count"] == body["member_count"] == 3


def test_get_single_pack_resolves_members(client) -> None:
    r = client.get(
        "/policy-packs/pack/coding-safety", headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "pack/coding-safety"
    assert {m["id"] for m in body["members"]} == {
        "prebuilt/privilege-scan-bash",
        "prebuilt/structured-output-at-final",
    }
    # All members start disabled.
    assert all(not m["enabled"] for m in body["members"])


# ── endpoint: cascade enable / disable ───────────────────────────


def test_enable_pack_cascades_prebuilt_members(client) -> None:
    r = client.post(
        "/policy-packs/pack/research-mode/enable",
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "all"
    assert {res["id"] for res in body["results"]} == {
        "prebuilt/citation-verify-at-final",
        "prebuilt/source-allowlist-webfetch",
        "prebuilt/prompt-injection-webfetch",
    }
    assert all(res["ok"] for res in body["results"])
    # GET /policies includes the materialized prebuilt rows.
    listed = client.get("/policies", headers=ADMIN_HEADERS).json()["items"]
    ids = {x["id"] for x in listed}
    assert "prebuilt/citation-verify-at-final" in ids


def test_disable_pack_cascades(client) -> None:
    client.post(
        "/policy-packs/pack/research-mode/enable", headers=ADMIN_HEADERS,
    )
    r = client.post(
        "/policy-packs/pack/research-mode/disable", headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "none"
    # Members rows still exist (disable is metadata-only), just
    # disabled.
    listed = client.get("/policies", headers=ADMIN_HEADERS).json()["items"]
    rows = {x["id"]: x for x in listed}
    assert rows["prebuilt/citation-verify-at-final"]["enabled"] is False


def test_enable_pack_is_idempotent(client) -> None:
    r1 = client.post(
        "/policy-packs/pack/coding-safety/enable", headers=ADMIN_HEADERS,
    )
    r2 = client.post(
        "/policy-packs/pack/coding-safety/enable", headers=ADMIN_HEADERS,
    )
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["status"] == "all"


def test_strict_block_pack_enables_inline_policies(client) -> None:
    r = client.post(
        "/policy-packs/pack/strict-block/enable", headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "all"
    # The 3 inline IRs land in the policy store with their pack-scoped
    # ids, action=block.
    listed = client.get("/policies", headers=ADMIN_HEADERS).json()["items"]
    rows = {x["id"]: x for x in listed}
    for mid in (
        "pack/strict-block/privilege-bash",
        "pack/strict-block/source-allowlist-webfetch",
        "pack/strict-block/prompt-injection-userprompt",
    ):
        assert mid in rows, f"strict-block member {mid} not materialized"
        assert rows[mid]["enabled"] is True


def test_enable_missing_skips_already_enabled(client) -> None:
    # Enable one member directly.
    client.post(
        "/policies/prebuilt/privilege-scan-bash/enable",
        headers=ADMIN_HEADERS,
    )
    # enable-missing on coding-safety should skip the already-enabled
    # one + enable the other.
    r = client.post(
        "/policy-packs/pack/coding-safety/enable-missing",
        headers=ADMIN_HEADERS,
    )
    body = r.json()
    skipped = [
        res for res in body["results"] if res.get("skipped") is True
    ]
    assert len(skipped) == 1
    assert skipped[0]["id"] == "prebuilt/privilege-scan-bash"


def test_unknown_pack_404s(client) -> None:
    r = client.post(
        "/policy-packs/pack/does-not-exist/enable",
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 404
    r2 = client.get(
        "/policy-packs/pack/does-not-exist", headers=ADMIN_HEADERS,
    )
    assert r2.status_code == 404


def test_pack_enable_requires_admin(client) -> None:
    r = client.post("/policy-packs/pack/research-mode/enable")
    assert r.status_code == 401


# ── endpoint: user-pack CRUD ─────────────────────────────────────


def test_create_user_pack(client) -> None:
    r = client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={
            "name": "My Research",
            "description": "custom bundle",
            "policy_ids": [
                "prebuilt/citation-verify-at-final",
                "prebuilt/source-allowlist-webfetch",
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "user-pack/my-research"
    assert body["source"] == "user"
    # GET /policy-packs surfaces it under the user section.
    listed = client.get(
        "/policy-packs", headers=ADMIN_HEADERS,
    ).json()["items"]
    by_id = {p["id"]: p for p in listed}
    assert "user-pack/my-research" in by_id
    assert by_id["user-pack/my-research"]["source"] == "user"


def test_create_user_pack_explicit_slug(client) -> None:
    r = client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={
            "name": "Anything",
            "policy_ids": [],
            "slug": "custom-slug-1",
        },
    )
    assert r.status_code == 200
    assert r.json()["id"] == "user-pack/custom-slug-1"


def test_create_user_pack_duplicate_slug_conflicts(client) -> None:
    payload = {"name": "X", "policy_ids": [], "slug": "dupe"}
    r1 = client.post("/policy-packs", headers=ADMIN_HEADERS, json=payload)
    r2 = client.post("/policy-packs", headers=ADMIN_HEADERS, json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 409


def test_create_user_pack_invalid_slug_422(client) -> None:
    r = client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={"name": "X", "policy_ids": [], "slug": "Bad Slug"},
    )
    assert r.status_code == 422


def test_create_user_pack_dedupes_policy_ids(client) -> None:
    r = client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={
            "name": "Dedupe", "policy_ids": ["a", "b", "a", "c"],
            "slug": "dedupe-pack",
        },
    )
    assert r.status_code == 200
    assert r.json()["policy_ids"] == ["a", "b", "c"]


def test_update_user_pack(client) -> None:
    client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={"name": "Old", "policy_ids": ["a"], "slug": "upd"},
    )
    r = client.put(
        "/policy-packs/user-pack/upd", headers=ADMIN_HEADERS,
        json={"name": "New", "policy_ids": ["b", "c"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "New"
    assert body["policy_ids"] == ["b", "c"]


def test_update_builtin_pack_405(client) -> None:
    r = client.put(
        "/policy-packs/pack/research-mode", headers=ADMIN_HEADERS,
        json={"name": "New"},
    )
    assert r.status_code == 405


def test_delete_user_pack(client) -> None:
    client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={"name": "Del", "policy_ids": [], "slug": "del"},
    )
    r = client.delete(
        "/policy-packs/user-pack/del", headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    # Second delete 404s (no longer present).
    r2 = client.delete(
        "/policy-packs/user-pack/del", headers=ADMIN_HEADERS,
    )
    assert r2.status_code == 404


def test_delete_builtin_pack_405(client) -> None:
    r = client.delete(
        "/policy-packs/pack/research-mode", headers=ADMIN_HEADERS,
    )
    assert r.status_code == 405


# ── per-member error reporting in cascade ────────────────────────


def test_enable_pack_reports_per_member_failure(client) -> None:
    """A user pack whose member id is not a known prebuilt + has no
    matching policy in the store reports an `ok: False` result for
    that member while still committing the successful ones. The brief
    asks for partial-success commit + per-member error.
    """
    client.post(
        "/policy-packs", headers=ADMIN_HEADERS,
        json={
            "name": "Mixed", "policy_ids": [
                "prebuilt/privilege-scan-bash",
                "unknown/never-saved-policy",
            ],
            "slug": "mixed",
        },
    )
    r = client.post(
        "/policy-packs/user-pack/mixed/enable", headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    by_id = {res["id"]: res for res in body["results"]}
    assert by_id["prebuilt/privilege-scan-bash"]["ok"] is True
    assert by_id["unknown/never-saved-policy"]["ok"] is False
    assert "error" in by_id["unknown/never-saved-policy"]
    # Status reflects post-attempt reality.
    assert body["status"] == "partial"


def test_get_single_pack_enabled_count(client) -> None:
    # Enable one of 2 members in coding-safety.
    client.post(
        "/policies/prebuilt/privilege-scan-bash/enable",
        headers=ADMIN_HEADERS,
    )
    r = client.get(
        "/policy-packs/pack/coding-safety", headers=ADMIN_HEADERS,
    )
    body = r.json()
    assert body["status"] == "partial"
    assert body["enabled_count"] == 1
    assert body["member_count"] == 2


def test_list_packs_locale_header(client) -> None:
    r = client.get(
        "/policy-packs", headers={
            **ADMIN_HEADERS, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        },
    )
    assert r.status_code == 200
    items = {p["id"]: p for p in r.json()["items"]
              if p["source"] == "builtin"}
    name = items["pack/research-mode"]["name"]
    assert any("가" <= ch <= "힣" for ch in name)
