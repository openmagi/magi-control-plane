import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { encodeSeed, decodeSeed } from "./handoff-seed"

/**
 * D57g: source-level + behavioural invariants for the "Continue in
 * conversation" handoff seam. Same pattern as the sibling
 * ConversationalCompose tests: grep the rendered TSX for the
 * contract, plus exercise the seed encoder.
 */

const HERE = __dirname
function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("HandoffLink seed encode / decode", () => {
  it("round-trips an ASCII payload", () => {
    const original = {
      wizard_state: { lifecycle: "before_tool_use", toolScope: "Bash" },
      draft_ir: null,
      origin: "guided" as const,
    }
    const seed = encodeSeed(original)
    const decoded = decodeSeed(seed)
    expect(decoded).toEqual(original)
  })

  it("round-trips a Hangul payload (UTF-8 safe)", () => {
    const original = {
      wizard_state: {
        description: "외부 fetch 결과 감사",
        lifecycle: "after_tool_use",
      },
      draft_ir: null,
    }
    const seed = encodeSeed(original)
    const decoded = decodeSeed(seed)
    expect(decoded).toEqual(original)
  })

  it("returns null on a malformed base64", () => {
    expect(decodeSeed("!!!not-base64!!!")).toBeNull()
  })

  it("returns null on a base64 payload that decodes to non-JSON", () => {
    const garbage = Buffer.from("not json").toString("base64")
    expect(decodeSeed(garbage)).toBeNull()
  })

  it("returns null on a base64 payload that decodes to an array", () => {
    const arr = Buffer.from(JSON.stringify([1, 2, 3])).toString("base64")
    expect(decodeSeed(arr)).toBeNull()
  })
})

describe("HandoffLink source invariants", () => {
  const src = read("HandoffLink.tsx")

  it("declares 'use client'", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("renders a link to ?mode=conversational&seed=...", () => {
    expect(src).toContain("?mode=conversational&seed=")
  })

  it("uses translate(locale, ...) instead of accepting a closure", () => {
    // Per the project's hard rule: client components MUST NOT take a
    // t() closure. They take locale and rebuild via translate().
    expect(src).toContain("translate(locale,")
    // The component prop shape declares `locale`, not `t`.
    expect(src).toMatch(/locale:\s*"ko"\s*\|\s*"en"/)
  })

  it("does NOT import from the @/components/ui barrel", () => {
    // Sub-path imports only.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    expect(stripped).not.toMatch(/from\s+["']@\/components\/ui["']/)
  })

  it("explicitly enumerates the wizard URL keys (no smuggling)", () => {
    expect(src).toContain("WIZARD_URL_KEYS")
    // Sanity check on a sample subset of the canonical keys.
    expect(src).toContain('"lifecycle"')
    expect(src).toContain('"toolScope"')
    expect(src).toContain('"action"')
    expect(src).toContain('"id"')
  })
})

describe("ConversationalCompose seed handling", () => {
  const src = read("ConversationalCompose.tsx")

  it("accepts an initialSeed prop", () => {
    expect(src).toMatch(/initialSeed\??:/)
  })

  it("posts the decoded seed to /api/policies/handoff-context on mount", () => {
    expect(src).toContain("/api/policies/handoff-context")
    expect(src).toContain("decodeSeed")
    // The mount effect must mount the assistant turn so the chat
    // surface shows the seeded summary instead of the canned intro.
    // We grep for the `role: "assistant"` literal that the seed mount
    // writes; the strict "no closure-snapshot" rule enforced by
    // ConversationalCompose.test.ts requires functional setters so
    // we don't assert a specific `setHistory(...)` shape here.
    expect(src).toMatch(/role:\s*"assistant"/)
  })

  it("guards re-runs with a seedAppliedRef so a remount does not re-fire", () => {
    expect(src).toContain("seedAppliedRef")
  })
})
