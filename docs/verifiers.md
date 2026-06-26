# Verifiers

A verifier is a deterministic function from a verifier request to a
verdict. The registry (`src/magi_cp/verifier/registry.py`) holds all
known verifiers keyed by `step` name. `register_builtins` (in
`verifier/builtins.py`) wires the 5 beachhead verifiers at boot.

## The wired verifiers

| Step | Class | What it checks |
|------|-------|----------------|
| `citation_verify` | `CitationVerifierAdapter` | Each cited authority exists in the corpus. Bare ID match, not verbatim grounding. |
| `privilege_scan` | `PrivilegeScanVerifier` | Privileged markers (attorney-client, Korean RRN patterns) do not appear in the payload. |
| `source_allowlist` | `SourceAllowlistVerifier` | Every cited URL belongs to the configured allowlist. |
| `structured_output` | `StructuredOutputVerifier` | Payload matches the declared JSON schema. |
| `prompt_injection_screen` | `PromptInjectionScreenVerifier` | Heuristic detection of jailbreak fragments. |

All five are deterministic. None call an LLM. They run in the cloud,
inside the request handler for the verifier route the policy IR points
at.

## Verifier interface

```python
from magi_cp.verifier.types import Verifier, VerifyRequest, VerifyResult

class MyVerifier(Verifier):
    step = "my_check"

    def verify(self, req: VerifyRequest) -> VerifyResult:
        # Inspect req.subject, req.payload_hash, req.named_groups, req.body
        # Return a VerifyResult with status='pass' | 'fail' | 'review'.
        ...
```

`VerifyRequest` carries the inputs the local gate captured plus the
named-group dict pulled from the sentinel regex. `VerifyResult` carries
the status, an operator-facing reason, and optional `evidence` dict
that gets sealed into the ledger entry.

## Registering a custom verifier

In a cloud-side plugin module:

```python
from magi_cp.cloud.app import _build_production_app
from magi_cp.verifier.registry import VerifierRegistry

def register_my_pack(reg: VerifierRegistry) -> None:
    reg.register(MyVerifier())
```

Wire it into the boot path by either:

1. Editing `verifier/builtins.py` (forks / Apache 2.0 forks) and adding
   a call to `register_my_pack(reg)` next to `register_builtins`.
2. Or installing it via the plugin entrypoint group `magi_cp.verifier_packs`
   (PEP 621 `[project.entry-points]`) - the cloud auto-discovers and
   calls each registered hook at boot.

A second `register()` call with the same step name raises
`ValueError(duplicate)`. That is intentional - silent re-registration
would mask a wiring bug.

## Authoring against a verifier that does not exist yet

Use the `preview:` prefix in `requires[].step`. The cloud accepts the
PUT and stamps `enforcement="preview"`. The compiled hook still ships;
the runtime DOES NOT skip the call. At runtime the route 404s and the
gate denies. This is fail-closed by construction - the intent is "wire
this policy in development, do not enable at scale until the verifier
ships".

To graduate to enforcing: land the verifier, redeploy the cloud, drop
the `preview:` prefix, re-PUT the policy. The cloud re-resolves and
stamps `enforcement="enforcing"`.

## Verifier evidence

Every verifier verdict is appended to the ledger as one entry. The
entry carries: `subject`, `payload_hash`, `step`, `status`, `reason`,
`evidence`, `kid` (signer key id), `signature`, and the `prev` hash
that chains it to the previous entry. `GET /ledger` returns the chain
plus a `chain_ok` boolean. A `false` return indicates tampering, a
restore from an inconsistent backup, or a verifier that emitted a
non-canonical body.

## Authoring tips

- Keep step names short and snake_case (`citation_verify`, not
  `verify-the-citation-please`).
- Avoid free-form evidence. Use stable keys so dashboards and downstream
  reports stay diff-clean.
- Reject inputs you do not understand instead of returning `pass`.
- When in doubt, return `review` and let the HITL queue carry the load.

## See also

- [Policy IR](./policy-ir.md) for how `requires[].step` is referenced.
- [API](./api.md) for the cloud's `POST /verify/{step}` contract.
- [Operator > Key rotation](./operator.md#key-rotation) for the Ed25519
  signer the ledger uses.
