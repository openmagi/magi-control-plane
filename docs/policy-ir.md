# Policy IR

Policy IR is the declarative spec the gate enforces. The compiler at
`src/magi_cp/policy/compiler.py` turns one or more IR rows into a single
Claude Code `managed-settings.json` blob. The IR itself is a set of
Python dataclasses in `src/magi_cp/policy/ir.py`, serialized to JSON on
disk and over the API; the compiler does not transform the shape.

## Where a rule sits

In the pack -> policy -> rule model (see
[Architecture](./architecture.md#packs-policies-and-rules)), one IR row
is a **rule**: the minimal compile unit. A **policy** is one authored
intent that owns one or more rules; a **pack** references policies. This
page documents the rule, i.e. the IR row.

The IR is a discriminated union, `AnyPolicy`. The original gate-binary
shape is `EvidencePolicy` (aliased as `Policy` for back-compat); the
other archetypes compile to native managed-settings surfaces without a
gate-binary hop.

## EvidencePolicy

A gate-binary rule: a runtime hook fires `gate_binary` against the tool
payload and the rule passes or fails based on its `requires[]` outcomes.

```jsonc
{
  "id": "legal-filing/v1",
  "description": "Require citation + privilege checks before a court filing.",
  "trigger": { "host": "claude-code", "event": "PreToolUse", "matcher": "Bash" },
  "sentinel_re": "^FILE_COURT_[A-Za-z0-9_]+_[A-Za-z0-9]+",
  "requires": [
    { "kind": "step", "step": "citation_verify" },
    { "kind": "step", "step": "privilege_scan" },
    { "kind": "step", "step": "source_allowlist" }
  ],
  "action": "block",
  "on_signature_invalid": "deny",
  "gate_binary": "/usr/local/bin/magi-gate.sh",
  "version": "0.1",
  "type": "evidence"
}
```

### Field reference

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | `^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$`. The cloud is the canonical validator (an admin-key holder bypasses the JS layer). |
| `description` | string | Operator-facing summary. |
| `trigger` | object | `host` (`claude-code`), `event` (`PreToolUse`, `SessionStart`, ...), `matcher` (tool name, e.g. `Bash`). |
| `sentinel_re` | string \| null | **Optional.** Anchored Python regex used only to decide whether a payload is rule-bound. If present it must compile; named groups are no longer prescribed. When absent, the runtime synthesizes `(subject, payload_hash)` from request context. |
| `requires[]` | list | Zero or more `EvidenceReq` conditions (see below). Empty means "unconditional signal": on every matched trigger the verdict is recorded but nothing is required. |
| `action` | enum | `block`, `ask`, or `audit`. The rule's primary intent. |
| `on_signature_invalid` | `"deny"` | Only `deny` is allowed in v0. |
| `gate_binary` | string | Path the runtime hook invokes. Default `/usr/local/bin/magi-gate.sh`. |
| `version` | string | Free-form version string, default `"0.1"`. |
| `type` | `"evidence"` | Union discriminator. |

There is no `enabled`, `tier`, or `source` field on the IR row. Those
live on the resolution wrapper (see [Precedence](#precedence)) and on the
policy / pack layer above.

### Action archetypes

- `block` - when `requires[]` does not all-pass, prevent the host action
  (the tool runs, the prompt sends, the compaction starts). The strongest
  pre-event gate.
- `ask` - interrupt for human approval (HITL) instead of blocking
  outright. Used for legally significant filings and similar.
- `audit` - record the verdict to the evidence ledger; never blocks.
  Combined with `requires=[]` this is the unconditional "emit signal"
  archetype.

### EvidenceReq kinds

`requires[]` is a discriminated union on `kind`. Only `step` references a
wired verifier; the other three are evaluated inline at gate time.

| kind | Field | Meaning |
|------|-------|---------|
| `step` | `step`, `verdict` | Reference a wired verifier by name (default). `verdict` defaults to `pass`. |
| `regex` | `pattern`, `field_path` | Python regex matched against the payload. `field_path` (optional, dotted, e.g. `tool_response.output`) scopes the match; empty matches the whole-payload projection. |
| `llm_critic` | `criterion` | Free-text rule judged by the configured LLM provider. Requires `MAGI_CP_LLM_COMPILER` / `MAGI_CP_LLM_REVIEWER`. |
| `shacl` | `shape_ttl` | Turtle SHACL shape validated against the payload dict with pyshacl. |

Because `llm_critic` exists, the runtime gate can invoke a model for that
requirement kind; the `step`, `regex`, and `shacl` kinds are
deterministic.

## Other archetypes

The IR union also carries archetypes that compile to native CC
managed-settings surfaces (no gate-binary hop), plus the
session-evidence pair:

| Type | Purpose |
|------|---------|
| `PermissionPolicy` | Compiles to `permissions.{allow,deny,ask}`. Also natively lowered for the Codex runtime. |
| `SubagentPolicy` | Disable a specific CC subagent via managed-settings. |
| `McpGatingPolicy` | Allow / deny a whole MCP server at the managed-settings level. |
| `ContextInjectionPolicy` | Static text injected into a CC hook handler via `additionalContext`. |
| `InputRewritePolicy` | Mutate a tool's input before the tool runs (`updatedInput`). |
| `RunCommandPolicy` | Run an inline shell command or attached script in response to a hook event. |
| `EvidenceAuditPolicy` | On a matched call, extract a subject, judge it, and append a record to the session ledger. Observational; never blocks. |
| `EvidencePreconditionPolicy` | On `PreToolUse`, deny (or `ask`) unless a required-kind record at the required verdict exists in the session ledger. |

The last two are the **authorable session-evidence gate**: an intent like
"only trade after a source was verified this session" authors as an
`EvidenceAuditPolicy` (records the verification) plus an
`EvidencePreconditionPolicy` (blocks the trade until that record exists).
Both accept an optional `project_scope` so the rule only fires when the
session cwd is inside the given directory. The ledger lives at
`~/.magi-cp/session-evidence/`, outside any agent workspace.

## Enforcement stamping

The cloud stamps an `enforcement` label when a rule is written
(`PUT /policies/{id}`):

- `enforcing` - all step refs resolve to an active registry entry.
- `preview` - one or more refs use the `preview:` prefix. The compiled
  hook still ships and fail-closes at runtime; the prefix is a flag for
  the operator, not a runtime no-op. See
  [Verifiers > Authoring against a verifier that does not exist yet](./verifiers.md#authoring-against-a-verifier-that-does-not-exist-yet).

A rule whose non-preview step ref does not resolve is rejected at PUT
time (a `StepResolutionError`), rather than silently persisted.

## Precedence

Precedence is a property of resolution, not a field on the rule. Rules
resolve across five sources, highest wins:

```
platform > org > bot > user > session
```

The resolver keys on rule `id`. There is also a tighten-only "floor"
resolution mode where a lower source may only narrow what a higher source
requires, never widen it.

## Sentinel regex

The sentinel is how the local gate decides whether a given payload is
rule-bound. Use anchored, narrow regexes. The local gate never executes
anything; the regex is only used for matching. The runtime no longer
reads specific named-group names; `(subject, payload_hash)` labels are
synthesized from request context (`_synth_subject_and_hash` in
`cloud/app.py`) when the regex does not supply them.

## Author from natural language

Two API paths (both admin-key gated), plus the dashboard `/policies/new`:

- `POST /policies/compile` - one-shot. Body `{ "nl": "..." }`. Response
  carries the structured `ir`, a critic LLM `review`, and a
  `schema_issues[]` array (empty when the IR is clean).
- `POST /policies/compile-interactive` - turn-by-turn. Carries the
  running `history`, `draft_so_far`, and the user's latest `answers`, and
  returns the next assistant turn plus an updated (compound-aware) draft.
  See [API](./api.md#policies).

Both require `MAGI_CP_LLM_COMPILER` / `MAGI_CP_LLM_REVIEWER` to be
configured. The runtime gate's deterministic kinds do not need them.

## Compile preview

`GET /policies/{id}/compiled` returns the `managed-settings.json` blob the
gate would receive if this rule were the only one active, plus a sha256
fingerprint so you can diff across edits.
