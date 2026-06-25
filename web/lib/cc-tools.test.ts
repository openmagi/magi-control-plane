import { describe, it, expect } from "vitest"
import {
  CC_BUILTIN_TOOLS,
  CC_TOP_SUGGESTIONS,
  classifyCcToolName,
  filterCcBuiltins,
  findCcBuiltinTool,
  isCcBuiltinTool,
} from "./cc-tools"

/**
 * D70: canonical CC built-in tool list invariants.
 *
 * The list is hand-mirrored from the v2.1.170 claude binary strings
 * table; these tests pin every name the wizard's autocomplete must
 * surface so a future refactor that intends to drop or rename one
 * surfaces in the diff. The Step 2 ToolCombobox imports from this
 * module — if a tool name disappears here, the dropdown silently
 * shrinks; this test catches that.
 */

describe("cc-tools — canonical built-in list", () => {
  it("includes every CC v2.1.170 built-in by canonical name", () => {
    // Source-verified from the strings table of
    //   /opt/homebrew/Caskroom/claude-code/2.1.170/claude
    const expected = [
      "Bash",
      "BashOutput",
      "KillBash",
      "Read",
      "Write",
      "Edit",
      "MultiEdit",
      "Glob",
      "Grep",
      "WebFetch",
      "WebSearch",
      "NotebookEdit",
      "NotebookRead",
      "Task",
      "TodoWrite",
      "ExitPlanMode",
      "AskUserQuestion",
    ]
    const got = CC_BUILTIN_TOOLS.map((t) => t.name)
    for (const name of expected) {
      expect(got).toContain(name)
    }
    // Pin the count too so a silent addition or removal surfaces.
    expect(CC_BUILTIN_TOOLS.length).toBe(expected.length)
  })

  it("every entry carries kind='built-in' and bilingual descriptions", () => {
    for (const entry of CC_BUILTIN_TOOLS) {
      expect(entry.kind).toBe("built-in")
      expect(typeof entry.description.ko).toBe("string")
      expect(typeof entry.description.en).toBe("string")
      expect(entry.description.ko.trim().length).toBeGreaterThan(0)
      expect(entry.description.en.trim().length).toBeGreaterThan(0)
    }
  })

  it("CC_TOP_SUGGESTIONS surfaces 5 picks all backed by the canonical list", () => {
    expect(CC_TOP_SUGGESTIONS.length).toBe(5)
    for (const name of CC_TOP_SUGGESTIONS) {
      // every top suggestion must be a real built-in
      expect(findCcBuiltinTool(name)).not.toBeNull()
    }
    // Brief mandates these five specific names as the default surface.
    expect(CC_TOP_SUGGESTIONS).toEqual(["Bash", "Read", "Edit", "WebFetch", "Task"])
  })
})

describe("cc-tools — classification + lookup", () => {
  it("isCcBuiltinTool is case-insensitive", () => {
    expect(isCcBuiltinTool("Bash")).toBe(true)
    expect(isCcBuiltinTool("bash")).toBe(true)
    expect(isCcBuiltinTool("BASH")).toBe(true)
    expect(isCcBuiltinTool("multiEdit")).toBe(true)
    expect(isCcBuiltinTool("ExitPlanMode")).toBe(true)
    expect(isCcBuiltinTool("AskUserQuestion")).toBe(true)
    expect(isCcBuiltinTool("definitely_not_a_tool")).toBe(false)
  })

  it("findCcBuiltinTool returns canonical entry or null", () => {
    const e = findCcBuiltinTool("bash")
    expect(e).not.toBeNull()
    expect(e!.name).toBe("Bash")
    expect(findCcBuiltinTool("mcp__github__search")).toBeNull()
  })

  it("classifyCcToolName splits built-in / mcp / custom", () => {
    expect(classifyCcToolName("Bash")).toBe("built-in")
    expect(classifyCcToolName("multiedit")).toBe("built-in")
    expect(classifyCcToolName("mcp__github__search")).toBe("mcp")
    expect(classifyCcToolName("MyCustomTool")).toBe("custom")
    expect(classifyCcToolName("")).toBe("custom")
    expect(classifyCcToolName("   ")).toBe("custom")
  })
})

describe("cc-tools — substring filter", () => {
  it("returns every built-in for an empty query", () => {
    expect(filterCcBuiltins("").length).toBe(CC_BUILTIN_TOOLS.length)
    expect(filterCcBuiltins("   ").length).toBe(CC_BUILTIN_TOOLS.length)
  })

  it("'mu' is a case-insensitive substring match that surfaces MultiEdit", () => {
    // Brief mentions "ma" → MultiEdit, but plain substring search over
    // "multiedit" (lowercase) misses "ma" (m-a is not a substring).
    // We use a real substring prefix here; the brief's UX intent
    // ("typing two characters narrows the list") still holds.
    const names = filterCcBuiltins("mu").map((t) => t.name)
    expect(names).toContain("MultiEdit")
  })

  it("'edit' surfaces Edit + MultiEdit + NotebookEdit (multi-match)", () => {
    const names = filterCcBuiltins("edit").map((t) => t.name)
    expect(names).toContain("Edit")
    expect(names).toContain("MultiEdit")
    expect(names).toContain("NotebookEdit")
  })

  it("'web' matches WebFetch + WebSearch", () => {
    const names = filterCcBuiltins("web").map((t) => t.name)
    expect(names).toContain("WebFetch")
    expect(names).toContain("WebSearch")
  })

  it("no match returns an empty array (combobox custom row handles it)", () => {
    expect(filterCcBuiltins("mcp__github__search")).toEqual([])
    expect(filterCcBuiltins("definitely_not_a_tool")).toEqual([])
  })

  it("preserves canonical declaration order", () => {
    const all = filterCcBuiltins("").map((t) => t.name)
    expect(all.indexOf("Bash")).toBeLessThan(all.indexOf("Read"))
    expect(all.indexOf("Read")).toBeLessThan(all.indexOf("Edit"))
    expect(all.indexOf("Edit")).toBeLessThan(all.indexOf("WebFetch"))
  })
})
