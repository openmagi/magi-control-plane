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
    // The onChipActivate callback inserts the raw path for variants
    // path / llm-marker (the marker wraps the same raw path in
    // curly braces) and shacl-stub substitutes the raw path into
    // the turtle stub. Either way the friendly label NEVER gets
    // inserted at the cursor.
    expect(src).toMatch(/insertion = f\.path/)
    expect(src).toMatch(/insertion = buildShaclStub\(f\)/)
  })

  it("D82c: variant='llm-marker' wraps path in curly braces", () => {
    // LLM critic textarea needs `{tool_response.output}` so the
    // runtime marker substitutor recognises the field AND so the
    // operator can SEE where the variable ends.
    expect(src).toMatch(/variant === "llm-marker"/)
    expect(src).toMatch(/insertion = `\{\$\{f\.path\}\}`/)
  })

  it("D82c: variant='regex-target' routes click to a separate <select>", () => {
    // Regex pattern textarea is left untouched (curly braces would
    // break the pattern); the chip sets the value of a separate
    // <select id={targetSelectId}> instead.
    expect(src).toMatch(/variant === "regex-target"/)
    expect(src).toMatch(/targetSelectId/)
    expect(src).toMatch(/HTMLSelectElement/)
    // Dispatches a change event so React form bindings pick up the
    // new value without a manual rebind.
    expect(src).toMatch(/new Event\("change", \{ bubbles: true \}\)/)
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

  it("D64 polish: no em-dash characters in the rendered source", () => {
    // Project no-em-dash hard rule (top AI-tell). Aria-label and title
    // strings flow into the DOM, so even a single em-dash in a template
    // literal is a runtime regression.
    expect(src).not.toMatch(/—/)
  })

  it("D82c fix: exports the Variant union for type-safe callers", () => {
    // The page-side InlineSubConfigPanel imports this union so its
    // chipVariant declaration cannot silently narrow and drop a
    // variant (the bug it ships covering: `'path' | 'shacl-stub'`
    // missed `'llm-marker'` and `'regex-target'`).
    expect(src).toMatch(/export type Variant/)
  })

  it("D82c fix: llm-marker variant rejects non-identifier paths", () => {
    // The runtime _MARKER_RX only accepts dotted identifiers; chip
    // paths containing `[]` / `-` etc fall back to raw-path
    // insertion (the marker substitutor would never resolve them).
    expect(src).toMatch(/_MARKER_PATH_RX/)
    expect(src).toMatch(/\.test\(f\.path\)/)
  })

  it("D82c fix: regex-target select-missing falls back to path insertion", () => {
    // When the targetSelectId doesn't resolve to an HTMLSelectElement
    // (mismatched id, mid-rerender, etc.), the chip still inserts
    // the raw path into the pattern textarea so the click does
    // something visible. Operators clicking and seeing nothing
    // happen would otherwise reasonably conclude the picker is broken.
    expect(src).toMatch(/console\.warn/)
    expect(src).toMatch(/falling back to path insertion/)
  })

  it("D64 polish: aria-label leads with the raw path before the friendly label", () => {
    // SR users authoring regex care about what is about to be inserted at
    // the caret (the raw path). Friendly label trails as the human cue.
    expect(src).toMatch(/aria\s*=\s*isFriendly\s*\?[\s\S]*?\$\{f\.path\}[\s\S]*?\$\{friendly\}/)
  })
})
