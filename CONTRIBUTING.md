# Contributing

magi-control-plane is open source under Apache 2.0. Contributions land
through GitHub pull requests against `main`.

## Dev loop

```bash
make install     # editable install + dev deps
make test        # pytest (~435 tests)
make cloud-dev   # FastAPI on :8787
cd web && npm install && npm run dev   # dashboard on :3787
```

## What's in scope

- Bug fixes against `src/magi_cp/` (cloud) and `web/` (dashboard)
- New verifiers (see `magi_cp.verifiers` registry)
- New presets in `policies/` + `src/magi_cp/cloud/presets_catalog.py`
- Translations: add a locale to `web/lib/i18n/dict.ts` (KO + EN ship today)
- Helm chart improvements (`charts/magi-cp/`)
- Documentation in `docs/`

## What's out of scope

- New integrations with closed-source agent runtimes
- Breaking the gate's fail-closed semantics (subscription/license expiry
  must continue to fail-closed; this is an audit-chain invariant)
- Anything that weakens the HMAC contract on `/admin/*` endpoints

## Tests are mandatory

- Every PR runs `pytest` + `vitest` + `tsc --noEmit` in CI
- Add tests next to the change (`*.test.ts` colocated; `tests/test_*.py`
  for Python)
- For verifier changes: hit `tests/test_builtin_verifiers.py`

## Coding rules

See `.claude/rules/*.md`. Highlights: TypeScript strict, no `any`,
functional React, kebab-case file names, no emoji icons (SVG only).

## Security disclosure

Email **security@openmagi.ai**. Do not file public issues for
vulnerabilities. We respond within 1 business day.

## License of contributions

By submitting a PR you agree your contribution is licensed under
Apache 2.0 (same as the project).
