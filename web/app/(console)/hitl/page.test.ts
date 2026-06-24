import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the HITL list page (issue #9 follow-on).
 * Matches the project convention used by SidebarClient.test.ts +
 * NavItem.test.ts: we grep the rendered JSX/JS for the contract rather
 * than spinning up React Testing Library. The runtime behaviour (label
 * fallback for legacy vs PR3+ rows) is implied by which t() keys + which
 * helper calls appear in the source.
 */
describe("HITL list page label fallback (PR3)", () => {
  const src = readFileSync(
    path.join(__dirname, "page.tsx"),
    "utf-8",
  )

  it("imports the PR3 display helpers from @/lib/cloud", () => {
    expect(src).toMatch(/displaySubject/)
    expect(src).toMatch(/displayPayloadHash/)
    expect(src).toMatch(/isLegacyHitlRow/)
  })

  it("routes column labels through t() (no hardcoded English literals)", () => {
    // The legacy-vs-canonical label flip lives in ItemCard. Both branches
    // must use t() so KO users do not see English column headers mixed
    // in (issue #6 follow-on, P2 list-page item).
    expect(src).toMatch(/t\(legacy \? "hitl\.col\.matter" : "hitl\.col\.subject"\)/)
    expect(src).toMatch(/t\(legacy \? "hitl\.col\.doc" : "hitl\.col\.payload"\)/)
    // And the raw English literals must not survive in the JSX side —
    // catches a refactor that re-introduced them.
    expect(src).not.toMatch(/subjLabel = legacy \? "matter" : "subject"/)
    expect(src).not.toMatch(/phashLabel = legacy \? "doc" : "payload"/)
  })

  it("hides the subject/matter span when both column values are null", () => {
    // `{subj && <span>…</span>}` — degenerate orphan rows render nothing
    // rather than "subject: " with an empty Code element.
    expect(src).toMatch(/\{subj && \(/)
    expect(src).toMatch(/\{phash && \(/)
  })
})

describe("HITL detail page label fallback (PR3)", () => {
  const src = readFileSync(
    path.join(__dirname, "[id]", "page.tsx"),
    "utf-8",
  )

  it("imports PR3 display helpers", () => {
    expect(src).toMatch(/displaySubject/)
    expect(src).toMatch(/displayPayloadHash/)
    expect(src).toMatch(/isLegacyHitlRow/)
  })

  it("uses i18n keys for column labels + legacy badge (issue #6)", () => {
    expect(src).toMatch(/hitl\.col\.matter/)
    expect(src).toMatch(/hitl\.col\.subject/)
    expect(src).toMatch(/hitl\.col\.doc/)
    expect(src).toMatch(/hitl\.col\.payload/)
    expect(src).toMatch(/hitl\.detail\.legacyBadge/)
  })

  it("picks the canonical-subject ledgerContext variant for PR3+ rows", () => {
    // Issue #6: ledgerContextSubject + ledgerHintSubject for PR3 rows;
    // ledgerContext + ledgerHint preserved for legacy rows.
    expect(src).toMatch(/hitl\.detail\.ledgerContextSubject/)
    expect(src).toMatch(/hitl\.detail\.ledgerHintSubject/)
    expect(src).toMatch(/hitl\.detail\.ledgerContext/)
    expect(src).toMatch(/hitl\.detail\.ledgerHint/)
  })

  it("hides the ledger-context heading when subject + matter are both missing", () => {
    // P2 follow-on: avoid `Ledger context for matter ` with a trailing
    // space + empty value on degenerate rows. The block guards on `subj`
    // being truthy.
    expect(src).toMatch(/if \(!subj\) return null/)
  })
})
