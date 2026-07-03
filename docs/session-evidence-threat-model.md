# Session-evidence gate: threat model (what it does and does not defend)

The session-evidence pair (`EvidenceAuditPolicy` + `EvidencePreconditionPolicy`)
lets a gate depend on evidence an audit recorded earlier in the same session
(e.g. "execute_trade requires a credible source was fetched this run"). This note
states, honestly, what that guarantees.

## What it defends: an aligned agent that skips a step

The gate's job is to make a governed workflow deterministic — an agent that is
trying to do the right thing but might skip verification, hurry, or hallucinate
that it already checked. Against that (the realistic and common case) the gate is
a hard, model-independent guarantee: no credible-source evidence on record ->
the tool is denied, regardless of what the model "decided".

The evidence ledger lives at `~/.magi-cp/session-evidence/` — **outside any agent
workspace**. A normal run never reads or references it (the deny reason discloses
no path), so an aligned agent has no occasion to touch it.

## What makes it a hard control under governance

In a governed setup — managed-settings installed by the operator, the agent run
**without** `--dangerously-skip-permissions` — ship the companion deny policy so
the agent cannot read or write the ledger at all:

```
magi-cp-session-audit  # (the audit hook, the ONLY writer)
```
plus a permission deny on the ledger path:
```jsonc
// managed-settings.json permissions
"deny": [
  "Read(~/.magi-cp/session-evidence/**)",
  "Edit(~/.magi-cp/session-evidence/**)",
  "Write(~/.magi-cp/session-evidence/**)",
  "Bash(*~/.magi-cp/session-evidence*)"
]
```
Now CC's permission engine (enforced by the root-installed managed layer) is the
boundary: the hook writes evidence out-of-band; the agent is denied the path. A
forged `{"verdict":"pass"}` append is blocked by the deny, not by obscurity.

## What it does NOT defend: a hostile agent under skip-permissions

`--dangerously-skip-permissions` means "I fully trust this agent" — every deny is
off, so the agent can write anywhere, including the ledger. That is out of scope
by definition, exactly as CC's own permission prompts are bypassed in that mode.
On a single-operator localhost box the operator can also `sudo`, so no purely
local mechanism (a file, a local key) is a hard boundary there. For a hard control
against a hostile agent, the evidence must be produced and verified somewhere the
agent cannot write — a cloud-recorded, cloud-verified ledger (the Option A
follow-up in `docs/plans/2026-07-03-session-evidence-trust-redesign.md`).

## Accuracy (separate from anti-forgery)

The audit judge scores the **parsed hostname** of a fetched URL against an
allowlist (exact or subdomain suffix), never a raw substring — so a name that
merely contains `ir.`/`edgar` cannot pass — and only records when the tool call
actually returned a result (a 403/empty fetch records nothing) and only for real
fetches (WebFetch, or a Bash `curl`/`wget`, never a bare `echo <url>`).
