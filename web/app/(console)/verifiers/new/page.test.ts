import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for /verifiers/new server page.
 *
 * The page parses + locally re-validates the JSON payload the client
 * island posts (cheap reject before the cloud hop). The cloud
 * re-validates again at custom_verifier_store.build_from_dict so a
 * hand-rolled client cannot bypass the dashboard's client checks.
 */
describe("verifiers/new page source invariants", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

  it("exports dynamic=force-dynamic", () => {
    expect(src).toMatch(/export const dynamic = "force-dynamic"/)
  })

  it("server action uses 'use server' pragma", () => {
    expect(src).toMatch(/"use server"/)
  })

  it("server action POSTs to /custom-verifiers", () => {
    expect(src).toContain("/custom-verifiers")
  })

  it("locks slug regex in line with backend", () => {
    expect(src).toMatch(/\/\^\[a-z\]\[a-z0-9_\]\*\$\//)
  })

  it("locks max lengths in line with backend", () => {
    expect(src).toMatch(/MAX_NAME_LEN\s*=\s*64/)
    expect(src).toMatch(/MAX_DESCRIPTION_LEN\s*=\s*500/)
  })

  it("guards body_type to preview only", () => {
    expect(src).toMatch(/body_type !== "preview"/)
  })

  it("forwards X-Api-Key (tenant scope), not admin key", () => {
    expect(src).toContain("X-Api-Key")
    expect(src).not.toContain("X-Admin-Api-Key")
  })

  it("redirects to /rules?tab=evidence&msg=verifier_created on success", () => {
    expect(src).toContain("/rules?tab=evidence&msg=verifier_created")
  })

  it("includes a back link to the verifiers tab", () => {
    expect(src).toContain('href="/rules?tab=evidence"')
  })

  it("renders form via the client island (server form, client state)", () => {
    expect(src).toContain("VerifierFormClient")
    expect(src).toMatch(/action=\{createVerifierAction\}/)
  })
})
