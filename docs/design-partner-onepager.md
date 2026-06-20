# magi-control-plane — for design partners

**Out-of-loop governance over Claude Code.** Your firm picks the rules; the
agent can't disable them; every action carries a cryptographic audit trail.

## What we built (beachhead: Korean legal filing)

When an attorney has Claude Code draft a court filing, four things
deterministically don't happen until a partner signs off:

1. **Citations to non-existent precedent.** Every `대법원 …도…` reference is
   validated against `law.go.kr`. Fabricated case numbers are blocked at
   the terminal-gate level — not a soft warning, not a model nudge.
2. **Misquotes of real precedent.** When the case exists but the quoted
   text isn't verbatim from the source, the filing is routed to a partner
   for explicit approval before submission.
3. **Privileged / RRN leaks.** Korean RRN patterns (`YYMMDD-N…`) and
   attorney-client privilege markers in the answer block the filing
   deterministically.
4. **Off-source synthesis.** Any URL outside the firm's allowlist
   (law.go.kr, scourt.go.kr, …) used as a citation source blocks the
   filing.

When all four pass: a signed Ed25519 token gates the actual `Bash`
command (`FILE_COURT_<matter>_<doc_id>`). The token plus the verdict +
matter/doc_id is appended to a hash-chained ledger — proof for the bar
association, the client, or a future audit.

## How it works (3 layers)

| Layer | Where | What |
|---|---|---|
| **Local gate** | attorney's laptop | reads CC's `PreToolUse` hook, consults WAL for a valid token, allow / deny |
| **Cloud** | your tenant on OpenMagi | issues signed tokens; runs deterministic verifiers (citation, privilege, source, JSON, injection); hosts HITL queue for partner reviews; serves /ledger |
| **Floor** | Claude Code itself | `managed-settings.json` enforces the hook — the agent literally cannot run a `FILE_COURT_*` bash command without the hook firing |

LLMs are used **only** in the authoring surface (translate NL policy into a
Policy IR JSON) — never in the runtime gate. Subscription expires →
fail-closed (every command denied) until renewed.

## What we'd need from you (design partner)

1. **One real filing workflow.** A handful of recent filings (anonymized or
   not — your call) so we can validate the citation list against `law.go.kr`
   and confirm the sentinel regex catches all the bash commands that
   actually leave your laptop.
2. **30-min weekly call** for the first 4 weeks. We'll demo current state,
   show audit-trail entries, gather edge cases.
3. **Permission to run the cloud in your tenant** (single-VPC, EU/Korea
   region, your choice). All data — ledger, citations, HITL decisions —
   stays in your tenant; we never see it.

## What you get

- **Pricing**: free for the first 3 design partners through 2026-Q3.
  After that, $499/firm/month (≤ 10 attorneys) or talk to us for
  enterprise.
- **Co-design rights** on:
  - the 5 wired verifiers — privilege regex, source allowlist, citation
    grounding strictness, prompt-injection patterns, structured-output
    schemas
  - the HITL UI (partner review queue + audit drill-down)
  - the NL policy builder
- **Code escrow**: should OpenMagi pivot away from this product, we
  release `magi-control-plane` under Apache 2.0 to all then-active design
  partners so you can self-host indefinitely.

## What we promise NOT to do

- **No agent training on your filings.** The cloud never sees text
  content unless you explicitly pass it via `corpus_override` for a
  specific verification, and even then it isn't persisted beyond the
  ledger entry.
- **No phone-home telemetry.** The cloud reports nothing to OpenMagi by
  default. Audit hooks (Prometheus, structlog JSON) are operator-controlled.
- **No silent enforcement changes.** Every Policy IR change is human-
  reviewed; the cloud rejects malformed IRs at write time.

## Tech specs (for your IT/security review)

- **Cloud**: FastAPI on Python 3.12, PostgreSQL or SQLite, Ed25519 signing
- **Container**: read-only rootfs, `runAsNonRoot:10001`, dropped caps,
  helm chart in `charts/magi-cp/`
- **Deploy options**: docker-compose (single-node), kubernetes helm
  (multi-replica with `serviceMonitor` for kube-prometheus integration)
- **Audit trail**: append-only hash chain in PG/SQLite; `chain_ok` over
  full history verifies tamper-evidence; backups encrypted with `age`
- **Key rotation**: `magi-cp keys rotate|list|revoke` CLI; multi-kid map
  on `/pubkey` lets clients verify rotated-out tokens until expiration
- **Test suite**: ~417 Python + 72 web; CI on every PR
- **License**: source-available pilot; Apache 2.0 if escrow triggers

## Repo

<https://github.com/openmagi/magi-control-plane> — currently private; we'll
grant your engineering reviewer read access during the pilot.

## Reach us

<kevin@openmagi.ai> — let's set up a 30-minute intro and see if it fits
your workflow.
