import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D72: first-time-visitor empty states across the dashboard.
 *
 * Source-grep tests (same convention as the other (console) page tests):
 * we lock the contract on what each zero-data branch renders without
 * spinning up RTL.
 *
 * Each surface is checked for:
 *   - EmptyState component mounted in the zero-data branch
 *   - the correct i18n key references (title + body + CTA)
 *   - the primary CTA href points to the expected next step
 *
 * The corresponding KO + EN copy lives in web/lib/i18n/dict.ts under
 * the `rules.empty.*`, `scripts.empty.*`, `ledger.empty.*`,
 * `hitl.empty.*`, `endpoints.empty.*`, `verify.empty.*`,
 * `overview.empty.cta`, `rules.welcome.*` namespaces. The drift gate in
 * dict.test.ts asserts KO+EN parity for every key.
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("D72: /rules Policies tab empty state", () => {
  // D82a: PoliciesTab + WelcomeBanner mount moved into the dedicated
  // PoliciesTab component file when Packs got promoted to its own tab.
  // The D72 invariants still live; their source-grep target shifted.
  const src = read("rules/_components/PoliciesTab.tsx")

  it("mounts the WelcomeBanner client component", () => {
    expect(src).toContain('from "./WelcomeBanner"')
    expect(src).toContain("WelcomeBanner")
  })

  it("renders the welcome banner only when there is nothing to act on", () => {
    // userPolicies.length === 0 AND every prebuilt is disabled.
    expect(src).toMatch(
      /showWelcome\s*=\s*\n?\s*!err\s*&&\s*userPolicies\.length\s*===\s*0\s*&&\s*prebuilt\.every\(\(p\)\s*=>\s*!p\.enabled\)/,
    )
  })

  it("EmptyState carries the D72 title + body + primary + secondary CTA", () => {
    expect(src).toContain("rules.empty.policies.title")
    expect(src).toContain("rules.empty.policies.body")
    expect(src).toContain("rules.empty.policies.cta.primary")
    expect(src).toContain("rules.empty.policies.cta.secondary")
  })

  it("primary CTA links to /policies/new, secondary to ?mode=conversational", () => {
    expect(src).toContain('href="/policies/new"')
    expect(src).toContain('href="/policies/new?mode=conversational"')
  })
})

