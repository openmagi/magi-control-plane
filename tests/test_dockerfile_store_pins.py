"""Deploy invariant: every writable on-disk store must be pinned onto the
/data volume in the IMAGE.

The compose run uses `read_only: true`, so the container root filesystem
(including HOME=/home/magi) is unwritable — only the `/data` volume and a
`/tmp` tmpfs can be written. Each store defaults to `~/.magi-cp/*`
(= /home/magi/.magi-cp), so unless the Dockerfile pins it onto /data the
first write fails with a read-only PermissionError → 500 (POST
/policy-packs, /custom-verifiers, a policy-group save, a /scripts upload)
or a silently-swallowed boot pack-centric floor migration.

These pins MUST live in the Dockerfile (baked into the image), NOT only in
docker-compose.yml: the installer preserves a user's existing compose file
across upgrades, so a compose-only pin silently regresses for anyone who
installed before it was added.

Regression guard for the read-only-rootfs store bug (self-host cloud
container unhealthy / pack-create 500).
"""
from __future__ import annotations

from pathlib import Path

# Each writable store env var and the on-disk prefix it must resolve to.
# All must point under the /data volume (the only writable persistent path
# on the read-only rootfs).
_REQUIRED_DATA_PINS = {
    "MAGI_CP_KEY_DIR": "/data",
    "MAGI_CP_DSN": "/data",  # sqlite:////data/... — the DSN path is on /data
    "MAGI_CP_POLICY_STORE": "/data",
    "MAGI_CP_PACK_STORE": "/data",
    "MAGI_CP_CUSTOM_VERIFIER_STORE": "/data",
    "MAGI_CP_POLICY_GROUP_STORE": "/data",
    "MAGI_CP_SCRIPT_STORE_DIR": "/data",
}


def _dockerfile_env() -> dict[str, str]:
    """Parse `ENV KEY=VALUE` lines from the repo-root Dockerfile."""
    root = Path(__file__).resolve().parent.parent
    text = (root / "Dockerfile").read_text(encoding="utf-8")
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("ENV "):
            continue
        body = line[len("ENV "):].strip()
        if "=" not in body:
            continue  # `ENV KEY VALUE` legacy form unused here
        key, _, val = body.partition("=")
        env[key.strip()] = val.strip()
    return env


def test_every_writable_store_is_pinned_to_data_volume():
    env = _dockerfile_env()
    missing = [k for k in _REQUIRED_DATA_PINS if k not in env]
    assert not missing, (
        "Dockerfile is missing store-path pins for "
        f"{missing}. On the read-only rootfs these default to ~/.magi-cp "
        "(unwritable) and their first write 500s. Add "
        "`ENV <VAR>=/data/...` to the Dockerfile."
    )
    wrong = {
        k: env[k]
        for k, prefix in _REQUIRED_DATA_PINS.items()
        if prefix not in env[k]
    }
    assert not wrong, (
        "Dockerfile store pins must resolve under /data (the only writable "
        f"path on the read-only rootfs); these do not: {wrong}"
    )
