import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D57b: source-level invariants for ConvAuthoringGuide.
 *
 * Same pattern as the neighbouring ConversationalCompose / SteeringAware-
 * Field source-grep tests: assert the rendered TSX against the contract
 * that is most likely to silently regress in a future refactor.
 *
 * Concerns we lock here:
 *   - "use client" pragma (otherwise the localStorage persistence + the
 *     pill onClick handler regress to dead server-rendered markup).
 *   - Sub-path UI imports only (the @/components/ui barrel breaks the
 *     client bundle per the project's hard rules).
 *   - The three sections are present in the conversational tone
 *     (어떤 시점에서? / 어떤 조건? / 어떤 조치?), NOT the older
 *     admin-shape labels (WHEN / CONDITION / WHAT).
 *   - 5-6 starter pills exist (the brief calls out 5 examples).
 *   - Pill click writes through onFillPrompt (the parent owns the chat
 *     input state); the guide MUST NOT call setInput / dispatchEvent
 *     directly.
 *   - localStorage persistence keys the documented identifier.
 *   - The user-facing copy NEVER names internal vocab (regex / shacl /
 *     llm_critic / EvidenceReq / matcher / kind / lifecycle).
 *   - Wired into ConversationalCompose above the chat scroll region.
 *   - i18n keys live under the convGuide.* namespace in both KO + EN.
 */

const HERE = __dirname
const SRC = readFileSync(path.join(HERE, "ConvAuthoringGuide.tsx"), "utf-8")
const PARENT = readFileSync(
  path.join(HERE, "ConversationalCompose.tsx"),
  "utf-8",
)
const DICT = readFileSync(
  path.join(HERE, "..", "..", "..", "..", "..", "lib", "i18n", "dict.ts"),
  "utf-8",
)

describe("ConvAuthoringGuide source invariants", () => {
  it("declares 'use client' (it manages local state + localStorage)", () => {
    expect(SRC.startsWith('"use client"')).toBe(true)
  })

  it("never imports from the @/components/ui barrel (sub-path only)", () => {
    // The hard rule: client components must NOT pull the UI barrel
    // because it drags a server-only chain into the client bundle.
    // Strip comments first so the file-header docstring referencing
    // the rule does not false-positive.
    const stripped = SRC
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    expect(stripped).not.toMatch(/from\s+"@\/components\/ui"/)
    // It is fine to import from "@/components/ui/Foo" (sub-path).
  })

  it("never takes a t() closure as a prop (locale-only contract)", () => {
    // The project's hard rule: client components MUST NOT take a t()
    // closure from a server parent. They take `locale: "ko" | "en"`
    // and rebuild t() locally via translate().
    expect(SRC).toMatch(/locale:\s*"ko"\s*\|\s*"en"/)
    expect(SRC).toMatch(/translate\(locale,/)
  })

  it("renders three conversational-tone section titles", () => {
    // The three sections mirror the conversational lens, not the
    // admin-shape (WHEN / CONDITION / WHAT) labels of the deleted
    // D52e NlAuthoringGuide.
    expect(SRC).toContain("convGuide.section.when.title")
    expect(SRC).toContain("convGuide.section.condition.title")
    expect(SRC).toContain("convGuide.section.action.title")
  })

  it("does not surface admin-shape headings in the rendered tree", () => {
    // We grep only USER-FACING string literals; comments + i18n keys
    // are allowed to reference the older vocabulary because comments
    // never reach the chat surface.
    const stripped = SRC
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    // The TS string literals carrying "WHEN" / "CONDITION" / "WHAT"
    // as visible text would be quoted strings, not identifier props.
    expect(stripped).not.toMatch(/"WHEN"/)
    expect(stripped).not.toMatch(/"CONDITION"/)
    expect(stripped).not.toMatch(/"WHAT"/)
  })

  it("declares 5 to 6 starter pills", () => {
    const matches = SRC.match(/labelKey:\s*"convGuide\.pill\./g) ?? []
    expect(matches.length).toBeGreaterThanOrEqual(5)
    expect(matches.length).toBeLessThanOrEqual(6)
  })

  it("pill click writes through onFillPrompt (parent owns input state)", () => {
    // The guide MUST NOT touch the chat input directly. It calls the
    // parent's onFillPrompt(text) prop, which sets the controlled
    // textarea value via the parent's useState.
    expect(SRC).toMatch(/onFillPrompt:\s*\(text:\s*string\)\s*=>\s*void/)
    expect(SRC).toMatch(/onFillPrompt\(t\(/)
    // No direct DOM writes or controlled-input bypass tricks.
    expect(SRC).not.toMatch(/dispatchEvent\(/)
    expect(SRC).not.toMatch(/document\.getElementById\(/)
  })

  it("persists open/closed state under the documented localStorage key", () => {
    expect(SRC).toContain('"magi_cp.conv_authoring_guide.expanded"')
    expect(SRC).toMatch(/window\.localStorage\.getItem/)
    expect(SRC).toMatch(/window\.localStorage\.setItem/)
    expect(SRC).toMatch(/window\.localStorage\.removeItem/)
  })

  it("toggle button carries aria-expanded + aria-controls (a11y)", () => {
    expect(SRC).toMatch(/aria-expanded=/)
    expect(SRC).toMatch(/aria-controls=/)
  })

  it("renders collapsed by default (panel body gated on `expanded`)", () => {
    // The body is gated on `expanded &&` so first-render is the
    // closed shell + toggle pin only.
    expect(SRC).toMatch(/\{expanded\s*&&/)
  })

  it("never exposes internal vocab (regex/shacl/llm_critic/EvidenceReq) in user copy", () => {
    // The hard rule: NL / conversational UX never names internal
    // terms to end users. Comments are stripped first so the file-
    // header docstring referencing those tokens does NOT false-
    // positive (the user only sees rendered string literals).
    const stripped = SRC
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    const banned = [
      /"\s*[Rr]egex\b[^"]*"/,
      /"\s*[Ss]hacl\b[^"]*"/,
      /"\s*llm_critic\b[^"]*"/,
      /"\s*EvidenceReq\b[^"]*"/,
      /"\s*on_missing\b[^"]*"/,
      /"\s*matcher\b[^"]*"/,
    ]
    for (const re of banned) {
      const m = stripped.match(re)
      expect(
        m,
        `user-facing string literal exposes internal vocab: ${m?.[0] ?? ""}`,
      ).toBeNull()
    }
  })
})

describe("ConversationalCompose wires the guide above the chat", () => {
  it("imports ConvAuthoringGuide from the sibling module", () => {
    expect(PARENT).toMatch(/from\s+"\.\/ConvAuthoringGuide"/)
  })

  it("mounts <ConvAuthoringGuide> with locale + onFillPrompt + pending", () => {
    expect(PARENT).toContain("<ConvAuthoringGuide")
    expect(PARENT).toMatch(/onFillPrompt=\{/)
  })

  it("mounts the guide before the chat scroll region in the JSX tree", () => {
    // The brief: "Wire into ConversationalCompose above the chat scroll
    // region." We assert the guide tag appears before the role='log'
    // chat scroll in source order.
    const guideIdx = PARENT.indexOf("<ConvAuthoringGuide")
    const scrollIdx = PARENT.indexOf('role="log"')
    expect(guideIdx).toBeGreaterThan(-1)
    expect(scrollIdx).toBeGreaterThan(-1)
    expect(guideIdx).toBeLessThan(scrollIdx)
  })
})

describe("dict.ts carries the convGuide.* keys in both KO and EN", () => {
  // Smoke check that the required keys exist; the full KO/EN drift
  // gate lives in web/lib/i18n/dict.test.ts.
  const requiredKeys = [
    "convGuide.header.title",
    "convGuide.header.subtitle",
    "convGuide.header.pin",
    "convGuide.section.when.title",
    "convGuide.section.when.subtitle",
    "convGuide.section.condition.title",
    "convGuide.section.condition.subtitle",
    "convGuide.section.action.title",
    "convGuide.section.action.subtitle",
    "convGuide.action.footer",
    "convGuide.tryOne.title",
    "convGuide.pill.blockAwsKey.label",
    "convGuide.pill.blockAwsKey.fill",
    "convGuide.pill.askRmRf.label",
    "convGuide.pill.askRmRf.fill",
    "convGuide.pill.auditWebFetch.label",
    "convGuide.pill.auditWebFetch.fill",
    "convGuide.pill.flagWeakCitations.label",
    "convGuide.pill.flagWeakCitations.fill",
    "convGuide.pill.blockPiiAfterTool.label",
    "convGuide.pill.blockPiiAfterTool.fill",
  ]
  for (const key of requiredKeys) {
    it(`KO + EN define "${key}"`, () => {
      const occurrences = DICT.match(
        new RegExp(`"${key.replace(/\./g, "\\.")}"\\s*:`, "g"),
      ) ?? []
      // At least one in KO_RAW, one in EN.
      expect(occurrences.length).toBeGreaterThanOrEqual(2)
    })
  }
})
