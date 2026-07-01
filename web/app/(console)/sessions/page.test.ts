import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * P4 (pack-centric runtime): source-level invariants for the new
 * /sessions dashboard tab.
 *
 * The page is a server component with a form-driven server action, so
 * these are source-grep invariants in the sibling pattern of the rules
 * page.test.ts + PoliciesTab.test.ts. The runtime table render is
 * exercised end-to-end by the cloud's GET /admin/sessions test
 * (tests/test_admin_sessions_and_pack_membership.py).
 */
describe("sessions page source invariants (P4)", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

  it("reads its data from GET /admin/sessions via cloud.listAdminSessions", () => {
    expect(src).toContain("cloud.listAdminSessions")
  })

  it("renders the four required table columns", () => {
    // Column headers are i18n keys — pin each one so a future refactor
    // that drops a column fails loudly.
    expect(src).toContain('t("sessions.col.session")')
    expect(src).toContain('t("sessions.col.activePacks")')
    expect(src).toContain('t("sessions.col.lastActivity")')
    expect(src).toContain('t("sessions.col.floorPack")')
  })

  it("wires the force-deactivate action to the deactivate endpoint", () => {
    // The row action loops active packs and calls the cloud deactivate
    // helper, which owns the `/packs/deactivate` path (asserted in the
    // cloud lib test below).
    expect(src).toContain("cloud.deactivateSessionPack")
    expect(src).toMatch(/forceDeactivateAll/)
    // Must be a server action (mutating the cloud from a form submit).
    expect(src).toContain('"use server"')
  })

  it("renders the ALWAYS-ON chip for the floor pack column", () => {
    expect(src).toContain('t("packs.alwaysOn")')
  })

  // P4 (Codex runtime adapter): the Runtime column.
  it("renders a Runtime column with a human-readable runtime name", () => {
    expect(src).toContain('t("sessions.col.runtime")')
    expect(src).toContain("runtimeNameKey(item.runtime_id)")
  })
})

describe("cloud lib session helpers (P4)", () => {
  const cloudSrc = readFileSync(
    path.join(__dirname, "../../../lib/cloud.ts"), "utf-8",
  )

  it("listAdminSessions targets /admin/sessions with the admin key", () => {
    expect(cloudSrc).toContain("listAdminSessions")
    expect(cloudSrc).toContain("/admin/sessions")
  })

  it("deactivateSessionPack targets the session deactivate endpoint", () => {
    expect(cloudSrc).toContain("deactivateSessionPack")
    expect(cloudSrc).toContain("/packs/deactivate")
  })
})
