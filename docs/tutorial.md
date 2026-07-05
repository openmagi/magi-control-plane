# Your first real policy

This walks one policy end to end: author it, enable it, watch it deny a
tool call inside a real Claude Code session, issue the evidence that
satisfies it, watch the same call get allowed, and read the sealed verdict
in the ledger. It is the whole loop the product exists for, in about ten
minutes.

It assumes you have already installed the gate ([Getting
started](./getting-started.md)) and that the dashboard is up at
`http://localhost:3000`.

## The policy we are building

"Before the agent runs a `FILE_COURT_...` command, a citation check must
have passed." Until that evidence exists, the command is denied. This is
the `EvidencePolicy` archetype: a runtime hook consults the local
evidence ledger and blocks unless a verifier token is present.

## 1. Author the rule

Open `/policies/new` and pick the raw IR editor (the conversational
compiler and the guided wizard get you here too; raw IR is the most
explicit for a tutorial). Paste:

```jsonc
{
  "id": "tutorial/legal-filing",
  "description": "Block FILE_COURT commands until citations are verified.",
  "trigger": { "host": "claude-code", "event": "PreToolUse", "matcher": "Bash" },
  "sentinel_re": "FILE_COURT_(?P<matter>[A-Za-z0-9]+)_(?P<doc_id>[A-Za-z0-9]+)",
  "requires": [
    { "kind": "step", "step": "citation_verify", "verdict": "pass" }
  ],
  "action": "block",
  "type": "evidence"
}
```

Save. The cloud validates the IR and stamps `enforcement: "enforcing"`
because `citation_verify` is a wired verifier. See [Policy
IR](./policy-ir.md) for every field.

## 2. Enable it

A saved rule is authored, not yet live. Toggle it on (the enable control
on the rule, or activate a pack that owns it). The next compiled
`managed-settings.json` includes the hook. Restart Claude Code so it
re-reads the managed settings.

## 3. Watch it deny

In your Claude Code session, have the agent run a matching command, for
example:

```bash
FILE_COURT_smithVjones_draft1
```

The `PreToolUse` hook fires, the gate finds no `citation_verify` token in
the local ledger for this `(subject, payload_hash)`, and returns a deny.
Claude Code refuses to run the command and surfaces the reason. Nothing
ran; the deny is the point.

You can reproduce the same decision from a terminal, outside the agent:

```bash
echo FILE_COURT_smithVjones_draft1 | bash ~/.local/bin/magi-gate.sh
```

The JSON on stdout carries `permissionDecision: "deny"`. (The gate always
exits 0; the decision is in the payload, not the exit code.)

## 4. Issue the evidence

Now satisfy the requirement. `magi-cp emit` asks the cloud to run
`citation_verify`; on a `pass` it writes the signed token into the local
WAL:

```bash
magi-cp emit \
  --subject smithVjones \
  --payload-hash draft1 \
  --cite "The contract is void||2019Da12345" \
  --corpus "2019Da12345=The contract is void for illegality."
```

`--cite` is `quote||ref`; `--corpus` is `case_no=source_text`. Here the
quote is grounded in the corpus, so the verifier returns `pass` and the
token lands in `~/.magi-cp/local/wal.jsonl`.

## 5. Watch it allow

Re-run the command (in the session or via the shell one-liner above). The
gate now finds a valid, unexpired, correctly signed token bound to
`(smithVjones, draft1)` and returns allow. Claude Code runs the command.
Same rule, same input, opposite verdict, because the evidence now exists.

## 6. Read the sealed verdict

Every verdict, allow or deny, is appended to the Ed25519-signed,
hash-chained ledger. View it in the dashboard `/ledger`, or over the API:

```bash
curl -s http://localhost:8787/ledger -H "X-Api-Key: $MAGI_CP_API_KEY"
```

The response is the chain plus `chain_ok: true`. Your deny and your
subsequent allow are both in it, tamper-evident. See
[API > Ledger](./api.md#ledger).

## Optional: route to a human instead of blocking

Change the rule's `action` from `block` to `ask`. Now a missing or failing
check does not hard-deny; it enqueues a review item for a human. An
approver opens `/hitl`, sees the request, and approves it, which mints a
token. On the agent side, `magi-cp await-approval --hitl-id <id>` polls
until the decision lands and writes the issued token to the WAL, so the
gate allows. This is the human-in-the-loop path for calls that are
legitimate but need a person to sign off. See [CLI >
await-approval](./cli.md#await-approval).

## Where to go next

- [Policy IR](./policy-ir.md): the other archetypes (permissions,
  MCP gating, subagent scoping, the session-evidence pair) with examples.
- [Session-evidence gate](./session-evidence.md): make one tool depend on
  evidence an earlier step recorded this session.
- [Verifiers](./verifiers.md): the wired verifiers and writing your own.
