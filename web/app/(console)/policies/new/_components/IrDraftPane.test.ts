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