describe("D72: WelcomeBanner client component", () => {
  const src = read("rules/_components/WelcomeBanner.tsx")

  it("is a client component", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("persists dismissal in localStorage under magi_cp.welcome_dismissed", () => {
    expect(src).toContain("magi_cp.welcome_dismissed")
    expect(src).toMatch(/localStorage\.setItem/)
    expect(src).toMatch(/localStorage\.getItem/)
  })

  it("links the CTA to the conversational policy builder", () => {
    expect(src).toContain('href="/policies/new?mode=conversational"')
  })

  it("uses the rules.welcome.* i18n keys", () => {
    expect(src).toContain("rules.welcome.title")
    expect(src).toContain("rules.welcome.body")
    expect(src).toContain("rules.welcome.cta")
    expect(src).toContain("rules.welcome.dismiss")
  })
})

describe("D72: /rules merged Evidence tab empty state (H1)", () => {
  const src = read("rules/_components/EvidenceTab.tsx")

  it("EmptyState carries the D72 title + body + CTA", () => {
    expect(src).toContain("rules.empty.checks.title")
    expect(src).toContain("rules.empty.checks.body")
    expect(src).toContain("rules.empty.checks.cta")
  })

  it("primary CTA links to /verifiers/new", () => {
    expect(src).toContain('href="/verifiers/new"')
  })
})

describe("D72: /scripts empty state", () => {
  const src = read("scripts/page.tsx")

  it("EmptyState mounted in the zero-data branch", () => {
    expect(src).toMatch(/scripts\.length === 0/)
    expect(src).toContain("EmptyState")
  })

  it("carries the D72 title + body + primary + secondary CTA", () => {
    expect(src).toContain("scripts.empty.title")
    expect(src).toContain("scripts.empty.body")
    expect(src).toContain("scripts.empty.cta.secondary")
  })

  it("primary CTA is the UploadScriptButton (file upload trigger)", () => {
    // The primary CTA in the empty state is the UploadScriptButton,
    // which calls fileRef.current?.click() under the hood. That keeps
    // the empty-state path consistent with the top-of-page button.
    expect(src).toContain("UploadScriptButton")
  })

  it("secondary CTA links to the guided wizard (no fictional hint param)", () => {
    // D72 follow-up: dropped the `hint=run_command` query param because
    // the wizard does not read it. Linking plainly to the guided wizard
    // keeps the URL honest about what the operator lands on.
    expect(src).toContain('href="/policies/new?mode=guided"')
    expect(src).not.toContain('hint=run_command')
  })
})

describe("D72: /ledger empty state", () => {
  const src = read("ledger/page.tsx")

  it("distinguishes 'no entries' vs 'filter empty' (regression guard)", () => {
    // The filter-empty branch keeps the existing ledger.filter.empty key.
    expect(src).toContain("ledger.filter.empty")
  })

  it("zero-data EmptyState carries the D72 title + body + CTA", () => {
    expect(src).toContain("ledger.empty.title")
    expect(src).toContain("ledger.empty.body")
    expect(src).toContain("ledger.empty.cta")
  })

  it("primary CTA links to /rules", () => {
    // The empty-state CTA points the operator at /rules so they can
    // enable a policy that will start producing entries here.
    expect(src).toMatch(/href="\/rules"/)
  })
})

describe("D72: /hitl empty state", () => {
  const src = read("hitl/page.tsx")

  it("EmptyState carries the D72 title + body", () => {
    expect(src).toContain("hitl.empty.title")
    expect(src).toContain("hitl.empty.body")
  })

  it("intentionally has no primary CTA (queue surface)", () => {
    // HITL is a queue; new items arrive when an Ask-a-human policy
    // fires. There is no operator-initiated action to take from this
    // page when it is empty. Assert via a parser-friendlier pattern:
    // the EmptyState tag must be self-closing and reference only the
    // hitl.empty title + body props, with no `action=` between them.
    // The previous 240-char window-slice was fragile to copy edits.
    expect(src).toMatch(
      /<EmptyState\s+title=\{t\("hitl\.empty\.title"\)\}\s+body=\{t\("hitl\.empty\.body"\)\}\s*\/>/,
    )
  })
})

describe("D72: /endpoints empty state", () => {
  const src = read("endpoints/page.tsx")

  it("EmptyState carries the D72 title + body + CTA via i18n keys", () => {
    expect(src).toContain("endpoints.empty.title")
    expect(src).toContain("endpoints.empty.body")
    expect(src).toContain("endpoints.empty.cta")
  })

  it("primary CTA links to /setup", () => {
    expect(src).toMatch(/href="\/setup"/)
  })
})

describe("D72: /verify empty state", () => {
  const src = read("verify/page.tsx")

  it("EmptyState mounted when no verifier is wired", () => {
    expect(src).toMatch(/wired\.length === 0/)
    expect(src).toContain("EmptyState")
  })

  it("carries the D72 title + body + CTA", () => {
    expect(src).toContain("verify.empty.title")
    expect(src).toContain("verify.empty.body")
    expect(src).toContain("verify.empty.cta")
  })

  it("primary CTA links to /rules?tab=checks (Checks tab)", () => {
    expect(src).toContain('href="/rules?tab=checks"')
  })
})

describe("D72: /overview empty-friendly CTA", () => {
  const src = read("overview/page.tsx")

  it("renders an EmptyState on fresh install (zero HITL + zero ledger)", () => {
    // D72 follow-up: when pending===0 AND ledgerEntries===0 the overview
    // page renders an EmptyState pointing at /rules instead of three
    // 0-valued KPI cards with no first-time-visitor framing.
    expect(src).toContain("overview.empty.title")
    expect(src).toContain("overview.empty.body")
    expect(src).toContain("overview.empty.ctaRules")
    expect(src).toMatch(/href="\/rules"/)
  })

  it("keeps the inline ledger link for non-empty installs", () => {
    expect(src).toMatch(/href="\/ledger"/)
    expect(src).toContain("overview.kpis.openLedger")
  })
})
