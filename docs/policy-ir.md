# Policy IR

Policy IR is the declarative spec the gate enforces. The compiler at
`src/magi_cp/policy/compiler.py` turns one or more IR rows into a single
Claude Code `managed-settings.json` blob. LLMs only appear in the
authoring path (NL -> IR). The runtime gate never calls a model.

## File layout

Each policy is one JSON file under `policies/<policy_id>.json` (or one
row in the cloud's policy store). Disk format is exactly the IR; the
compiler does not transform the shape.

## Schema

```jsonc
{
  "id": "legal-filing/v1",
  "version": 1,
  "enabled": true,
  "tier": "platform",
  "source": "platform",
  "sentinel_re": "^FILE_COURT_(?<subject>[A-Za-z0-9_]+)_(?<payload_hash>[A-Za-z0-9]+)",
  "requires": [
    { "step": "citation_verify" },
    { "step": "privilege_scan" },
    { "step": "source_allowlist" }
  ],
  "on_pass": { "issue_token_ttl_s": 600 },
  "on_fail": { "verdict": "deny", "reason_template": "{step} failed" }
}
```

## Field reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | `^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$`. Reserved suffixes `/compiled`, `/enabled` are rejected. |
| `version` | int | yes | Bump on breaking IR change. The cloud keeps the latest only. |
| `enabled` | bool | yes | Compiled JSON skips disabled rows but the dashboard still lists them. |
| `tier` | enum | yes | One of `platform`, `org`, `bot`, `user`, `session`. 5-tier precedence (highest wins). |
| `source` | enum | yes | One of `platform`, `tenant`, `user`. Audit only. |
| `sentinel_re` | regex | yes | Anchored Python regex; named groups feed verifier inputs. |
| `requires[]` | list | yes | Verifier step refs. Use the bare step name or prefix with `preview:` (see Authoring). |
| `on_pass.issue_token_ttl_s` | int | yes | Token TTL in seconds. Bound at issue time. |
| `on_fail.verdict` | enum | yes | `deny` or `review`. `review` routes the verdict to HITL. |
| `on_fail.reason_template` | string | no | f-string-style template; `{step}` is the failing step name. |

## Enforcement stamping

The cloud stamps an `enforcement` field on `PUT /policies/{id}`:

- `enforcing` - all step refs resolve to an active registry entry.
- `preview` - one or more refs use the `preview:` prefix. The compiled
  hook still ships and fail-closes at runtime; the prefix is a flag for
  the operator, not a runtime no-op.
- `unresolved-legacy` - a pre-stamp policy whose step ref no longer
  resolves. The compiler omits the row; the dashboard renders a warning.

See [Operator > Authoring against in-development verifiers](./operator.md#authoring-against-in-development-verifiers)
for the full lifecycle of `preview:` policies.

## Sentinel regex

The sentinel is how the local gate decides whether a given `Bash`
command is policy-bound. Use anchored, narrow regexes. Each named group
becomes an input to the verifier registry (`subject` and `payload_hash`
are the canonical pair). The local gate never executes anything; the
regex is only used for matching plus group extraction.

## 5-tier precedence

When multiple rows match the same sentinel, the precedence is:

```
platform > org > bot > user > session
```

The compiler resolves the winner per `(sentinel_re, step)` pair. Lower
tiers can broaden but cannot weaken what a higher tier requires.

## Author from natural language

Two paths:

- Dashboard `/policies/new` - NL primary, IR draft pane, save when ready.
- `POST /policies/compile` - same flow as a JSON API. Body:

```json
{ "nl": "Block bash commands matching FILE_COURT_<subject>_<payload_hash> when citation_verify has not passed." }
```

Response includes the structured `ir`, a critic LLM `review`, and a
`schema_issues[]` array (empty when the IR is clean).

LLMs do not appear in the runtime path. The authoring LLMs are
configurable via `MAGI_CP_LLM_COMPILER` and `MAGI_CP_LLM_REVIEWER`
(see [Operator > Environment](./operator.md#environment)).

## Compile preview

`GET /policies/{id}/compiled` returns the `managed-settings.json` blob
the gate would receive if this policy were the only one active. The
dashboard shows the IR side-by-side with the compiled JSON plus a
sha256 fingerprint so you can diff across edits.
