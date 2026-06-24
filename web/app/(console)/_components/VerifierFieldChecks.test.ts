import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
// D57e P2 cleanup: lifecycleGroupsFor was re-exported through the
// component module in the original D57e patch. The re-export was a
// duplicated public-API surface and got dropped; tests now import
// from the canonical lib seam alongside the other helpers.
import {
  fieldChecksFlat,
  getVerifierDescriptor,
  allVerifierDescriptors,
  lifecycleGroupsFor,
} from "../../../lib/verifier-descriptors"

/**
 * D52d: VerifierFieldChecks is rendered in two surfaces (catalog
 * expander + wizard verifier picker). These source-level invariants
 * guard the contract that both surfaces depend on:
 *
 *   - the component is a shared React component (no per-surface fork)
 *   - it pulls the `field_checks` list straight off the descriptor
 *     mirror (no second translation layer to drift against)
 *   - the preview branch fires when there is no descriptor OR when
 *     the descriptor's field_checks is empty (custom verifier with no
 *     authored field_checks)
 *   - the tree uses semantic <dl> markup, not <div>+text
 */
describe("VerifierFieldChecks source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierFieldChecks.tsx"),
    "utf-8",
  )

  it("imports the descriptor mirror directly (single source of truth)", () => {
    expect(src).toContain("getVerifierDescriptor")
    expect(src).toContain("@/lib/verifier-descriptors")
  })

  it("renders the preview branch when the resolved field_checks groups are empty", () => {
    // D52d follow-up + D57e: the preview branch now triggers on
    // `groups === null` (no descriptor + no override, or every
    // resolved group is empty). `groups` resolves to the explicit
    // `fieldChecksOverride` when the caller passes one (for custom-
    // source catalog rows) and falls back to the descriptor mirror
    // otherwise. A non-empty override therefore renders the tree
    // even when getVerifierDescriptor returns null.
    expect(src).toMatch(/groups === null/)
    expect(src).toContain("verifier-field-checks-preview")
  })

  it("renders one <details> section per lifecycle group (D57e)", () => {
    // D57e: descriptors that ship more than one lifecycle group
    // render each under its own <details> so the operator can
    // collapse the ones that do not match their policy's lifecycle.
    expect(src).toContain("verifier-field-checks-group-")
    expect(src).toMatch(/<details/)
    expect(src).toMatch(/<summary/)
  })

  it("dims non-matching groups when a lifecycle is supplied (D57e)", () => {
    // The brief: "When the verifier is used inside a policy with a
    // specific lifecycle, the OTHER groups appear collapsed and
    // grayed-out; the current lifecycle's group is expanded by
    // default."
    expect(src).toMatch(/data-lifecycle-active/)
    expect(src).toMatch(/data-lifecycle-dimmed/)
    expect(src).toMatch(/opacity-60 grayscale/)
  })

  it("labels each group with a plain-language tooltip (D57e)", () => {
    // The brief: labeled with the CC event name + a plain-language
    // tooltip ("PreToolUse" = "Before any tool runs").
    //
    // D57e P2 (i18n): the tooltip strings now route through t() so
    // ko speakers see the same payload in Korean. We assert on the
    // i18n key prefix (the canonical seam) instead of the hardcoded
    // English copy — `dict.ts` is the source of truth for the en /
    // ko strings.
    expect(src).toMatch(/lifecycleTooltip/)
    expect(src).toContain("rules.verifier.fieldChecks.lifecycle.")
  })

  it("accepts a fieldChecksOverride prop so custom-source catalog rows render the tree", () => {
    // D52d follow-up: the prop is the seam the rules catalog uses to
    // hand author-supplied field_checks from EvidenceTypeEntry into
    // the shared component, replacing the prior placeholder render.
    expect(src).toMatch(/fieldChecksOverride\?:\s*FieldCheck\[\]/)
  })

  it("uses a semantic <dl> for the path -> description mapping", () => {
    expect(src).toMatch(/<dl/)
    expect(src).toMatch(/<dt/)
    expect(src).toMatch(/<dd/)
  })

  it("marks the visual tree glyphs as aria-hidden so SR reads data, not ASCII", () => {
    expect(src).toMatch(/aria-hidden/)
    expect(src).toContain("├─")
    expect(src).toContain("└─")
  })

  it("exposes showFooter so the wizard picker gets verdicts + emits inline", () => {
    expect(src).toMatch(/showFooter\?:\s*boolean/)
    expect(src).toContain("verifier-field-checks-footer")
  })

  it("the preview note is i18n-routed (not a hardcoded English string)", () => {
    expect(src).toMatch(/rules\.verifier\.fieldChecks\.preview/)
  })
})

