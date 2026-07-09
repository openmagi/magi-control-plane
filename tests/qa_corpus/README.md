# QA authoring corpus (L2)

Hand-authored scenario fixtures for the magi-cp conversational-authoring QA
harness. Each file under `scenarios/<id>.json` is one scenario. The loader
and validator live in `tests/qa_harness/corpus.py`; the schema test lives in
`tests/test_qa_corpus_schema.py`.

Design reference (clawy `docs/plans/`):
- `2026-07-09-magi-cp-authoring-qa-harness-design.md` (Sections 0.3, 1.4, 5.1, 5.2).
- `2026-07-06-magi-cp-conversational-authoring-coverage-audit.md` (the S0-S52 rows).

The corpus is a regression guard, not a bug hunt: every audit finding is
already fixed at base. Each scenario encodes the correct behavior so a future
change that reintroduces a class of failure fails CI.

## Field semantics (Section 5.1)

One JSON object per file. `scenarios/<id>.json` where the filename stem MUST
equal the `id` field.

- `schema_version` (int): fixture format version. Currently `1`.
- `id` (string, kebab-case): unique across the corpus; equals the filename stem.
- `category` (enum): one of
  `happy_path, wide_event, negated_enforce, enforce_verb, overtrigger_bait,
  ambiguous, adversarial_injection, malformed, out_of_scope, infeasible_runtime,
  wrong_language, archetype_run_command, archetype_compound, pack_shaped`.
- `language` (enum): `ko` or `en`. The language the phrasings are written in.
- `style` (string): phrasing register. Seeds use `canonical`; LLM expansion
  (PR-F) adds `terse`, `verbose`, `ambiguous`.
- `runtime_id` (string or null): `null` for the default claude-code path, or
  `codex` for the Codex-runtime lane (feasibility classification differs).
- `engine` (enum): the L3 replay engine.
  - `fake_empty`: deterministic. The phrasing lands in the extractor vocabulary
    and pills suffice, so the flow runs with FakeLlmProvider empty responses and
    no recorded LLM semantics.
  - `cassette`: needs free-text LLM interpretation (phrasing outside the
    extractor vocabulary, negated enforce, ambiguous intent). Replayed from a
    recorded cassette in CI, never live.
- `stable` (bool): `false` quarantines a scenario (report-only, non-blocking)
  while it is being tuned. Seeds ship `true`.
- `known_limitation` (bool): documents a behavior that is a known limitation
  today (for example, no in-conversation archetype pivot). Flipping it later is
  a deliberate corpus edit, not a failure.
- `target_ir` (object or null): the intended saved policy IR for the
  round-trip oracle.
  - `null` for non-authoring outcomes (`steered`, `infeasible`, `pack_cta`,
    `handoff_cta`, `rejected_422`) and for archetypes whose oracle is not the
    evidence round-trip (`archetype_run_command` -> RunCommandPolicy,
    `archetype_compound` -> member-wise oracle).
  - When present it MUST be an EXPLICIT triple (Section 0.3): the `trigger`
    block carries `host`, `event`, AND `matcher`, never relying on the
    `Trigger` dataclass defaults (`host=claude-code, event=PreToolUse,
    matcher=Bash`) which would silently canonicalize a missing field into a
    valid but WRONG triple. The validator rejects a `target_ir` missing any of
    the three.
  - The triple `(event, matcher_class_of(matcher), action)` MUST be in
    `LEGAL_COMBINATIONS`, and `policy_from_dict(target_ir)` MUST succeed. Both
    are enforced by the schema test. Notable illegal triples the corpus avoids:
    `Stop/*/block`, `Stop/*/ask`, `Stop/Bash/audit` (audit at Stop is
    wildcard-only), `PreToolUse/*/block` (block at PreToolUse needs
    tool/mcp_tool/tool_alt).
- `compound_gate_matcher` (string, optional): for a compound
  (`archetype_compound`) scenario whose saved policy is expanded MEMBER-WISE
  (Section 6.3) - so `target_ir` is null and the O1 round-trip oracle does not
  apply - this supplies the legal gated-tool matcher the scripted answerer
  uses to answer the compound `q_matcher`. Only valid when `target_ir` is null.
  The schema test rejects it alongside a non-null `target_ir`.
