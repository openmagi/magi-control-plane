import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the HITL list page.
 * Matches the project convention used by SidebarClient.test.ts +
 * NavItem.test.ts: we grep the rendered JSX/JS for the contract rather
 * than spinning up React Testing Library.
 *
 * PR4: legacy matter/doc display helpers + the "(legacy)" fallback path
 * are removed (the DB drop migration refuses to run with NULL-subject
 * rows, so the UI no longer needs a fallback branch).
 */
describe("HITL list page (PR4 canonical-only)", () => {
  const src = readFileSync(
    path.join(__dirname, "page.tsx"),
    "utf-8",
  )

  it("does not import the retired PR3 display helpers", () => {
    expect(src).not.toMatch(/displaySubject/)
    expect(src).not.toMatch(/displayPayloadHash/)
    expect(src).not.toMatch(/isLegacyHitlRow/)
  })

  it("uses canonical i18n keys for column labels", () => {
    expect(src).toMatch(/hitl\.col\.subject/)
    expect(src).toMatch(/hitl\.col\.payload/)
    // Legacy "matter" / "doc" labels are gone — a refactor that
    // re-introduces them would trip this guard.
    expect(src).not.toMatch(/hitl\.col\.matter/)
    expect(src).not.toMatch(/hitl\.col\.doc/)
  })

  it("hides the subject/payload span when the value is missing", () => {
    expect(src).toMatch(/\{subj && \(/)
    expect(src).toMatch(/\{phash && \(/)
  })
})

describe("HITL detail page (PR4 canonical-only)", () => {
  const src = readFileSync(
    path.join(__dirname, "[id]", "page.tsx"),
    "utf-8",
  )

  it("does not import the retired PR3 display helpers", () => {
    expect(src).not.toMatch(/displaySubject/)
    expect(src).not.toMatch(/displayPayloadHash/)
    expect(src).not.toMatch(/isLegacyHitlRow/)
  })

  it("uses canonical i18n keys for column labels", () => {
    expect(src).toMatch(/hitl\.col\.subject/)
    expect(src).toMatch(/hitl\.col\.payload/)
    expect(src).not.toMatch(/hitl\.col\.matter/)
    expect(src).not.toMatch(/hitl\.col\.doc/)
    // The "(legacy)" badge key is retired together with the legacy
    // branch in the renderer.
    expect(src).not.toMatch(/hitl\.detail\.legacyBadge/)
  })

  it("uses the canonical-subject ledgerContext variant only", () => {
    expect(src).toMatch(/hitl\.detail\.ledgerContextSubject/)
    expect(src).toMatch(/hitl\.detail\.ledgerHintSubject/)
    // Legacy (matter-bound) variants retired.
    expect(src).not.toMatch(/hitl\.detail\.ledgerContext"/)
    expect(src).not.toMatch(/hitl\.detail\.ledgerHint"/)
  })

  it("hides the ledger-context heading when subject is missing", () => {
    expect(src).toMatch(/if \(!subj\) return null/)
  })
})
