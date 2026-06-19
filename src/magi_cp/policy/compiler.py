"""Deterministic Policy IR → CC managed-settings.json compiler.

Guarantees: pure function (no LLM, no clock, no randomness), same input ⇒ same
output (byte-stable). Policy order preserved in `_magi_policies` meta.
"""
from __future__ import annotations
import json
import sys

from .ir import Policy, load_policy


def compile_to_managed_settings(policies: list[Policy]) -> dict:
    seen_ids: set[str] = set()
    for p in policies:
        p.validate()
        if p.trigger.host != "claude-code":
            raise ValueError(f"policy '{p.id}': host 'claude-code'만 지원(v0); got {p.trigger.host!r}")
        if p.id in seen_ids:
            raise ValueError(f"중복 policy id: {p.id!r}")
        seen_ids.add(p.id)

    events: dict[str, list[dict]] = {}
    for p in policies:
        events.setdefault(p.trigger.event, []).append({
            "matcher": p.trigger.matcher,
            "hooks": [{"type": "command", "command": p.gate_binary}],
        })

    return {
        "allowManagedHooksOnly": True,
        "permissions": {"defaultMode": "default"},
        "hooks": events,
        "_magi_policies": [
            {"id": p.id, "version": p.version, "description": p.description}
            for p in policies
        ],
    }


def compile_files(policy_paths: list[str], out_path: str) -> dict:
    policies = [load_policy(p) for p in policy_paths]
    settings = compile_to_managed_settings(policies)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return settings


def main() -> int:  # pragma: no cover (CLI shim)
    if len(sys.argv) < 3:
        print("usage: python -m magi_cp.policy.compiler <policy.json> [...] <out.json>",
              file=sys.stderr)
        return 2
    compile_files(sys.argv[1:-1], sys.argv[-1])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
