import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D75: source-level invariants for the PackSection server component
 * + PackToggle client component.
 *
 * The Pack section renders ABOVE the Prebuilt section so the
 * intent-level controls land first. Each card carries: name,
 * description, member-count, status badge, and a single toggle that
 * cascades to every member via the cloud's enable / disable handler.
 *
 * Three property classes the source-grep pins:
 *
 *   1. The section file pulls in PackToggle + togglePackAction and
 *      uses the `rules.pack.section.title` i18n key.
 *   2. PackToggle posts via a hidden form (same shape as
 *      PrebuiltToggle / PolicyToggle); the cloud verb is decided by
 *      the boolean carried in `enabled`.
 *   3. The New-pack page wires the createPackAction handler with the
 *      multi-select policy_ids checkbox group.
 */
describe("PackSection source invariants (D75)", () => {
  const sectionSrc = readFileSync(
    path.join(__dirname, "PackSection.tsx"),
    "utf-8",
  )

  it("imports PackToggle + togglePackAction", () => {
    expect(sectionSrc).toContain("PackToggle")
    expect(sectionSrc).toContain("togglePackAction")
  })

  it("uses the rules.pack.section.title i18n key", () => {
    expect(sectionSrc).toContain('"rules.pack.section.title"')
  })

  it("renders status badges for all/partial/none", () => {
    expect(sectionSrc).toContain('"rules.pack.status.all"')
    expect(sectionSrc).toContain('"rules.pack.status.partial"')
    expect(sectionSrc).toContain('"rules.pack.status.none"')
  })

  it("renders the New pack CTA pointing at /policy-packs/new", () => {
    expect(sectionSrc).toContain('href="/policy-packs/new"')
    expect(sectionSrc).toContain('"packs.new.cta"')
  })

  it("renders an expander for member ids", () => {
    expect(sectionSrc).toContain('"rules.pack.expand.toggle"')
    expect(sectionSrc).toMatch(/policy_ids\.map/)
  })
})

describe("PackToggle source invariants (D75)", () => {
  const toggleSrc = readFileSync(
    path.join(__dirname, "PackToggle.tsx"),
    "utf-8",
  )

  it("declares 'use client'", () => {
    expect(toggleSrc.startsWith('"use client"')).toBe(true)
  })

  it("renders a role=switch toggle with aria-checked", () => {
    expect(toggleSrc).toContain('role="switch"')
    expect(toggleSrc).toContain("aria-checked={checked}")
  })

  it("status=all maps to checked=true; partial/none map to false", () => {
    // The check sentence is `const checkedNow = status === "all"`.
    expect(toggleSrc).toContain('status === "all"')
  })

  it("posts via a hidden form (id, enabled) to the server action", () => {
    expect(toggleSrc).toContain('name="id"')
    expect(toggleSrc).toContain('name="enabled"')
    // No window.fetch — the server action handles the cloud call.
    expect(toggleSrc).not.toContain("fetch(")
  })
})

describe("New pack page source invariants (D75)", () => {
  const newSrc = readFileSync(
    path.join(
      __dirname,
      "..",
      "..",
      "policy-packs",
      "new",
      "page.tsx",
    ),
    "utf-8",
  )

  it("uses the createPackAction handler", () => {
    expect(newSrc).toContain("createPackAction")
  })

  it("renders a multi-select policy_ids checkbox group", () => {
    expect(newSrc).toContain('name="policy_ids"')
    expect(newSrc).toContain('type="checkbox"')
  })

  it("uses the packs.new.title + packs.new.save i18n keys", () => {
    expect(newSrc).toContain('"packs.new.title"')
    expect(newSrc).toContain('"packs.new.save"')
  })
})
