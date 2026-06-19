# magi-control-plane v1 build plan

> 2026-06-19. v0 (P0–P7) MERGED into main. v1 = policy authoring surface.
> Strategic frame: turn the single hardcoded policy into a *multi-policy*
> system with a real authoring UI, so the deployment shape becomes
> "load a domain pack → toggle/parameterize → preview compile → deploy".

## What v0 left undone (binding constraint for partner pilot)
- Policies are JSON files committed in `policies/`. Users cannot toggle/add/edit
- No REST CRUD on policies
- Dashboard is read/decide only — no authoring affordance
- HITL queue shows status but does not explain *why* (which predicate fired)
- No 5-tier precedence resolution (platform/org/bot/user/session) — the spec
  exists in the magi-agent reuse audit but isn't implemented

## v1 phases (TDD shape, multi-perspective review on milestones)

### v1-P1 — Backend foundations (≤ 1 day)
Reuse audit §5.1 #3·#4·#5·#6 — patterns only, not code, because magi-agent
is in-loop and magi-cp is out-of-loop.

| Module | Pattern from | Local home |
|---|---|---|
| Policy IR validation matrix (event × matcher × decision) | magi-agent `customize/custom_rules.py::_LEGAL` | `src/magi_cp/policy/ir.py` |
| Frozen-overrides accessor (`from_overrides` + `enabled_*_rules()`) | `customize/verification_policy.py` | `src/magi_cp/policy/resolved.py` (NEW) |
| 5-tier precedence (`platform > org > bot > user > session`) | `harness/policy_state.py::SOURCE_PRECEDENCE` (truncate) | `src/magi_cp/policy/precedence.py` (NEW) |
| Persistent store w/ round-trip `_normalize` | `customize/store.py` | `src/magi_cp/cloud/policy_store.py` (NEW) |

Tests: `tests/test_policy_matrix.py`, `test_policy_resolved.py`,
`test_policy_precedence.py`, `test_policy_store.py`.

### v1-P2 — `/policies` CRUD API (≤ 0.5 day)
- `GET  /policies` — list all (id, enabled, tier, source)
- `GET  /policies/{id}` — full IR + resolved view + last compile sha256
- `PUT  /policies/{id}` — replace IR (validated via `_LEGAL` matrix)
- `PATCH /policies/{id}/enabled` — toggle
- `GET  /policies/{id}/compiled` — managed-settings.json for THIS policy

Auth: new `X-Admin-Api-Key` (separate from API/HITL keys, fail-closed).
SECURITY.md note added.

### v1-P3 — Policy cards UI + compile preview (≤ 0.5 day, MILESTONE)
- `web/app/policies/page.tsx` — card grid (enabled state + enforcement label)
- `web/app/policies/[id]/page.tsx` — detail (IR YAML/JSON view + compiled
  managed-settings preview side-by-side + last compile sha256)
- Server actions for toggle (PATCH /enabled)

Milestone review: UX + auth + audit-fidelity subagents.

### v1-P4 — Policy builder modal (≤ 1 day)
- Structured form for IR fields (trigger, sentinel_re, requires[],
  on_missing, on_signature_invalid)
- Live preview of the compiled managed-settings.json
- `_LEGAL` matrix violations highlighted inline
- Save → PUT /policies/{id}
- Cancel without side effects

NL→IR assist is intentionally out-of-scope for v1 (deferred to v1.x).

### v1-P5 — HITL detail page "why review?" (≤ 0.5 day)
- `web/app/hitl/[id]/page.tsx`
- Shows: which policy fired, which predicate (existence/verbatim/NLI), each
  citation's source-text span vs quoted span (diff view), NLI score, ledger
  context (entries before/after)
- Approve/Reject inline (same validation as list page)

### v1-P6 — E2E + final milestone review (≤ 0.5 day, MILESTONE)
- E2E: create policy via API → toggle enable → compile → MCP `verify_citations`
  call → gate fires → dashboard shows entry
- Full regression (pytest + vitest)
- 3 subagent review (security / integration / regression). Iterate until PASS.

## Review protocol (same as v0)
- Per-phase: `code-reviewer` subagent. PASS/FAIL + concrete fixes. Iterate.
- Milestones (P1, P3, P4, P6): 3 subagents in parallel (security/architecture/UX).
  All must PASS.

## Out of scope for v1 (deferred to v1.x / v2)
- NL→IR LLM compiler (`shacl_compiler.py` 3-gate pattern) → v1.1
- SHACL verifier integration (full pyshacl) → v1.2
- Per-tenant multi-policy with tenant_id column → v2 (SECURITY.md §multi-tenant)
- kid rotation history at /pubkey → v2
- Real SourceResolver (cloud-side law.go.kr) → v1.1

## Success criteria
- 150+ tests (v0 baseline) + ≥ 30 new tests (v1)
- All v1 phases pass code-reviewer cycle
- All 3 milestone reviews PASS
- E2E demo: create new domain policy via UI → it actually gates a sentinel
- README updated to "v1 alpha"
