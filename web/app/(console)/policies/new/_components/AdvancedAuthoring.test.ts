import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D57g hotfix: AdvancedAuthoring wraps PolicyBuilder + HandoffLink in a
 * single client tree so the HandoffLink can read the live raw-editor
 * draft at click time. Source-level invariants only — the actual draft
 * round-trip is exercised end-to-end via the page.tsx + cloud tests.
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("AdvancedAuthoring source invariants", () => {
  const src = read("AdvancedAuthoring.tsx")

  it("declares 'use client'", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("renders BOTH PolicyBuilder AND HandoffLink in the same tree", () => {
    expect(src).toContain("<PolicyBuilder")
    expect(src).toContain("<HandoffLink")
  })

  it("passes a getDraft callback to HandoffLink so the live draft is captured at click time", () => {
    expect(src).toMatch(/getDraft=\{getDraft\}/)
  })

  it("wires onDraftChange from PolicyBuilder to a ref-driven snapshot", () => {
    expect(src).toContain("onDraftChange={handleDraftChange}")
    expect(src).toContain("draftRef")
  })

  it("forwards origin='advanced' so the cloud picks the raw-editor framing", () => {
    expect(src).toMatch(/origin="advanced"/)
  })

  it("takes locale: 'ko' | 'en' (no t() closure per the project hard rule)", () => {
    expect(src).toMatch(/locale:\s*"ko"\s*\|\s*"en"/)
  })

  it("does NOT import from the @/components/ui barrel (server-only chain leak)", () => {
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    expect(stripped).not.toMatch(/from\s+["']@\/components\/ui["']/)
  })

  it("falls back to null when the draft is genuinely empty (so the empty-state intro renders)", () => {
    // Returning null from getDraft means the seed payload's `draft_ir`
    // is null, and the cloud serialiser will fall through to the
    // canned intro / empty-state question set. We assert the explicit
    // null branch is preserved.
    expect(src).toMatch(/if \(!cur\) return null/)
  })
})

describe("AdvancedAuthoring page.tsx wiring", () => {
  const pageSrc = readFileSync(
    path.join(HERE, "..", "page.tsx"),
    "utf-8",
  )

  it("page.tsx mounts AdvancedAuthoring inside the advanced mode branch", () => {
    expect(pageSrc).toContain("<AdvancedAuthoring")
  })

  it("page.tsx imports AdvancedAuthoring", () => {
    expect(pageSrc).toMatch(
      /import AdvancedAuthoring from\s+"\.\/_components\/AdvancedAuthoring"/,
    )
  })

  it("page.tsx no longer renders a HandoffLink WITHOUT getDraft in the AuthoringShell header", () => {
    // The previous regression rendered HandoffLink inside AuthoringShell
    // without `getDraft`, so the raw-editor draft was always dropped on
    // the floor. The fix moved the link into AdvancedAuthoring; the
    // dead branch is gone from page.tsx.
    expect(pageSrc).not.toMatch(/handoffOrigin === "advanced"/)
    // Defensive: ensure the AuthoringShell call site for advanced mode
    // passes `handoffOrigin="conversational"` (the suppression
    // sentinel), so a future contributor cannot silently re-introduce
    // the broken header link.
    expect(pageSrc).toMatch(/handoffOrigin="conversational"/)
  })
})

describe("D1: raw-JSON escape hatch", () => {
  const src = read("AdvancedAuthoring.tsx")

  it("renders a raw-JSON form posting draft_json to the save action", () => {
    expect(src).toContain('data-testid="advanced-raw-json-form"')
    expect(src).toContain('name="draft_json"')
    expect(src).toContain("action={saveAction}")
  })

  it("has a JSON textarea + save button", () => {
    expect(src).toContain('data-testid="advanced-raw-json-input"')
    expect(src).toContain('data-testid="advanced-raw-json-save"')
  })

  it("carries the source field like the evidence save path", () => {
    expect(src).toContain('name="source"')
  })
})
