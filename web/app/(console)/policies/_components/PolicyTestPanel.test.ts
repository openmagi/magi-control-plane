import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D77: PolicyTestPanel client component invariants. Same pattern as
 * the DryRunPanel test — assert structure via grep so the component
 * stays narrow enough that a future refactor cannot silently drift
 * the contract.
 */
describe("PolicyTestPanel", () => {
  const src = readFileSync(
    path.join(__dirname, "PolicyTestPanel.tsx"),
    "utf-8",
  )

  it("is a client component", () => {
    expect(src).toMatch(/^\s*"use client"/m)
  })

  it("imports the synthetic template catalog from a sub-path", () => {
    expect(src).toContain('from "@/lib/synthetic-payloads"')
  })

  it("posts to the same-origin proxy (admin key stays off browser)", () => {
    expect(src).toContain('/api/policies/test')
    expect(src).toContain('"POST"')
  })

  it("supports both kind=policy and kind=pack", () => {
    expect(src).toMatch(/kind === "policy"|kind: "policy"/)
    expect(src).toMatch(/kind === "pack"|kind: "pack"/)
  })

  it("validates JSON inline + disables Run on parse error", () => {
    expect(src).toContain("JSON.parse")
    expect(src).toContain("payloadInvalid")
    expect(src).toContain("payloadParseError != null")
  })

  it("renders verdict + action pills + per-requires reasons", () => {
    expect(src).toContain("policy-test-verdict-pill")
    expect(src).toContain("policy-test-action-pill")
    expect(src).toContain("policy-test-reasons")
  })

  it("surfaces run_command would_run + input_rewrite new_tool_input collapsibles", () => {
    expect(src).toContain("policy-test-would-run")
    expect(src).toContain("policy-test-new-input")
  })

  it("uses sub-path i18n imports (no server-only barrel)", () => {
    expect(src).toContain('from "@/lib/i18n/dict"')
    expect(src).not.toContain('from "@/components/ui"')
  })
})
