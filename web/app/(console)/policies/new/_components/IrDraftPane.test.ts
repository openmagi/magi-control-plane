import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D66: IrDraftPane must render the run_command summary verbatim from a
 * seeded draft. The D63 review (P1) already widened the pane to render
 * runtime / command body / args / timeout / fail_closed, so the only
 * D66-shaped invariant left is that a seeded draft (the shape the
 * `/api/policies/handoff-context` route returns after the wizard hands
 * off mid-flight) flows through unchanged.
 *
 * Source-level grep tests so the file can run under the dashboard's
 * vitest config without next/react render plumbing. See sibling
 * AdvancedAuthoring.test.ts / RunCommandForm.test.ts for the same
 * pattern.
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("IrDraftPane run_command rendering invariants", () => {
  const src = read("IrDraftPane.tsx")

  it("declares 'use client'", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("widens ActionArchetype to include run_command", () => {
    // The D63 review (P1) widened the union so a wizard-handed-off or
    // conversational-compose-emitted run_command IR renders its
    // body in the right column.
    expect(src).toMatch(/"run_command"/)
    expect(src).toMatch(/ActionArchetype[\s\S]+run_command/)
  })

  it("dispatches actionFromDraft on the `type: \"run_command\"` discriminator", () => {
    // The persisted shape uses `type: "run_command"` (sibling-archetype
    // dispatcher convention), so the pane must read the discriminator
    // rather than only `action`.
    expect(src).toContain('t === "run_command"')
  })

  it("renders the run_command body block when action is run_command", () => {
    // The brief's IrDraftPane test: a seeded draft mounted as the
    // first turn must surface the run_command fields the same way it
    // would after the operator typed them. We grep for the testids
    // the pane exposes.
    expect(src).toContain('data-testid="ir-draft-run-command-runtime"')
    expect(src).toContain('data-testid="ir-draft-run-command-body"')
    expect(src).toContain('data-testid="ir-draft-run-command-args"')
    expect(src).toContain('data-testid="ir-draft-run-command-timeout"')
    expect(src).toContain('data-testid="ir-draft-run-command-fail-closed"')
  })

  it("reads command / script_path / runtime / args / timeout_ms / fail_closed off the draft", () => {
    // The persisted RunCommandPolicy shape carries these keys directly
    // on the top-level IR object (RunCommandDraftPersist in page.tsx).
    // The seeded draft from the handoff endpoint uses the SAME shape
    // (the backend serializer reuses the IR-internal field names).
    // The pane reads each defensively because the draft may be
    // mid-merge. The readers either string-key the lookup
    // (`readRunCommandField(draft, "runtime")`) or property-access
    // (`(d).args`); we grep both styles.
    expect(src).toContain('"runtime"')
    expect(src).toContain('"command"')
    expect(src).toContain('"script_path"')
    expect(src).toMatch(/\)\.args\b/)
    expect(src).toMatch(/\)\.timeout_ms\b/)
    expect(src).toMatch(/\)\.fail_closed\b/)
  })

  it("never imports from the @/components/ui barrel (sub-path imports only)", () => {
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    expect(stripped).not.toMatch(/from\s+["']@\/components\/ui["']/)
  })

  it("takes locale: 'ko' | 'en' (no t() closure per the project hard rule)", () => {
    // The pane is a server component but it accepts a t closure
    // because it lives inside a server-rendered tree. Per the project
    // rule, CLIENT components must rebuild via translate(locale, ...);
    // the pane itself is rendered above a "use client" boundary and
    // receives both a t closure AND a locale (so child client islands
    // can rebuild). We assert on the locale prop's presence so the
    // run_command labels (KO/EN) can vary deterministically.
    expect(src).toMatch(/locale:\s*"ko"\s*\|\s*"en"/)
  })

  it("renders a `runs shell` warning pill so a seeded run_command policy is visibly flagged", () => {
    // A seeded run_command draft must surface that the rule will
    // execute a shell command. The warning pill is the affordance.
    expect(src).toContain('data-testid="ir-draft-action-warning"')
  })

  it("hides the run_command body when the action is not run_command (defense in depth)", () => {
    // The render guards on `action === "run_command"` so a verifier-
    // archetype draft does not accidentally render the body block.
    expect(src).toMatch(/action === "run_command"/)
  })
})

/* ── Q102 invariants ────────────────────────────────────────────────── */

