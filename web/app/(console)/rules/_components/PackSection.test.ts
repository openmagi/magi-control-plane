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

  // P4 (Codex runtime adapter): the per-pack coverage rollup.
  it("renders a PackCoverageRollup on each pack card when codex is enabled", () => {
    expect(sectionSrc).toContain("PackCoverageRollup")
    expect(sectionSrc).toContain("codexEnabled && <PackCoverageRollup")
    expect(sectionSrc).toContain("packCoverage")
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

/**
 * D75 follow-up: source-level invariants for the cascade-semantics
 * confirmation gate. PackToggle now mirrors PrebuiltToggle's
 * `setupRequired` / `enableAnyway` dialog so a one-click pack enable
 * does not silently land "Active" badges on inert prebuilt members.
 * PackSection wires the data through.
 */
describe("PackSection setup_required + stale wiring (D75 follow-up)", () => {
  const sectionSrc = readFileSync(
    path.join(__dirname, "PackSection.tsx"),
    "utf-8",
  )

  it("passes setup_required_members through to PackToggle", () => {
    expect(sectionSrc).toContain("setup_required_members")
    expect(sectionSrc).toContain("setupRequiredMembers")
  })

  it("renders setup_required + stale chips on the pack card", () => {
    expect(sectionSrc).toContain('"rules.pack.setupRequired.chip"')
    expect(sectionSrc).toContain('"rules.pack.stale.chip"')
  })

  it("inline member badges flag stale + needs-setup ids", () => {
    expect(sectionSrc).toContain('"rules.pack.stale.inlineBadge"')
    expect(sectionSrc).toContain('"rules.pack.setupRequired.inlineBadge"')
  })

  it("wires the partial-reach + setup_required dialog copy", () => {
    expect(sectionSrc).toContain('"rules.pack.setupRequired.title"')
    expect(sectionSrc).toContain('"rules.pack.setupRequired.body"')
    expect(sectionSrc).toContain('"rules.pack.partialReach.title"')
    expect(sectionSrc).toContain('"rules.pack.partialReach.body"')
  })

  it("uses the builtins-only hint instead of the misleading empty copy", () => {
    // The empty branch now only fires on cloud failure, with a
    // dedicated hint. The builtins-only hint surfaces under the grid
    // when the operator has no user packs of their own.
    expect(sectionSrc).toContain('"rules.pack.builtinsOnlyHint"')
    expect(sectionSrc).toContain('"rules.pack.section.cloudErrorHint"')
    // Old false "No packs" branch is gone (the body referenced 'No
    // policy packs yet' which is wrong when 5 built-ins always
    // exist).
    expect(sectionSrc).not.toContain('"rules.pack.empty.body"')
  })
})

describe("PackToggle confirm dialog (D75 follow-up)", () => {
  const toggleSrc = readFileSync(
    path.join(__dirname, "PackToggle.tsx"),
    "utf-8",
  )

  it("accepts a setupRequiredMembers prop", () => {
    expect(toggleSrc).toContain("setupRequiredMembers")
  })

  it("only intercepts the OFF -> ON transition for the confirm", () => {
    // The submit path bypasses the dialog when disabling (`nextEnabled
    // === false`). Source-grep pins the conditional.
    expect(toggleSrc).toContain("nextEnabled && confirmCopy")
  })

  it("renders an alertdialog when setup_required members exist", () => {
    expect(toggleSrc).toContain('role="alertdialog"')
    expect(toggleSrc).toContain('calloutKind === "setup"')
    expect(toggleSrc).toContain('calloutKind === "partial-reach"')
  })

  it("partial status triggers the cross-pack reach callout", () => {
    expect(toggleSrc).toMatch(/status === "partial"[\s\S]*partial-reach/)
  })
})

describe("togglePackAction partial-success routing (D75 follow-up)", () => {
  const actionsSrc = readFileSync(
    path.join(__dirname, "..", "actions.ts"),
    "utf-8",
  )

  it("inspects cascade results[] for per-member failures", () => {
    expect(actionsSrc).toContain("result.results.filter")
    expect(actionsSrc).toContain("r.ok === false")
  })

  it("routes to pack_partial_failure when nothing succeeded", () => {
    expect(actionsSrc).toContain("pack_partial_failure")
  })

  it("routes to pack_partial_success on mixed outcomes", () => {
    expect(actionsSrc).toContain("pack_partial_success")
  })

  it("logs failed member ids to server stderr for forensic trail", () => {
    expect(actionsSrc).toContain("console.error")
    expect(actionsSrc).toContain("failed members")
  })
})

describe("flash code coverage for pack flows (D75 follow-up)", () => {
  const flashSrc = readFileSync(
    path.join(__dirname, "..", "..", "..", "..", "lib", "flash.ts"),
    "utf-8",
  )

  it("OK_CODES carries pack_created so createPackAction surfaces a banner", () => {
    expect(flashSrc).toContain("pack_created:")
  })

  it("ERR_CODES carries name_required so empty form submits show a banner", () => {
    expect(flashSrc).toContain("name_required:")
  })

  it("carries pack_partial_failure + pack_partial_success codes", () => {
    expect(flashSrc).toContain("pack_partial_failure:")
    expect(flashSrc).toContain("pack_partial_success:")
  })
})
