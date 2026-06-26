import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Q97b — LlmKeysForm source-level invariants.
 *
 * The form holds the only user-typed inputs that carry raw provider
 * keys, so the invariants are stricter than a typical render check.
 * We assert via source grep so a future refactor cannot silently
 * leak a key into the rendered DOM, swap a password input for a
 * text input, or drop the server-action wiring.
 */
describe("LlmKeysForm source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "LlmKeysForm.tsx"),
    "utf-8",
  )

  it("declares itself a client component (server actions wire through props)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("uses password input type for both provider rows (never text)", () => {
    // Single source: a hand-rolled <input type="password" /> for each
    // row. The shared <Input> DS primitive defaults to type="text" and
    // we explicitly do NOT use it here.
    const passwordInputs = src.match(/type="password"/g) ?? []
    expect(passwordInputs.length).toBeGreaterThanOrEqual(1)
    // Defensive: the form must not contain a `type="text"` input.
    // (autoComplete="off" / spellCheck=false are safe attribute strings.)
    expect(src).not.toMatch(/type="text"/)
  })

  it("never renders the raw key value (no `value={...key}` on inputs)", () => {
    // The input is uncontrolled; placeholders carry "**** last4" only.
    expect(src).not.toMatch(/value=\{[^}]*api_key[^}]*\}/)
    expect(src).not.toMatch(/value=\{[^}]*\.last4[^}]*\}/)
    // The status payload is read for `set` + `last4` only; raw key
    // is never even typed by the cloud client (LlmKeysStatus shape).
    expect(src).toMatch(/placeholderSet/)
    expect(src).toMatch(/last4/)
  })

  it("wires the form to saveLlmKeysAction via <form action={onSave}>", () => {
    expect(src).toMatch(/saveLlmKeysAction/)
    expect(src).toMatch(/action=\{onSave\}/)
  })

  it("wires the Test connection button to testConnectionAction", () => {
    expect(src).toMatch(/testConnectionAction/)
    expect(src).toMatch(/onClick=\{onTest\}/)
  })

  it("classifies status pill from {set, last4} + last test result", () => {
    // 4-way mapping per the brief.
    expect(src).toMatch(/settings\.llm\.status\.notConfigured/)
    expect(src).toMatch(/settings\.llm\.status\.configured/)
    expect(src).toMatch(/settings\.llm\.status\.active/)
    expect(src).toMatch(/settings\.llm\.status\.failed/)
    // Emerald → active, amber → configured / notConfigured, red → failed.
    expect(src).toMatch(/"emerald"/)
    expect(src).toMatch(/"amber"/)
    expect(src).toMatch(/"red"/)
  })

  it("uses sub-path imports only (NEVER the @/components/ui barrel)", () => {
    // The barrel re-exports server-only NavBar etc. and would drag a
    // server-only import chain into the client bundle.
    expect(src).not.toMatch(/from "@\/components\/ui"/)
  })

  it("takes locale as a prop and resolves copy via translate (never getT)", () => {
    expect(src).toMatch(/locale: Locale/)
    expect(src).toMatch(/translate\(locale, /)
    expect(src).not.toMatch(/getT\(/)
  })

  it("renders one row per provider with explicit clear checkbox", () => {
    // Persistence contract: missing field preserves, empty string
    // clears. The UI surfaces a checkbox so a blank password input
    // does NOT accidentally clear an already-set key.
    expect(src).toMatch(/anthropic_clear/)
    expect(src).toMatch(/openai_clear/)
    expect(src).toMatch(/settings\.llm\.clearLabel/)
  })

  it("never imports raw env vars (admin key stays server-side)", () => {
    expect(src).not.toMatch(/process\.env\.MAGI_CP_ADMIN_API_KEY/)
    expect(src).not.toMatch(/process\.env\.MAGI_CP_API_KEY/)
  })
})