/**
 * Data-shape parity check vs the mirror. Renders correctness depends
 * on the descriptor row being well-formed; both the catalog expander
 * and the wizard picker would mis-render if the mirror drifted.
 */
describe("VerifierFieldChecks data parity vs the descriptor mirror", () => {
  it("every built-in descriptor exposes a non-empty field_checks dict (D57e)", () => {
    // D57e: field_checks is grouped by lifecycle. Every built-in
    // must declare at least one group; every group must carry at
    // least one row with a non-empty path + description.
    for (const d of allVerifierDescriptors()) {
      const groups = d.field_checks ?? {}
      expect(
        Object.keys(groups).length,
        `field_checks dict empty on ${d.step}`,
      ).toBeGreaterThan(0)
      for (const fc of fieldChecksFlat(d)) {
        expect(fc.path, `empty path on ${d.step}`).toBeTruthy()
        expect(fc.check_description, `empty desc on ${d.step}`).toBeTruthy()
        expect(fc.check_description.length).toBeLessThanOrEqual(200)
      }
    }
  })

  it("citation_verify field_checks mirror the verifier's own input dict (caller-assembled)", () => {
    // D52d follow-up: citation_verify is a caller-assembled verifier
    // (its `run()` reads `citations` + `corpus_override` from the
    // posted dict, NOT a CC stdin path). The catalog row therefore
    // documents the verifier's input contract, not CC stdin paths.
    //
    // D57e: rows live under the Stop lifecycle group.
    const d = getVerifierDescriptor("citation_verify")
    expect(d).not.toBeNull()
    expect(lifecycleGroupsFor(d!)).toEqual(["Stop"])
    const paths = (d!.field_checks!.Stop ?? []).map((f) => f.path)
    expect(paths).toContain("citations[].quote")
    expect(paths).toContain("citations[].ref")
    expect(paths).toContain("corpus_override")
  })

  it("source_allowlist field_check description names allowlist semantics", () => {
    const d = getVerifierDescriptor("source_allowlist")
    expect(d).not.toBeNull()
    // D57e: source_allowlist is PreToolUse-only.
    expect(lifecycleGroupsFor(d!)).toEqual(["PreToolUse"])
    const fcs = d!.field_checks!.PreToolUse ?? []
    expect(fcs.length).toBeGreaterThan(0)
    const blob = fcs.map((f) => `${f.path} ${f.check_description}`).join("\n")
    expect(blob).toMatch(/allowlist/i)
  })

  it("getVerifierDescriptor returns null for unknown step (preview branch fallback)", () => {
    expect(getVerifierDescriptor("does_not_exist")).toBeNull()
  })
})

/* ─── D64: friendly display labels per row ────────────────────── */
describe("VerifierFieldChecks D64 display-label invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "VerifierFieldChecks.tsx"),
    "utf-8",
  )

  it("imports getDisplayLabel and threads locale into the Row renderer", () => {
    expect(src).toContain("getDisplayLabel")
    expect(src).toContain('from "@/lib/payload-schemas"')
    // VerifierFieldChecks accepts an optional locale prop; Row reads
    // it to resolve the friendly label per path.
    expect(src).toMatch(/locale\?:\s*import\("@\/lib\/i18n\/dict"\)\.Locale/)
  })

  it("Row renders friendly label as primary + raw path as muted secondary", () => {
    expect(src).toMatch(/friendly\s*=\s*getDisplayLabel\(path,\s*locale\)/)
    expect(src).toMatch(/isFriendly\s*=\s*friendly\s*!==\s*path/)
  })

  it("Row exposes data-field-path + data-display-label hooks", () => {
    expect(src).toMatch(/data-field-path=\{path\}/)
    expect(src).toMatch(/data-display-label=\{friendly\}/)
  })

  it("aria-label names BOTH the friendly label and the raw path", () => {
    // SR users still hear the literal field path (back-compat) AND
    // the friendly name. Brief: "raw path in title= tooltip + aria-
    // label". Both go into the listitem aria.
    expect(src).toMatch(/ariaLabel\s*=\s*`\$\{friendly\}/)
  })
})
