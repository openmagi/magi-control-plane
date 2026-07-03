# magi-control-plane

> Open-source governance gate for Claude Code (Codex / OpenCode next). Apache 2.0.

`magi-control-plane` is the runtime that sits between Claude Code and your
machine: a verifier registry, Ed25519-signed evidence ledger, HITL queue, and
an NL → IR policy authoring dashboard. Every tool call, prompt, and session
boundary can be gated against your policies at runtime; verdicts are sealed
into a tamper-evident chain. Self-host on docker or K8s.

## Self-host

One-line installer (replace `<your-instance>` and `mcp_YOUR_KEY` with your
deploy hostname + tenant key — see the install guide for getting them):

```bash
curl -fsSL https://<your-instance>/install.sh | bash -s -- mcp_YOUR_KEY
```

The installer:
1. installs the Python `magi-cp` package
2. drops `~/.claude/managed-settings.json` + `~/.local/bin/magi-gate.sh`
3. persists your key + cloud URL to `~/.config/magi-cp/env` (0600)
4. runs the smoke test to confirm the gate fires

See [docs/](docs/) for the install guide, operator guide, and architecture
reference.

## Quick start (dev)

```bash
make install          # editable install + dev deps
make test             # pytest
make cloud-dev        # cloud API on :8787  (registry-wired + builtins)
make build-plugin     # policies/*.json → plugin/managed-settings.json
cd web && npm install && npm run dev   # dashboard on :3000
```

The cloud is reachable at `http://127.0.0.1:8787`. `GET /healthz` is public;
everything else needs a key.

## CLI

```text
magi-cp gate                 PreToolUse hook reader (stdin → exit + JSON)
magi-cp emit                 request citation_verify, cache in WAL
magi-cp await-approval       poll HITL, write token to WAL
magi-cp compile <ir> <out>   Policy IR → managed-settings
magi-cp cloud                run FastAPI cloud server
magi-cp mcp                  stdio MCP server
magi-cp keys                 Ed25519 signing key lifecycle
magi-cp share <sessionId>    Claude Code run → public share link
```

## Architecture in one paragraph

Three layers. **Local** is the `magi-cp` CLI + Claude Code hook on your
machine: client side, verify-only, no signing keys. **Cloud**
(`src/magi_cp/cloud/`) is the policy authority, Ed25519 signer, tamper-evident
ledger, HITL queue, dashboard, and NL → IR authoring. **Floor** is the
managed-settings + plugin combination that prevents users from disabling the
gate mid-session; license expiry is fail-closed by design.

## Docs

Developer docs live under [`docs/`](docs/). The same content renders on
`cp.openmagi.ai/docs`.

- [Getting started](docs/getting-started.md)
- [Install](docs/install.md)
- [Architecture](docs/architecture.md)
- [Policy IR](docs/policy-ir.md)
- [Verifiers](docs/verifiers.md)
- [Operator](docs/operator.md) (deploy, rotate keys, observability)
- [API](docs/api.md)
- [CLI](docs/cli.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Share runs](docs/share-runs.md)

## License

Apache 2.0. See [LICENSE](LICENSE).
