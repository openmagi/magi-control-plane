# Session-evidence primitive: trust-model redesign (before landing PR #65)

Status: **DESIGN REVISION.** PR #65 (feat/session-evidence-primitive) is
NOT merged: a security review found two P0 enforcement bypasses that are
architectural, not code-level. This doc records the findings and the trust
decision that must precede a merge.

## 1. What PR #65 builds (and what is genuinely good)

A new first-class primitive so the dashboard can author "policy B requires
evidence that policy A produced earlier in this session":
- `session_evidence.py`: session-keyed JSONL ledger (record/has/entries).
- `magi-cp-session-audit` (PostToolUse): extract url -> judge -> record.
- `magi-cp-session-gate` (PreToolUse): deny if ledger lacks (kind, pass).
- `EvidenceAuditPolicy` + `EvidencePreconditionPolicy` IR archetypes +
  compiler wiring. Legacy gate untouched, matrix unchanged.

Verified-solid by review: path-traversal defense (same sanitizer on
read+write), shlex/JSON injection closed, verdict canonicalization (unknown
-> review), fail-CLOSED on an unreadable ledger, no matrix widening. The
authoring layer (IR + compiler) is the right shape and is the target the
dashboard UI would sit on.

## 2. Why it cannot merge as-is (the trust hole)

The gate exists to constrain a coding agent. But the evidence it trusts is
produced from, and stored in, things that SAME agent controls:

- **P0 self-attest (session_audit.py).** The recorded verdict is derived
  from the agent's REQUEST input, not a verified result: `extract_subject`
  reads `tool_input.url` / the first URL in the Bash command string, and
  `judge_domain_credibility` does UNANCHORED substring matching
  (`"ir."`, `"edgar"`, `"investor."`). A no-op `Bash("echo https://sec.gov")`
  or a URL like `https://evil.blog/dir.html` (contains `ir.`) records a
  `pass` for `source_credibility` without any real credible fetch. The gated
  agent forges the evidence that clears its own gate.
- **P0 writable ledger (session_evidence.py).** The ledger is a plaintext
  JSONL at a predictable path (`~/.magi-cp/session-evidence/<session>.jsonl`)
  that the agent can `Write`/`Bash`-append `{"kind":...,"verdict":"pass"}`
  to directly, bypassing the audit hook entirely.

Plus P1/P2 (fix regardless of the trust decision):
- P1: `session_gate.py` hardcodes a `PreToolUse` verdict envelope, but the IR
  lets `EvidencePreconditionPolicy` be authored on other block-legal events
  (UserPromptSubmit, PostToolUse, ...). There the compiler files the hook but
  the binary emits the wrong shape -> CC drops it -> silent no-op (green
  policy, zero enforcement). Also `action="ask"` is accepted by the IR but
  the gate only ever emits `deny`.
- P2: gate fails OPEN on unparseable payload / missing session_id.
- P2: `_safe_session` lossy char-substitution can alias two session ids onto
  one file (cross-session evidence reuse).
- P2: `policy_from_dict` raises bare `KeyError` on missing required fields.

## 3. The trust decision (this is the fork; pick before coding)

The primitive is only meaningful if evidence cannot be forged by the gated
agent. Three postures:

### Option A: Gate verifies the result itself (no separate evidence store)
The precondition hook, at the moment it fires, re-derives the fact it needs
from the agent's OWN transcript / prior tool_responses that the CLOUD
recorded (the cloud ledger the agent cannot write). E.g. "execute_trade
requires a prior WebFetch whose RESPONSE (server-fetched body, seen by the
gate/cloud) came from an allowlisted host." Evidence = the cloud's own
record of what actually happened, keyed by session, never the agent's input.
- Pro: no forgeable local file; the audit judge disappears (no self-attest).
- Con: needs the cloud to persist per-session tool_responses (or the gate to
  re-fetch/re-verify), and the gate must read them at decide time.

### Option B: Signed local ledger
Keep the local ledger but make each entry HMAC-signed by a key the agent
cannot read (installed root-owned, like the managed requirements layer). The
audit hook signs; the gate verifies the signature. A hand-appended line has
no valid signature -> ignored.
- Pro: keeps the local-first shape; smaller cloud change.
- Con: the audit judge STILL must derive the verdict from a verified result
  (tool_response + parsed-hostname allowlist), or Option B signs a forged
  verdict. And key protection on a single-operator localhost box is weak
  (the operator IS the agent's user; a determined local process can often
  read the key). Honest framing: defense-in-depth, not a hard control
  against a hostile local agent.

### Option C: Ship as observability only (not enforcement)
Land the IR/compiler + audit as an AUDIT-only surface (records evidence,
dashboard shows it), and DROP the precondition `deny` until A or B lands.
The Sessions/ledger UX gets real data; nothing claims to block on forgeable
evidence.
- Pro: unblocks the good 80% (authoring + audit trail) immediately, honest.
- Con: doesn't deliver the "execute_trade requires source_credibility"
  enforcement demo yet.

## 4. Recommendation

- **Fix P1/P2 unconditionally** (PreToolUse pin, fail-closed, hash the
  session key, clean loader errors): they're bugs on any path.
- **For the P0s:** the audit judge must in ALL options derive its verdict
  from the tool_RESPONSE (a verified fetch) with a PARSED-hostname allowlist,
  never the request input or a substring. That single change kills the
  self-attest even before the storage question.
- **Storage:** default to **Option A** (cloud-recorded evidence keyed by
  session) as the real control, because on a single-operator localhost box a
  local file / local key is not a boundary against the agent. If A is too big
  for now, ship **Option C** (audit + authoring, no enforcement deny) and
  label the precondition "requires the cloud-evidence backend (follow-up)".

## 5. Non-goals / what stays

- Keep the IR archetypes + compiler wiring (good; reused by all options).
- Keep the path-traversal + shlex + fail-closed-on-unreadable defenses.
- Do not merge the precondition `deny` while its evidence is forgeable.
