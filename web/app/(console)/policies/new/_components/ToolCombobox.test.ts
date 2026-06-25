import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  CC_BUILTIN_TOOLS,
  CC_TOP_SUGGESTIONS,
} from "../../../../../lib/cc-tools"

/**
 * D70 / D71: source-level invariants for the Step 2 tool autocomplete
 * combobox.
 *
 * The repo's vitest runs node-only without the React + Next path alias
 * chain, so behavioural assertions go through source grep (same pattern
 * as the surrounding *.test.ts files in this directory). The brief's
 * required behaviours are encoded below as TSX source pins:
 *
 *   - Empty input renders the top suggested built-ins.
 *   - Typing filters built-ins by case-insensitive substring (uses
 *     `filterCcBuiltins`).
 *   - A typed string with no built-in match exposes a "Use as custom
 *     tool name" affordance (the brief's MCP / custom path).
 *   - Enter on the highlighted row commits the value and closes.
 *   - ArrowUp / ArrowDown move the highlight; first press from a
 *     closed dropdown lands on first/last option without skipping.
 *   - Home / End / PageUp / PageDown move by larger steps.
 *   - Escape reverts in-flight query to last committed value (open)
 *     or clears value (closed).
 *   - Tab closes popup and falls through to default focus behaviour.
 *   - Click-outside / touch-outside / focusout close the popup.
 *   - Hidden `toolScope_custom` carries the CANONICAL name when the
 *     typed value resolves to a built-in (case-fixed on submit).
 *   - Inline hint when the operator types a legacy alias.
 *   - aria-activedescendant tracks the highlighted option's DOM id.
 *
 * Behavioural runtime coverage of the click-outside / keyboard chain
 * lives at the integration level (the wizard-wiring tests pin the
 * single hidden `toolScope_custom` input that advanceWizard reads).
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("ToolCombobox — Step 2 single autocomplete (D70 / D71)", () => {
  const src = read("ToolCombobox.tsx")

  it("is a client component (\"use client\" pragma)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("imports the canonical built-in list + legacy alias helper from cc-tools", () => {
    expect(src).toMatch(/from\s+"@\/lib\/cc-tools"/)
    expect(src).toMatch(/CC_BUILTIN_TOOLS/)
    expect(src).toMatch(/CC_TOP_SUGGESTIONS/)
    expect(src).toMatch(/filterCcBuiltins/)
    expect(src).toMatch(/classifyCcToolName/)
    expect(src).toMatch(/legacyAliasCanonical/)
  })

  it("submits the picked value as `toolScope_custom` (hidden input)", () => {
    expect(src).toMatch(/type="hidden"\s+name="toolScope_custom"/)
  })

  it("canonicalises typed built-in names before persisting (case-fix)", () => {
    // 'bash' typed -> 'Bash' written to the hidden input so
    // TOOL_SPECIFIC_BY_NAME["Bash"] hits downstream.
    expect(src).toMatch(/function canonicalizeForSubmit/)
    expect(src).toMatch(/canonicalizeForSubmit\(initialValue/)
    // canonicalizeForSubmit is called both onChange and on commit.
    const onChangeBlock = src.match(/onChange=\{\(ev\) =>[\s\S]{0,600}canonicalizeForSubmit/)
    expect(onChangeBlock).not.toBeNull()
  })

  it("renders the listbox (combobox role + aria-controls when open)", () => {
    expect(src).toMatch(/role="combobox"/)
    expect(src).toMatch(/aria-autocomplete="list"/)
    expect(src).toMatch(/role="listbox"/)
    // aria-controls conditional on listbox being mounted.
    expect(src).toMatch(/aria-controls=\{listboxRendered \? listboxId : undefined\}/)
  })

  it("wires aria-activedescendant to the highlighted option's DOM id", () => {
    expect(src).toMatch(/aria-activedescendant=\{activeDescendant\}/)
    expect(src).toMatch(/function optionDomId/)
    // Each <li role="option"> carries an id matching optionDomId.
    expect(src).toMatch(/id=\{domId\}/)
  })

  it("aria-selected reflects the COMMITTED value, not the keyboard cursor", () => {
    // Cursor moves via aria-activedescendant; aria-selected uses the
    // committed value comparison so AT announces the chosen item once.
    expect(src).toMatch(/aria-selected=\{isCommitted\}/)
    expect(src).toMatch(/const isCommitted = e\.name === value/)
  })

  it("forwards an external aria-labelledby (parent owns the visible label)", () => {
    expect(src).toMatch(/ariaLabelledBy\?: string/)
    expect(src).toMatch(/aria-labelledby=\{ariaLabelledBy\}/)
  })

  it("empty input falls back to CC_TOP_SUGGESTIONS for the dropdown", () => {
    expect(src).toMatch(/CC_TOP_SUGGESTIONS\.map/)
  })

  it("non-empty query goes through filterCcBuiltins (substring match)", () => {
    expect(src).toMatch(/filterCcBuiltins\(trimmed\)/)
  })

  it("exposes a 'Use as custom tool name' affordance for unmatched text", () => {
    expect(src).toMatch(/useAsCustom/)
    expect(src).toMatch(/isExactBuiltin/)
  })

  it("ArrowDown from closed dropdown opens AND lands on first option (no skip)", () => {
    // First ArrowDown from open=false should set highlight=0, not
    // advance past it on the same keystroke.
    const block = src.match(/ev\.key === "ArrowDown"[\s\S]{0,300}/)
    expect(block).not.toBeNull()
    expect(block![0]).toMatch(/setHighlight\(0\)/)
  })

  it("ArrowUp from closed dropdown opens AND lands on last option", () => {
    const block = src.match(/ev\.key === "ArrowUp"[\s\S]{0,300}/)
    expect(block).not.toBeNull()
    expect(block![0]).toMatch(/setHighlight\(Math\.max\(rows\.length - 1, 0\)\)/)
  })

  it("Home / End / PageUp / PageDown handled", () => {
    expect(src).toMatch(/ev\.key === "Home"/)
    expect(src).toMatch(/ev\.key === "End"/)
    expect(src).toMatch(/ev\.key === "PageDown"/)
    expect(src).toMatch(/ev\.key === "PageUp"/)
  })

  it("Enter on the highlighted row commits the value", () => {
    expect(src).toMatch(/ev\.key === "Enter"/)
    expect(src).toMatch(/onPick\(row\)/)
  })

  it("Escape (open) reverts in-flight query to last-committed value", () => {
    const block = src.match(/ev\.key === "Escape"[\s\S]{0,800}/)
    expect(block).not.toBeNull()
    // open: setQuery(value) reverts in-flight typing; setOpen(false).
    expect(block![0]).toMatch(/setQuery\(value\)/)
    expect(block![0]).toMatch(/setOpen\(false\)/)
    // closed: clears both query and value.
    expect(block![0]).toMatch(/setQuery\(""\)/)
    expect(block![0]).toMatch(/setValue\(""\)/)
  })

  it("Tab closes the dropdown but falls through to default focus behaviour", () => {
    const tabBlock = src.match(/ev\.key === "Tab"[\s\S]{0,160}/)
    expect(tabBlock).not.toBeNull()
    expect(tabBlock![0]).toMatch(/setOpen\(false\)/)
    expect(tabBlock![0]).not.toMatch(/ev\.preventDefault\(\)/)
  })

  it("pointerdown listener closes the dropdown (mouse + touch)", () => {
    expect(src).toMatch(/document\.addEventListener\("pointerdown"/)
    expect(src).toMatch(/containerRef\.current/)
  })

  it("focusout listener closes when focus leaves the combobox entirely", () => {
    expect(src).toMatch(/addEventListener\("focusout"/)
    expect(src).toMatch(/relatedTarget/)
  })

  it("badges suggestions via i18n dict keys (no hardcoded English / Korean)", () => {
    expect(src).toMatch(/function badgeKey/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.badge\.builtin/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.badge\.mcp/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.badge\.custom/)
    // No hardcoded badge strings ("빌트인" / "Built-in" / "커스텀" /
    // "Custom") inside the component source.
    expect(src).not.toMatch(/"빌트인"/)
    expect(src).not.toMatch(/"Built-in"/)
    expect(src).not.toMatch(/"커스텀"/)
  })

  it("uses the locale-resolved description per suggestion row", () => {
    expect(src).toMatch(/locale === "ko"\s*\?\s*e\.description\.ko\s*:\s*e\.description\.en/)
  })

  it("translates UI copy through i18n dict", () => {
    expect(src).toMatch(/translate\(locale,/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.placeholder/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.useAsCustom/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.legacyAliasHint/)
    expect(src).toMatch(/newPolicy\.wizard\.step2\.toolPicker\.customInvalidHint/)
  })

  it("inline legacy-alias hint surfaces when user types a pre-rename name", () => {
    expect(src).toMatch(/tool-combobox-legacy-alias-hint/)
    expect(src).toMatch(/legacyAliasCanonical/)
  })

  it("inline custom-invalid hint warns on whitespace / special chars without blocking", () => {
    expect(src).toMatch(/tool-combobox-custom-invalid-hint/)
    expect(src).toMatch(/CUSTOM_NAME_PATTERN/)
    // Pattern matches the legacy MCP input's regex.
    expect(src).toMatch(/mcp__\[A-Za-z0-9_\]\+__\[A-Za-z0-9_\]\+/)
  })

  it("hard cap maxLength={256} on the visible input (paste forgiveness)", () => {
    expect(src).toMatch(/MAX_TOOL_NAME_LENGTH = 256/)
    expect(src).toMatch(/maxLength=\{MAX_TOOL_NAME_LENGTH\}/)
  })

  it("does NOT import the @/components/ui barrel (client-bundle slimness)", () => {
    expect(src).not.toMatch(/from\s+"@\/components\/ui"/)
  })

  it("re-exports the canonical built-in manifest comment (refactor anchor)", () => {
    // Every canonical name must appear at least once in the source
    // (either as a CC_BUILTIN_TOOLS entry consumer or in the
    // CC_BUILTIN_MANIFEST trailer comment).
    for (const entry of CC_BUILTIN_TOOLS) {
      expect(src).toMatch(new RegExp(`\\b${entry.name}\\b`))
    }
    for (const name of CC_TOP_SUGGESTIONS) {
      expect(src).toMatch(new RegExp(`\\b${name}\\b`))
    }
  })

  it("docstring enumerates the v2.1.170 omissions the new combobox fixes", () => {
    // The brief calls out that the docstring should describe the
    // actual v2.1.170 omissions (Agent / Skill / ToolSearch /
    // StructuredOutput / Cron* / Monitor / Worktree* / Task[Create|
    // Get|List|Update|Stop|Output] / SendUserMessage / ListAgents /
    // etc.) rather than the pre-rename names.
    const top = src.slice(0, src.indexOf("import {"))
    expect(top).toMatch(/Agent\b/)
    expect(top).toMatch(/Skill\b/)
    expect(top).toMatch(/ToolSearch\b/)
    expect(top).toMatch(/StructuredOutput/)
    expect(top).toMatch(/Cron(Create|Delete|List)/)
    expect(top).toMatch(/Monitor\b/)
    expect(top).toMatch(/Worktree/)
    expect(top).toMatch(/SendUserMessage/)
    expect(top).toMatch(/ListAgents/)
    expect(top).toMatch(/TaskCreate/)
    expect(top).toMatch(/TaskStop/)
    expect(top).toMatch(/TaskOutput/)
  })
})
