import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D74a follow-up: source-level invariants for the PoliciesTab server
 * component.
 *
 * The trigger render block was the entry point for the P0 client crash
 * "Cannot read properties of undefined (reading 'event')" — a future
 * refactor that re-introduces an unconditional `item.trigger.event`
 * access in the user-policies grid would resurface it. This file pins
 * the contract via source-grep (matching the sibling .test.ts pattern in
 * this directory: PrebuiltToggle / PackSection / VerifierExpander).
 *
 *   1. `item.trigger` is read defensively (ternary or optional chain) —
 *      no `.event` access without a presence guard.
 *   2. The cloud's `PolicyListItem.trigger` type stays optional in
 *      web/lib/cloud.ts so a non-EvidencePolicy row without trigger
 *      still type-checks.
 *
 * The runtime listing render is exercised end-to-end by scenario 06
 * (06-policy-pack-roundtrip) and by tests/test_policies_api.py
 * (test_list_policies_synthesizes_trigger_for_context_injection +
 * test_list_policies_handles_mixed_archetypes). This file is the
 * fast-feedback safety net the workflow brief asked for.
 */
describe("PoliciesTab source invariants (D74a)", () => {
  const src = readFileSync(
    path.join(__dirname, "PoliciesTab.tsx"),
    "utf-8",
  )

  it("does NOT carry a 'use client' pragma (server component)", () => {
    expect(src.startsWith('"use client"')).toBe(false)
  })

  it("guards every trigger access with a presence check", () => {
    // The crash class is `item.trigger.event` evaluated when trigger is
    // undefined. Pin the rendered ternary so a future copy-paste of a
    // new trigger row template cannot silently re-introduce it.
    expect(src).toMatch(/item\.trigger\s*\?/)
    // Negative: no unconditional `item.trigger.event` access anywhere
    // in the file. (The only legal reference inside the ternary's true
    // branch carries the surrounding `item.trigger ?` guard.)
    const unguarded = src
      // Strip the ternary's true branch so the bare reference inside
      // it is excluded from the negative scan.
      .replace(/item\.trigger\s*\?[\s\S]*?:\s*null/g, "")
    expect(unguarded).not.toMatch(/item\.trigger\.event/)
    expect(unguarded).not.toMatch(/item\.trigger\.matcher/)
  })

  it("renders both event + matcher in the trigger span", () => {
    // The render keeps the surface a single archetype-agnostic span so
    // ContextInjectionPolicy (event+matcher synthesized by the cloud
    // serializer) and EvidencePolicy / RunCommandPolicy (real trigger
    // triple) share one operator-visible cue.
    expect(src).toContain("item.trigger.event")
    expect(src).toContain("item.trigger.matcher")
  })

  it("uses the t('policies.trigger') i18n key (no hard-coded string)", () => {
    expect(src).toContain('t("policies.trigger")')
  })

  it("filters materialized prebuilts out of the user-policies grid", () => {
    // D60 follow-up: enabled prebuilts render in the prebuilt section,
    // not in the user-policies grid. Pin so a future refactor doesn't
    // accidentally double-render them.
    expect(src).toMatch(/filter\(\(p\)\s*=>\s*!p\.id\.startsWith\("prebuilt\/"\)\)/)
  })
})

describe("PolicyListItem.trigger is optional in the cloud client (D74a)", () => {
  const cloudSrc = readFileSync(
    path.join(__dirname, "..", "..", "..", "..", "lib", "cloud.ts"),
    "utf-8",
  )

  it("declares `trigger?:` (optional) on PolicyListItem", () => {
    // The ContextInjectionPolicy + RunCommandPolicy P0 crash root cause
    // was reading trigger as required. SubagentPolicy + McpGatingPolicy
    // legitimately have no event scope (and so no trigger), and legacy
    // unstamped rows from before D74a may also omit it; the optional
    // marker is what lets the listing tolerate both cases.
    expect(cloudSrc).toMatch(
      /export type PolicyListItem[\s\S]*?trigger\?:\s*\{[\s\S]*?event:\s*string;\s*matcher:\s*string\s*\}/,
    )
  })
})