describe("Q102: Live draft missing summary + Save this policy prominence", () => {
  const src = read("IrDraftPane.tsx")
  const dictPath = path.join(
    HERE, "..", "..", "..", "..", "..", "lib", "i18n", "dict.ts",
  )
  const dict = readFileSync(dictPath, "utf-8")

  it("declares the missingFields prop on the public Props interface", () => {
    // The IrDraftPane must accept a missing-field set from the
    // conversational compose state so its named placeholders and
    // bottom "still missing" line can track the server's view.
    expect(src).toMatch(/missingFields\?:\s*readonly\s+string\[\]/)
  })

  it("always renders the status pill (drafting/ready), not just on the ready branch", () => {
    // The legacy code rendered the READY pill behind a `readyToSave &&`
    // guard so the operator had no at-a-glance status while drafting.
    // Q102 promotes it to a constant pill with the data-state
    // discriminator the test pins below.
    expect(src).toContain('data-testid="ir-draft-status-pill"')
    expect(src).toMatch(/data-state=\{readyToSave\s*\?\s*"ready"\s*:\s*"drafting"\}/)
  })

  it("maps the status pill to amber (drafting) and emerald (ready)", () => {
    // Status pill colour pin per the brief: amber while drafting,
    // emerald once the server flips ready_to_save.
    expect(src).toMatch(/bg-amber-100/)
    expect(src).toMatch(/text-amber-900/)
    expect(src).toMatch(/bg-emerald-100/)
    expect(src).toMatch(/text-emerald-800/)
  })

  it("references the statusDrafting / statusReady i18n keys", () => {
    // The pill's visible label routes through translate(...) so KO/EN
    // copy stays in dict.ts and the test pins both keys exist there.
    expect(src).toContain('"newPolicy.conv.liveDraft.statusDrafting"')
    expect(src).toContain('"newPolicy.conv.liveDraft.statusReady"')
    expect(dict).toContain('"newPolicy.conv.liveDraft.statusDrafting"')
    expect(dict).toContain('"newPolicy.conv.liveDraft.statusReady"')
  })

  it("names every canonical missing field via a dedicated i18n key", () => {
    // The pane must NAME each missing field (not surface raw IR
    // tokens). One key per FieldName in the server's
    // _missing_fields_for_draft helper, pinned both in source (so the
    // mapping switch can't lose a branch) and in dict.ts (so the KO+EN
    // copy stays in step).
    const FIELDS = [
      "lifecycle", "matcher", "requires", "requires_body",
      "on_missing", "id",
    ]
    for (const f of FIELDS) {
      const key = `"newPolicy.conv.liveDraft.missing.${f}"`
      expect(src).toContain(key)
      expect(dict).toContain(key)
    }
  })

  it("renders a named-missing placeholder helper instead of legacy stubs", () => {
    // The whenLabel / conditionLabel / actionLabel branches replace
    // the empty "Waiting for an AI judge criterion" / "(not chosen
    // yet)" placeholders with namedMissingPlaceholder(field, t) so the
    // copy NAMES what's missing.
    expect(src).toMatch(/namedMissingPlaceholder\("lifecycle"/)
    expect(src).toMatch(/namedMissingPlaceholder\("matcher"/)
    expect(src).toMatch(/namedMissingPlaceholder\("requires"/)
    expect(src).toMatch(/namedMissingPlaceholder\("requires_body"/)
    expect(src).toMatch(/namedMissingPlaceholder\("on_missing"/)
    // The placeholder format uses {name} substitution.
    expect(src).toContain('"newPolicy.conv.liveDraft.placeholderMissing"')
    expect(dict).toContain('"newPolicy.conv.liveDraft.placeholderMissing"')
  })

  it("drops the legacy 'Waiting for an AI judge criterion' stub", () => {
    // The brief specifically names this legacy placeholder as the one
    // being replaced. Confirm it is gone so a future refactor doesn't
    // silently bring it back.
    expect(src).not.toContain("Waiting for an AI judge criterion")
    expect(src).not.toContain("AI 판단 기준 입력 대기 중")
  })

  it("renders the bottom 'this항목이 비어 있어요' footer when missing fields remain", () => {
    // The quiet footer surfaces the full missing-field list under
    // the card whenever the draft is not yet ready_to_save. Routes
    // through `newPolicy.conv.liveDraft.missingList` and joins names
    // via missingFieldLabel(...).
    expect(src).toContain('data-testid="ir-draft-missing-list"')
    expect(src).toContain('"newPolicy.conv.liveDraft.missingList"')
    expect(src).toMatch(/!readyToSave\s*&&\s*knownMissing\.length\s*>\s*0/)
    expect(dict).toContain('"newPolicy.conv.liveDraft.missingList"')
    // KO + EN copy must NAME the missing items per the brief.
    expect(dict).toContain('이 항목이 비어 있어요')
    expect(dict).toContain('Still missing:')
  })

  it("gates the Save CTA prominence (size=lg + motion-safe pulse) on ready", () => {
    // Save CTA gets size="lg" (vs prior "md") and a subtle pulse
    // animation that respects prefers-reduced-motion. The brand purple
    // remains via variant="primary" (--color-accent = #7C3AED).
    expect(src).toMatch(/size="lg"/)
    expect(src).toMatch(/motion-safe:animate-pulse/)
    expect(src).toMatch(/data-testid="ir-draft-save"/)
    expect(src).toMatch(/variant="primary"/)
  })

  it("uses locale: 'ko' | 'en' (no t closure forced into the i18n contract)", () => {
    // The hard rule from the brief: locale prop, not a t closure
    // baked at the boundary. The pane DOES take a t closure (per the
    // prior contract test above) but it ALSO takes a locale so child
    // surfaces and Q102's status pill can branch.
    expect(src).toMatch(/locale:\s*"ko"\s*\|\s*"en"/)
  })

  it("never em-dashes (project hard rule) in any new Q102 dict copy", () => {
    // KO live-draft copy must use commas/periods/parens, never an em
    // dash. We scope the assertion to the new key block so this test
    // doesn't drift against unrelated dict entries.
    const koBlock = dict.split('"newPolicy.conv.liveDraft.statusDrafting"')[1] ?? ""
    const koHead = koBlock.split('"newPolicy.conv.liveDraft.placeholderMissing"')[0] ?? ""
    expect(koHead).not.toContain("—")
  })
})

describe("IrDraftPane compound (evidence_gate) rendering", () => {
  const src = read("IrDraftPane.tsx")

  it("detects the evidence_gate discriminator", () => {
    expect(src).toContain('d.type === "evidence_gate"')
    expect(src).toContain("function isEvidenceGate")
  })

  it("reads gate.matcher / gate.action / project_scope for the summary", () => {
    expect(src).toContain("function evidenceGateTool")
    expect(src).toContain("function evidenceGateAction")
    expect(src).toContain("function evidenceGateScope")
  })

  it("renders a dedicated compound summary block with testids", () => {
    expect(src).toContain('data-testid="ir-draft-compound-when"')
    expect(src).toContain('data-testid="ir-draft-compound-condition"')
    expect(src).toContain('data-testid="ir-draft-compound-action"')
    expect(src).toContain('data-testid="ir-draft-compound-scope"')
  })

  it("branches the generic single-policy summary off the compound flag", () => {
    // The trigger/requires/action rows must NOT render for a compound.
    expect(src).toContain("hasDraft && !compound")
    expect(src).toContain("hasDraft && compound")
  })

  it("still serializes the full draft into ir_json for save", () => {
    // The compound draft (type/gate/audit/kind) must round-trip through
    // the hidden ir_json field so saveCompiled can route it.
    expect(src).toContain("JSON.stringify(draft, null, 2)")
    expect(src).toContain('name="ir_json"')
  })
})

describe("IrDraftPane policy-integrity review panel", () => {
  const src = read("IrDraftPane.tsx")

  it("accepts a review verdict + pending prop", () => {
    expect(src).toContain("review?:")
    expect(src).toContain("reviewPending")
  })

  it("renders the review panel above the Save CTA once ready", () => {
    expect(src).toContain('data-testid="ir-draft-review"')
    expect(src).toContain('data-testid="ir-draft-review-summary"')
    expect(src).toContain('data-testid="ir-draft-review-issues"')
    expect(src).toContain('data-testid="ir-draft-review-pending"')
  })

  it("colors the panel by verdict (emerald ok / amber needs-look)", () => {
    expect(src).toContain("emerald-50")
    expect(src).toContain("amber-50")
  })

  it("never gates Save on the review (panel is advisory, sibling of the form)", () => {
    // The Save form's render condition must not reference `review`.
    expect(src).toContain("readyToSave && draft && (")
  })
})

describe("F1/F2: review verdict localization + honest states", () => {
  const src = read("IrDraftPane.tsx")

  it("F1: localizes issues by stable code (not raw server message)", () => {
    expect(src).toContain("function localizeReviewIssue")
    expect(src).toContain('case "orphan_gate"')
    expect(src).toContain('case "action_intent_mismatch"')
    // semantic-source prose passes through (already in operator locale)
    expect(src).toMatch(/iss\.source === "semantic"[\s\S]{0,40}return iss\.message/)
  })

  it("F2: green 'implements your intent' only when the semantic layer ran", () => {
    expect(src).toContain('review.checked.includes("semantic")')
    expect(src).toContain("Structure checked")
  })

  it("F2: renders a neutral couldn't-check row on review error", () => {
    expect(src).toContain('data-testid="ir-draft-review-error"')
    expect(src).toContain("reviewError")
  })
})

describe("H3 (IF-13): dry-run disabled for compound drafts", () => {
  const src = read("IrDraftPane.tsx")

  it("gates the DryRunPanel off for an evidence_gate compound", () => {
    // The dry-run endpoint 422s a compound; pass ir=null + disabled.
    expect(src).toContain("readyToSave && draft && !compound")
    expect(src).toContain("disabled={!readyToSave || compound}")
  })
})
