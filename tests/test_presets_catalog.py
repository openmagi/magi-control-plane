"""v1.1-PC — /presets catalog backend.

The /presets endpoint merges the live VerifierRegistry (the 5 wired beachhead
verifiers from PB) with a static catalog of magi-agent preset IDs (vendored
for label parity). Wired entries get enforcement=enforcing; everything else
is honestly labeled preview.

The catalog is read-only — no auth requirement (read view is operator-facing
but doesn't expose secrets). The endpoint matches /v1/ versionless pattern.
"""
from fastapi.testclient import TestClient


def _client(*, with_registry: bool = True, tmp_path_factory=None):
    """Build an app with isolated state for each test.

    SQLite uses :memory:; policy store uses a fresh temp file so tests don't
    contend on $HOME/.magi-cp/policies.json.
    """
    import tempfile
    from magi_cp.cloud.app import create_app
    from magi_cp.verifier.protocol import VerifierRegistry
    from magi_cp.verifier.builtins import register_builtins

    reg = None
    if with_registry:
        reg = VerifierRegistry()
        register_builtins(reg)

    tmp_store = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp_store.close()
    app = create_app(
        dsn="sqlite:///:memory:",
        policy_store_path=tmp_store.name,
        verifier_registry=reg,
    )
    return TestClient(app)


# ── shape ─────────────────────────────────────────────────────────
class TestShape:
    def test_returns_200(self):
        c = _client()
        r = c.get("/presets")
        assert r.status_code == 200

    def test_has_presets_key(self):
        c = _client()
        body = c.get("/presets").json()
        assert "presets" in body
        assert isinstance(body["presets"], list)

    def test_presets_include_wired_and_vendor(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        ids = {p["id"] for p in items}
        # 5 wired
        assert "citation-verify" in ids
        assert "privilege-scan" in ids
        assert "source-allowlist" in ids
        assert "structured-output" in ids
        assert "prompt-injection-screen" in ids
        # vendor sampling — known magi-agent IDs
        assert "answer-quality" in ids
        assert "memory-continuity" in ids
        assert "dangerous-patterns" in ids


# ── enforcement labels ───────────────────────────────────────────
class TestEnforcement:
    def _by_id(self, items, pid):
        return next((p for p in items if p["id"] == pid), None)

    def test_wired_entries_are_enforcing(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        for pid in (
            "citation-verify", "privilege-scan",
            "source-allowlist", "structured-output", "prompt-injection-screen",
        ):
            entry = self._by_id(items, pid)
            assert entry is not None, f"missing wired: {pid}"
            assert entry["enforcement"] == "enforcing", f"{pid}: {entry['enforcement']}"

    def test_vendor_entries_are_preview(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        # answer-quality is magi-agent vendor, not in our registry — preview
        entry = self._by_id(items, "answer-quality")
        assert entry["enforcement"] == "preview"

    def test_no_capability_label_in_v1_1(self):
        """We don't have env-flag-gated capability presets — the 4-tier label
        exists for future expansion but no entry should claim it now."""
        c = _client()
        items = c.get("/presets").json()["presets"]
        for p in items:
            assert p["enforcement"] != "capability"


# ── category + 8-tier coverage ───────────────────────────────────
class TestCategories:
    def test_all_eight_categories_present(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        cats = {p["category"] for p in items}
        for expected in ("ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
                         "RESEARCH", "MEMORY", "SECURITY"):
            assert expected in cats, f"missing category: {expected}"

    def test_each_entry_has_category(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        for p in items:
            assert "category" in p
            assert p["category"] in ("ANSWER", "FACT", "CODING", "TASK",
                                     "OUTPUT", "RESEARCH", "MEMORY", "SECURITY")


# ── wired carry step name for policy IR binding ──────────────────
class TestWiredMetadata:
    def test_wired_carry_step(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        entry = next(p for p in items if p["id"] == "citation-verify")
        assert entry["step"] == "citation_verify"

    def test_preview_step_is_null(self):
        c = _client()
        items = c.get("/presets").json()["presets"]
        entry = next(p for p in items if p["id"] == "answer-quality")
        # honest: no step binding because no verifier exists
        assert entry["step"] is None


# ── ID uniqueness (wired must not duplicate any vendor ID) ────────
def test_preset_ids_unique():
    c = _client()
    items = c.get("/presets").json()["presets"]
    ids = [p["id"] for p in items]
    assert len(ids) == len(set(ids)), f"duplicates: {[x for x in ids if ids.count(x) > 1]}"


# ── catalog-shape: no future vendor entry may collide with a wired ID ─
def test_vendor_catalog_does_not_collide_with_wired_ids():
    """Build-time guard: if someone adds a vendor entry whose ID matches a
    wired verifier's step-derived ID, the wired entry would silently shadow
    the vendor entry (or vice versa, depending on merge order). Catch that
    at unit-test time rather than at request time."""
    from magi_cp.cloud.presets_catalog import vendor_catalog
    from magi_cp.verifier.protocol import VerifierRegistry
    from magi_cp.verifier.builtins import register_builtins

    reg = VerifierRegistry()
    register_builtins(reg)
    wired_ids = {v.step.replace("_", "-") for v in reg.all()}
    vendor_ids = {vp.id for vp in vendor_catalog()}
    collisions = wired_ids & vendor_ids
    assert not collisions, (
        f"vendor catalog must not declare IDs that collide with wired "
        f"verifier steps; collisions={collisions}"
    )


# ── catalog is stable when registry is empty (no 5 wired) ─────────
def test_endpoint_works_without_verifier_registry():
    """A magi-control-plane deployment without verifier wiring should still
    return the vendor catalog — labels all preview, none enforcing."""
    c = _client(with_registry=False)
    items = c.get("/presets").json()["presets"]
    # all entries are preview; no entry can be enforcing
    enforcings = [p for p in items if p["enforcement"] == "enforcing"]
    assert enforcings == []
    # still 30+ vendor entries surfaced
    assert len(items) >= 30


# ── description present, never empty (UI affordance) ──────────────
def test_each_entry_has_nonempty_description():
    c = _client()
    items = c.get("/presets").json()["presets"]
    for p in items:
        assert p.get("description"), f"{p['id']}: missing description"


# ── deterministic ordering (UX) ───────────────────────────────────
def test_presets_sorted_wired_first_then_alpha():
    """UX rule: wired entries surface first (operator sees what they have),
    then vendor entries alphabetical by ID."""
    c = _client()
    items = c.get("/presets").json()["presets"]
    wired_count = 5
    wired_ids = [p["id"] for p in items[:wired_count]]
    assert set(wired_ids) == {
        "citation-verify", "privilege-scan",
        "source-allowlist", "structured-output", "prompt-injection-screen",
    }
    vendor = [p["id"] for p in items[wired_count:]]
    assert vendor == sorted(vendor), "vendor not alpha-sorted"
