# API

REST reference for the cloud (FastAPI). All routes return JSON. All
mutating routes require auth via `X-Api-Key`, `X-Admin-Api-Key`, or
`x-magi-signature` HMAC.

## Auth scopes

| Header | Source | Scope |
|--------|--------|-------|
| `X-Api-Key` | Tenant `mcp_...` key | Tenant-scoped reads / token emits. |
| `X-Hitl-Api-Key` | HITL operator key | HITL queue read / approve / reject. |
| `X-Admin-Api-Key` | Admin key | Policy CRUD, verifier registry inspect. |
| `x-magi-signature` + `x-magi-timestamp` | HMAC-SHA256 over `method\npath\ntimestamp\nbody` | Admin endpoints called from automation. |

The admin HMAC signs `METHOD\nPATH\nTIMESTAMP\nBODY` (newline-joined), where
`PATH` has no query string and `TIMESTAMP` is unix seconds. Present the hex
digest in `x-magi-signature` and the same timestamp in `x-magi-timestamp`.
Binding method + path stops a captured signature being replayed on a different
admin route; the timestamp must be within 300 seconds of the server clock, so a
capture expires. Sign over the exact body bytes sent (re-serializing JSON
between sign and send changes the bytes and the request will 401).

## Health

```http
GET /healthz
200 OK
{"status":"ok"}
```

Public, no auth. The smoke test uses this.

## Policies

```http
GET /policies
X-Admin-Api-Key: ...

200 OK
{"policies": [{"id": "legal-filing/v1", "enabled": true, "tier": "platform", "enforcement": "enforcing"}]}
```

```http
GET /policies/{id}
X-Admin-Api-Key: ...

200 OK
{"id": "legal-filing/v1", "version": 1, "policy": <IR>, "compiled_sha256": "...", "enforcement": "enforcing"}
```

```http
PUT /policies/{id}
X-Admin-Api-Key: ...
Content-Type: application/json

{"policy": <IR>, "source": "platform"}
```

Validates the IR against the `_LEGAL` matrix and stamps `enforcement`.
422 on schema fail, unknown step, or inactive step.

```http
PATCH /policies/{id}/enabled
X-Admin-Api-Key: ...

{"enabled": false}
```

```http
GET /policies/{id}/compiled
X-Admin-Api-Key: ...

200 OK
{"managed_settings": {...}, "sha256": "..."}
```

```http
POST /policies/compile
X-Admin-Api-Key: ...

{"nl": "Block bash commands matching FILE_COURT_..."}

200 OK
{"ir": {...}, "review": "...", "schema_issues": []}
```

## Verify

```http
POST /verify/{step}
X-Api-Key: ...

{"subject": "M1", "payload_hash": "D1", "body": {...}}

200 OK
{"status": "pass", "reason": null, "token": "ed25519:..."}
```

`status` is `pass`, `fail`, or `review`. On `pass`, a signed token is
issued bound to `(subject, payload_hash)` with TTL from the policy. On
`review`, the verdict is enqueued into HITL and the gate is told to
wait.

## HITL

```http
GET /hitl
X-Hitl-Api-Key: ...

200 OK
{"items": [{"id": "...", "subject": "M1", "payload_hash": "D1", "step": "citation_verify", "reason": "...", "created_at": ...}]}
```

```http
POST /hitl/{id}/approve
X-Hitl-Api-Key: ...

{"reason": "verified by partner"}

200 OK
{"token": "ed25519:..."}
```

```http
POST /hitl/{id}/reject
X-Hitl-Api-Key: ...

{"reason": "fabricated citation"}
```

## Ledger

```http
GET /ledger
X-Api-Key: ...

200 OK
{"entries": [...], "chain_ok": true}
```

The chain links each entry by hash. `chain_ok: false` indicates
tampering, restore from an inconsistent backup, or a verifier emitting
non-canonical bodies. See [Operator > Backups](./operator.md#backups).

## Pubkey

```http
GET /pubkey?kid=...

200 OK
{"kid": "...", "public_key_hex": "..."}
```

Public, no auth. The local gate pulls the pubkey for the `kid` stamped
on each signed token. Rotation does not break in-flight tokens because
the gate pins per `(subject, payload_hash)`.

## Admin: tenants

HMAC-signed. The signature is hex-encoded SHA-256 over the raw body and
sent in `x-magi-signature`.

```http
POST /admin/tenants
x-magi-signature: <hmac_sha256 hex over METHOD\nPATH\nTIMESTAMP\nBODY>
x-magi-timestamp: <unix seconds>
Content-Type: application/json

{"tenant_id": "t-cus-NhP3oABC123", "plan": "default", "expires_at": 1769990400}

200 OK
{"id": "t-cus-NhP3oABC123", "status": "active", "plan": "default", "expires_at": 1769990400}
```

Idempotent on `tenant_id`. Re-POSTing returns the existing record.

```http
POST /admin/tenants/{tenant_id}/keys
x-magi-signature: <hmac_sha256 hex over METHOD\nPATH\nTIMESTAMP\nBODY>
x-magi-timestamp: <unix seconds>

{}

200 OK
{"api_key": "mcp_...", "key_id": "..."}
```

**Not idempotent.** Every call issues a fresh key. The caller is
responsible for tracking which keys have been delivered.

```http
GET /admin/tenants/{tenant_id}
X-Admin-Api-Key: ...

200 OK
{"id": "...", "status": "active", "plan": "default", "expires_at": ...}
```

## Share runs

See [Share runs](./share-runs.md) for the public `openmagi.runView.v1`
contract and the `/r/{token}` dashboard route.

```http
POST /v1/runs/share
X-Api-Key: ...

{"session_id": "...", "run_view": {...}}

200 OK
{"token": "...", "token_hash": "...", "url": "https://<your-dashboard>/r/..."}
```

```http
GET /share/run/{token}

200 OK
{"run_view": {...}, "expires_at": null}
```

```http
POST /v1/runs/share/{token_hash}/revoke
X-Api-Key: ...

200 OK
{"revoked_at": ...}
```

```http
GET /v1/runs/share
X-Api-Key: ...

200 OK
{"shared_runs": [{"token_hash": "...", "created_at": ..., "revoked_at": null}]}
```

## Metrics

```http
GET /metrics
```

Prometheus exposition. Only attached when the `[observability]` extra
is installed and inside `_build_production_app`. See
[Operator > Observability](./operator.md#observability).

## Error format

```json
{
  "detail": "human readable message",
  "code": "policy_unknown_step",
  "extra": {"step": "preview:my_check"}
}
```

`code` is a stable string. `extra` is optional structured context.
