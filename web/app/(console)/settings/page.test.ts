import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Q97b — settings page source-level invariants.
 *
 * The page server-fetches the current LLM-keys status at SSR (so the
 * pills render with the right tone on first paint) and hands the
 * payload to the client form for the password inputs + status pills.
 * Keys are never returned to the client — only `{set, last4}`.
 */
describe("Settings page", () => {
  const src = readFileSync(path.join(__dirname, "page.tsx"), "utf-8")

  it("fetches the status payload via cloud.getLlmKeys at SSR", () => {
    expect(src).toMatch(/cloud\.getLlmKeys/)
  })

  it("hands the initial status to the client form", () => {
    expect(src).toMatch(/LlmKeysForm/)
    expect(src).toMatch(/initialStatus=\{initial\}/)
    expect(src).toMatch(/locale=\{locale\}/)
  })

  it("renders the page header with the i18n title + subtitle", () => {
    expect(src).toMatch(/PageHeader/)
    expect(src).toMatch(/settings\.title/)
    expect(src).toMatch(/settings\.subtitle/)
  })

  it("falls back to ErrorState when the cloud is unreachable", () => {
    expect(src).toMatch(/ErrorState/)
    expect(src).toMatch(/CloudConfigError/)
    expect(src).toMatch(/cloudUnreachable/)
  })

  it("is force-dynamic (status reflects every restart)", () => {
    expect(src).toMatch(/dynamic = "force-dynamic"/)
  })

  it("never imports the admin key directly (cloud client handles it)", () => {
    expect(src).not.toMatch(/MAGI_CP_ADMIN_API_KEY/)
  })
})
