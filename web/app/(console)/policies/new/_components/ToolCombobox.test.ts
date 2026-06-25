import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  CC_BUILTIN_TOOLS,
  CC_TOP_SUGGESTIONS,
} from "../../../../../lib/cc-tools"

/**
 * D70: source-level invariants for the Step 2 tool autocomplete combobox.
 *
 * The repo's vitest runs node-only without the React + Next path alias
 * chain, so behavioural assertions go through source grep (same pattern
 * as the surrounding *.test.ts files in this directory). The brief's
 * required behaviours are encoded below as TSX source pins:
 *
 *   - Empty input renders every built-in (CC_TOP_SUGGESTIONS surface).
 *   - Typing filters built-ins by case-insensitive substring (uses
 *     `filterCcBuiltins`).
 *   - A typed string with no built-in match exposes a "Use as custom
 *     tool name" affordance (the brief's MCP / custom path).
 *   - Enter on the highlighted row commits the value and closes.
 *   - ArrowUp / ArrowDown move the highlight; Tab / click-outside / Escape close.
 *   - Backspace on empty input fully clears the persisted value.
 *
 * Behavioural runtime coverage of the click-outside / keyboard chain
 * lives at the integration level (the wizard-wiring tests pin the
 * single hidden `toolScope_custom` input that advanceWizard reads).
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("ToolCombobox — Step 2 single autocomplete (D70)", () => {
  const src = read("ToolCombobox.tsx")

  it("is a client component (\"use client\" pragma)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("imports the canonical built-in list from cc-tools", () => {
    expect(src).toMatch(/from\s+"@\/lib\/cc-tools"/)
    expect(src).toMatch(/CC_BUILTIN_TOOLS/)
    expect(src).toMatch(/CC_TOP_SUGGESTIONS/)
    expect(src).toMatch(/filterCcBuiltins/)
    expect(src).toMatch(/classifyCcToolName/)
  })

  it("submits the picked value as `toolScope_custom` (hidden input)", () => {
    // advanceWizard already reads this field; the combobox is a
    // drop-in replacement for the legacy MCP free-text input.
    expect(src).toMatch(/type="hidden"\s+name="toolScope_custom"/)
  })

  it("renders the listbox (combobox role + aria-controls)", () => {
    expect(src).toMatch(/role="combobox"/)
    expect(src).toMatch(/aria-autocomplete="list"/)
    expect(src).toMatch(/role="listbox"/)
  })

  it("empty input falls back to CC_TOP_SUGGESTIONS for the dropdown", () => {
    // buildRows: when query is empty, returns the top suggested
    // built-ins instead of nothing.
    expect(src).toMatch(/CC_TOP_SUGGESTIONS\.map/)
  })

  it("non-empty query goes through filterCcBuiltins (substring match)", () => {
    expect(src).toMatch(/filterCcBuiltins\(trimmed\)/)
  })

  it("exposes a 'Use as custom tool name' affordance for unmatched text", () => {
    // The brief calls this out explicitly: when the typed text does
    // not match any built-in, the dropdown shows a custom row that
    // commits the raw text on click.
    expect(src).toMatch(/useAsCustom/)
    // The affordance row is only appended when the typed text is NOT
    // a built-in (avoid duplicate "Bash" + "Use as Bash" rows).
    expect(src).toMatch(/isExactBuiltin/)
  })

  it("keyboard nav: ArrowDown / ArrowUp move highlight, Enter commits", () => {
    expect(src).toMatch(/ev\.key === "ArrowDown"/)
    expect(src).toMatch(/ev\.key === "ArrowUp"/)
    expect(src).toMatch(/ev\.key === "Enter"/)
    expect(src).toMatch(/setHighlight/)
  })

  it("Escape closes the dropdown without committing", () => {
    expect(src).toMatch(/ev\.key === "Escape"/)
  })

  it("Tab closes the dropdown but falls through to default focus behaviour", () => {
    // We `return` from the handler without preventDefault — the brief
    // requires Tab to focus the next form field.
    const tabBlock = src.match(/ev\.key === "Tab"[\s\S]{0,160}/)
    expect(tabBlock).not.toBeNull()
    expect(tabBlock![0]).toMatch(/setOpen\(false\)/)
    // No preventDefault inside the Tab branch (must let browser focus).
    expect(tabBlock![0]).not.toMatch(/ev\.preventDefault\(\)/)
  })

  it("Backspace on an empty input wipes the persisted value", () => {
    expect(src).toMatch(/ev\.key === "Backspace"/)
    expect(src).toMatch(/setValue\(""\)/)
  })

  it("click-outside listener closes the dropdown", () => {
    expect(src).toMatch(/document\.addEventListener\("mousedown"/)
    expect(src).toMatch(/containerRef\.current/)
  })

  it("badges suggestions with built-in / MCP / custom category", () => {
    expect(src).toMatch(/badgeText\(/)
    // String literals only inside the badge resolver — keep them
    // pinned so a future refactor that drops a category surfaces.
    expect(src).toMatch(/"built-in"/)
    expect(src).toMatch(/"mcp"/)
    expect(src).toMatch(/"custom"/)
  })

  it("uses the locale-resolved description per suggestion row", () => {
    expect(src).toMatch(/locale === "ko"\s*\?\s*e\.description\.ko\s*:\s*e\.description\.en/)
  })

  it("translates UI copy through i18n dict (no hardcoded English)", () => {
    expect(src).toMatch(/translate\(locale,/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.placeholder/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.useAsCustom/)
  })

  it("does NOT import the @/components/ui barrel (client-bundle slimness)", () => {
    expect(src).not.toMatch(/from\s+"@\/components\/ui"/)
  })

  it("re-exports the canonical built-in manifest comment (refactor anchor)", () => {
    // The CC_BUILTIN_MANIFEST comment pins every name a future
    // refactor would surface in the diff if it drops a tool.
    for (const entry of CC_BUILTIN_TOOLS) {
      expect(src).toMatch(new RegExp(`\\b${entry.name}\\b`))
    }
    for (const name of CC_TOP_SUGGESTIONS) {
      // Top suggestions show up in the dropdown surface, so a name
      // grep is sufficient. (CC_TOP_SUGGESTIONS is exercised at the
      // cc-tools.test.ts level for behavioural equivalence.)
      expect(src).toMatch(new RegExp(`\\b${name}\\b`))
    }
  })
})
