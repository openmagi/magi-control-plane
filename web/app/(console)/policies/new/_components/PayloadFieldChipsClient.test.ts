import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D64: source-level invariants for the chip renderer's friendly
 * display-label contract. Same grep-the-rendered-TSX pattern as the
 * surrounding *.test.ts files (VerifierExpander, HandoffLink etc).
 *
 *   - Primary chip text comes from `display_label_ko` / `display_label_en`,
 *     locale-resolved.
 *   - Raw path stays in title= + aria-label so screen reader + hover
 *     users still hear / see the literal field path.
 *   - Click-to-insert STAYS raw path (operators authoring regex / shacl
 *     need the real predicate the runtime materializes).
 *   - UNKNOWN paths fall back to the raw path verbatim.
 */
describe("PayloadFieldChipsClient — D64 friendly label invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "PayloadFieldChipsClient.tsx"),
    "utf-8",
  )

  it("ChipField type carries display_label_ko + display_label_en", () => {
    expect(src).toMatch(/display_label_ko\?\:\s*string/)
    expect(src).toMatch(/display_label_en\?\:\s*string/)
  })

  it("resolves the friendly label by locale at render time", () => {
    // locale ko → display_label_ko, locale en → display_label_en
    expect(src).toMatch(/display_label_ko/)
    expect(src).toMatch(/display_label_en/)
    // Both branches read off the chip directly (not via t())
    expect(src).toMatch(/locale === "ko".*display_label_ko/s)
  })

  it("falls back to raw path when no friendly label is registered", () => {
    // The `?? f.path` chain — operator-typed MCP paths must surface
    // verbatim instead of inventing a friendly name.
    expect(src).toMatch(/\?\?\s*f\.path/)
  })

  it("primary chip text reads the resolved friendly label, not raw path", () => {
    // The chip span used to be `<span>{f.path}</span>` (raw + mono);
    // D64 swaps that to render `{friendly}` so the visible text changes
    // when display_label_* is present.
    expect(src).toMatch(/\{friendly\}/)
  })

  it("keeps the raw path in the title attribute (hover)", () => {
    expect(src).toMatch(/title=\{title\}/)
    expect(src).toMatch(/f\.path/)
  })

  it("aria-label names BOTH the friendly label and the raw path", () => {
    // Brief: SR users still hear the literal field path. The aria-label
    // template includes both names so the keyboard + SR pass.
    expect(src).toMatch(/aria-label=\{aria\}/)
    expect(src).toMatch(/\$\{friendly\}.*\$\{f\.path\}/s)
  })

  it("click-to-insert routes through the raw path (NOT the friendly label)", () => {
    // The onChipActivate callback inserts `f.path` for variant=path
    // and the SHACL stub builder substitutes the raw path into the
    // turtle stub. Either way the friendly label NEVER gets inserted.
    expect(src).toMatch(/insertion = variant === "shacl-stub"\s*\?\s*buildShaclStub\(f\)\s*:\s*f\.path/)
  })

  it("exposes data-field-path on the chip for tests / CSS hooks", () => {
    expect(src).toMatch(/data-field-path=\{f\.path\}/)
  })

  it("exposes data-display-label on the chip with the resolved friendly text", () => {
    expect(src).toMatch(/data-display-label=\{friendly\}/)
  })

  it("renders an sr-only span carrying the raw path when friendly differs", () => {
    expect(src).toMatch(/sr-only/)
  })
})
