# Session-evidence gate

The session-evidence gate makes one tool call depend on evidence that an
earlier step in the same Claude Code session produced. The canonical
example: "only allow `execute_trade` after a credible source was actually
fetched this run." It turns a soft "the agent should check first" into a
hard, model-independent precondition.

It is built from two IR archetypes (see
[Policy IR](./policy-ir.md#other-archetypes)) that you author together as
one intent:

- **`EvidenceAuditPolicy`** records evidence. On each matched tool call it
  extracts a subject (e.g. the fetched URL), judges it (e.g.
  domain-credibility), and appends a record of a named `kind` to the
  session ledger. It is observational and never blocks.
- **`EvidencePreconditionPolicy`** consumes evidence. On `PreToolUse` it
  denies (or asks) unless a record of the required `kind` at the required
  verdict exists in the session ledger for this session.

A single authored intent expands to this audit + precondition pair. In
the pack -> policy -> rule model that is one **policy** owning two
**rules**.

## Author one

Two paths, both admin-key gated:

- Dashboard: `/policies/new/evidence-gate` is the dedicated builder. It
  takes a plain-language seed ("require a credible source before a
  trade") and drafts the compound.
- Conversational: `/policies/new` (default mode) authors the same
  compound over a turn, reusing an existing audit producer when one
  already covers the evidence you need.

Either path saves as a compound policy (see
[API > `POST /policies/compound`](./api.md#policies)).

## Scope it to a project

Both archetypes accept an optional `project_scope`. When set, the audit
and the precondition only fire when the session's working directory is
inside that path. The check is a real path-boundary test, so `/a/proj`
does not match `/a/proj-x`. Use it to keep a trading gate from firing in
an unrelated repo open in the same account.

## Where the ledger lives

Records are written to `~/.magi-cp/session-evidence/`, outside any agent
workspace. The audit hook (`magi-cp-session-audit`) is the only writer.
Under governance you also ship a managed-settings permission deny on that
path so the agent cannot forge a `pass` record. What this does and does
not defend against (a hostile agent under `--dangerously-skip-permissions`
is out of scope for a purely local control) is spelled out in the
[session-evidence threat model](./session-evidence-threat-model.md).

## See also

- [Policy IR](./policy-ir.md) for the `EvidenceAuditPolicy` /
  `EvidencePreconditionPolicy` fields.
- [Architecture > Packs, policies, and rules](./architecture.md#packs-policies-and-rules).
