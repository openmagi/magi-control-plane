# Verifiers

A verifier is a function from a payload dict to a `Verdict`. The registry
(`VerifierRegistry` in `src/magi_cp/verifier/protocol.py`) holds all known
verifiers keyed by both `name` and `step`. `register_builtins` (in
`verifier/builtins.py`) wires the 5 beachhead verifiers at boot.

## The wired verifiers

| Step | Class | What it checks |
|------|-------|----------------|
| `citation_verify` | `CitationVerifierAdapter` | Each cited authority exists in the corpus. Bare ID match, not verbatim grounding. |
| `privilege_scan` | `PrivilegeScanVerifier` | Privileged markers (attorney-client, Korean RRN patterns) do not appear in the payload. |
| `source_allowlist` | `SourceAllowlistVerifier` | Every cited URL belongs to the configured allowlist. |
| `structured_output` | `StructuredOutputVerifier` | Payload matches the declared JSON schema. |
| `prompt_injection_screen` | `PromptInjectionScreenVerifier` | Heuristic detection of jailbreak fragments. |

These five are deterministic and never call an LLM. They run in the
cloud, inside the request handler for the verifier route the rule's
`requires[].step` points at. (A rule can also carry an inline
`llm_critic` requirement kind, which does call a model; see
[Policy IR > EvidenceReq kinds](./policy-ir.md#evidencereq-kinds).)

## Verifier interface

Every verifier satisfies the `Verifier` protocol: a set of read-at-boot
attributes plus a `run` method.

```python
from magi_cp.verifier.protocol import Verifier, Verdict, Enforcement

class MyVerifier:  # structural type; no base class required
    name = "my_check"
    step = "my_check"
    category = "content"
    enforcement = Enforcement.enforcing
    description = "One-line operator-facing summary."
    input_schema = {"type": "object"}

    def run(self, payload: dict) -> Verdict:
        # Inspect the raw payload dict (shaped per input_schema).
        if bad(payload):
            return Verdict(status="deny", reasons=["why it failed"])
        return Verdict(status="pass")
```

`Verdict.status` is one of `pass`, `review`, or `deny`:

- `pass` -> token issued, gate allows.
- `review` -> token issued with a HITL flag, gate routes to the HITL
  queue.
- `deny` -> no token, gate blocks.

`reasons` is a list of operator-facing strings. There is no `verify()`
method, no `VerifyRequest` / `VerifyResult`, and no `evidence` field on
the verdict; the ledger-side fields (`subject`, `payload_hash`, ...) are
added by the cloud when it seals the verdict, not by the verifier.

`enforcement` (an `Enforcement` enum) labels how the verdict participates
in the gate: `enforcing`, `always_on`, `preview`, or `capability`.

## Registering a custom verifier

Build a class satisfying the protocol and register it on a
`VerifierRegistry`:

```python
from magi_cp.verifier.protocol import VerifierRegistry

def register_my_pack(reg: VerifierRegistry) -> None:
    reg.register(MyVerifier())
```

Wire it into the boot path one of two ways:

1. Edit `verifier/builtins.py` (or your fork) and call
   `register_my_pack(reg)` next to `register_builtins`.
2. Construct your own registry and pass it to `create_app`. The
   production wiring in `_build_production_app` (`cloud/app.py`) does
   exactly this: `reg = VerifierRegistry(); register_builtins(reg)` then
   `create_app(verifier_registry=reg, ...)`.

`register()` runs a shape check (all required attributes present and
correctly typed) and rejects a duplicate `name` or `step` with a
`ValueError`. That is intentional: silent re-registration would mask a
wiring bug.

## Authoring against a verifier that does not exist yet

Use the `preview:` prefix in `requires[].step`. The cloud accepts the PUT
and stamps `enforcement="preview"`. The compiled hook still ships and the
runtime does NOT skip the call: at runtime the unresolved step denies.
This is fail-closed by construction, the intent being "wire this rule in
development, do not enable at scale until the verifier ships."

To graduate to enforcing: land the verifier, redeploy the cloud, drop the
`preview:` prefix, and re-PUT the rule. The cloud re-resolves and stamps
`enforcement="enforcing"`.

## Verifier evidence

Each sealed verdict is appended to the ledger as one entry, chained by
hash to the previous entry and signed with the active `kid`.
`GET /ledger` returns the chain plus a `chain_ok` boolean. A `false`
return indicates tampering, a restore from an inconsistent backup, or a
verifier that emitted a non-canonical body.

## Authoring tips

- Keep step names short and snake_case (`citation_verify`, not
  `verify-the-citation-please`).
- Keep verdict reasons stable and diff-clean so dashboards and downstream
  reports stay legible.
- Reject inputs you do not understand instead of returning `pass`.
- When in doubt, return `review` and let the HITL queue carry the load.

## See also

- [Policy IR](./policy-ir.md) for how `requires[].step` is referenced.
- [API](./api.md) for the cloud's `POST /verify/{step}` contract.
- [Operator > Key rotation](./operator.md#key-rotation) for the Ed25519
  signer the ledger uses.
