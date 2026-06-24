# Quickstart

Pilot a magi-control-plane gate over Claude Code on a single laptop in under
10 minutes. This walks the full path: cloud → policy → CC plugin → gated
bash command.

## 0. Prereqs

- Python 3.11+
- Node 20+ (for the dashboard)
- Claude Code 2.0+ installed (`claude --version`)
- Anthropic and OpenAI API keys (for `/policies/compile`; the runtime gate
  itself never calls an LLM)

## 1. Install + boot the cloud

```bash
git clone https://github.com/openmagi/magi-control-plane.git
cd magi-control-plane
make install

# Required API keys — single-tenant first
export MAGI_CP_API_KEY=$(uuidgen)
export MAGI_CP_HITL_API_KEY=$(uuidgen)
export MAGI_CP_ADMIN_API_KEY=$(uuidgen)
export MAGI_CP_ADMIN_HMAC_SECRET=$(python -c 'import secrets;print(secrets.token_hex(32))')

# LLM providers (for NL→IR compile)
export ANTHROPIC_API_KEY=sk-ant-…
export OPENAI_API_KEY=sk-…
export MAGI_CP_LLM_COMPILER=magi_cp.llm.anthropic_provider:anthropic_default
export MAGI_CP_LLM_REVIEWER=magi_cp.llm.openai_provider:openai_default

make cloud-dev          # serves on http://127.0.0.1:8787
```

In another terminal:

```bash
curl http://127.0.0.1:8787/healthz       # {"status":"ok"}
curl http://127.0.0.1:8787/presets | jq '.presets | length'   # 38+ entries
```

## 2. Author a policy via NL

```bash
curl -s -X POST http://127.0.0.1:8787/policies/compile \
  -H "X-Admin-Api-Key: $MAGI_CP_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"nl": "Block bash commands matching FILE_COURT_<subject>_<payload_hash> when citation_verify has not passed."}' \
  | jq '{ir, review, schema_issues}'
```

You'll see a structured IR, a critic LLM review, and a schema_issues array
(empty when the IR is clean). Edit the IR, then save it:

```bash
curl -s -X PUT http://127.0.0.1:8787/policies/legal-filing/v1 \
  -H "X-Admin-Api-Key: $MAGI_CP_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"policy": <PASTE_IR_HERE>, "source": "platform"}'
```

The dashboard at <http://127.0.0.1:3000/policies/compile> (run `cd web && npm
run dev`) wraps the same flow with a textarea + "Edit & save" handoff.

## 3. Wire Claude Code to consult the gate

Build the managed-settings JSON:

```bash
make build-plugin       # plugin/managed-settings.json
```

Install the plugin:

```bash
# Edit on macOS:
mkdir -p "$HOME/Library/Application Support/ClaudeCode"
cp plugin/managed-settings.json \
   "$HOME/Library/Application Support/ClaudeCode/managed-settings.json"
```

Install the gate binary on PATH so the hook can call it:

```bash
sudo cp plugin/magi-gate.sh /usr/local/bin/magi-gate.sh
sudo chmod +x /usr/local/bin/magi-gate.sh
```

Restart Claude Code. Any `Bash` tool call matching the policy's
`sentinel_re` will now consult the gate before executing.

## 4. Test the loop

In a Claude Code session:

```bash
echo FILE_COURT_M1_D1 motion.pdf
```

Without a token: the gate calls /citation_verify with empty citations →
deny → CC blocks the command. With a valid token in WAL (`magi-cp emit
--subject M1 --payload-hash D1 --citations '[…]' --corpus-override '{…}'`):
pass → token cached → gate allows.

## 5. Inspect the audit trail

```bash
curl -s http://127.0.0.1:8787/ledger \
  -H "X-Api-Key: $MAGI_CP_API_KEY" | jq '.entries | length, .chain_ok'
```

The hash chain returns `chain_ok: true` over the full history. Each entry
records the subject, payload_hash, verdict, and a signed token (when one
was issued).

## Next steps

- Multi-tenant pilot? Provision tenants via the admin API (see
  `/admin/tenants` in the [README](../README.md)).
- Stripe webhook integration with clawy? See `docs/operator-runbook.md`.
- Troubleshooting? See `docs/troubleshooting.md`.