- `expected` (object):
  - `outcome` (enum): one of
    `saved, steered, infeasible, pack_cta, handoff_cta, rejected_422`.
    `steered` means an honest deterministic steer (downgrade notice, wizard
    steer, scripts-upload fallback); the oracle checks the wire marker, never
    prose.
  - `feasibility_code` (string or null): the exact wire `feasibility.code`
    expected, or `null` when none.
  - `max_turns` (int): the turn budget for L3 termination.
- `phrasings` (list, non-empty): one or more `{text, note}` objects. Each
  phrasing fans out as a separate parametrized L3 run sharing the target and
  expectations. `text` is the operator utterance; `note` records provenance.
- `provenance` (object): `{source, generated_by, reviewed}`. `source` records
  the lane or audit row (for example `lane1-grid`, `audit-S6`).

## Requires-row shapes (target_ir.requires)

`policy_from_dict` requires an explicit `kind` for non-step rows. Use:
- regex: `{"kind": "regex", "pattern": "..."}`
- llm_critic: `{"kind": "llm_critic", "criterion": "..."}`
- shacl: `{"kind": "shacl", "shape_ttl": "..."}`
- step: `{"step": "<verifier>", "verdict": "pass"}` (kind defaults to step)

Registered verifier steps: `citation_verify, privilege_scan, source_allowlist,
structured_output, prompt_injection_screen`.

## Cassette files

Authored cassettes live in `tests/qa_corpus/cassettes/<scenario_id>.json`.
They are hand-written JSON documents (generated_by: "authored") whose
`compiler` list maps sha256 message-digest keys to canned LLM response strings.
No live LLM is called in CI.

## Re-recording cassettes

To re-record after a legitimate compiler-prompt or flow change:

```
MAGI_CP_QA_RECORD=1 PYTHONPATH=src python3 -m pytest tests/test_qa_corpus_replay.py -k <id>
```

A stale cassette surfaces as a loud keyed miss with an actionable error
message, never a silent stale pass.  The key derivation is
sha256(canonical_JSON(nonce-normalised messages)), so changing the system
prompt text requires re-recording.  The `_make_fence_nonce()` monkeypatch in
`tests/conftest.py` (fixture `qa_nonce_counter`) pins the nonce to a
deterministic counter so authored cassettes remain stable across runs.

## QA report and baseline gate (PR-E)

### What the report contains

After every pytest run that includes `tests/test_qa_corpus_replay.py`, the
`conftest.py` session hook writes `.qa-report/summary.{json,md}` (gitignored).
The report includes:

- Pass rate by category, language, and engine.
- Dead-end (O3), loop (O4), and contradiction (O2/I2) counts.
- Per-oracle failure table with affected scenario ids.
- Drift vs the committed `tests/qa_corpus/baseline.json` snapshot:
  regressions (hard CI blocker), improvements, and new scenarios.

### How the baseline gate works

`tests/qa_corpus/baseline.json` is a committed snapshot:
```json
{
  "<scenario_id>": {
    "stable": true,
    "outcome": "saved",
    "passed": true,
    "oracle_fingerprint": "<16-char sha256 prefix>"
  },
  ...
}
```

`tests/test_qa_baseline.py::test_baseline_drift_gate` reads
`.qa-report/summary.json` (written by the conftest hook during the same
pytest session) and calls `check_drift(baseline, current)`.  If any
`stable=true` scenario that was passing in baseline is now failing, the
test hard-fails with a clear message identifying which scenario regressed
and which oracle fired.

Quarantined scenarios (`stable=false`) are xfail/report-only and do not
block CI.

### oracle_fingerprint

A 16-character sha256 prefix of the sorted set of failing oracle ids
(e.g. `["O3"]`).  An empty failure list (all oracles passed) produces a
stable fingerprint of `"4f53cda18c2baa0c"`.  A change in WHICH oracle
fails (not just pass/fail) is therefore visible as a fingerprint change in
the report's drift section.

### Regenerating the baseline

After a legitimate corpus change (new scenario, scenario fix, or intentional
behavior change), regenerate the baseline:

```
MAGI_CP_QA_UPDATE_BASELINE=1 PYTHONPATH=src python3 -m pytest \
    tests/test_qa_corpus_replay.py tests/test_qa_baseline.py -q
```

This runs the replay tests, writes `.qa-report/summary.json`, then writes a
new `tests/qa_corpus/baseline.json` from that summary (instead of asserting
against the old one).  Commit both the corpus change and the updated
baseline in the same PR.

The regeneration path mirrors the cassette re-record pattern
(`MAGI_CP_QA_RECORD=1`): an env var gates an explicit update, never a
silent drift.
