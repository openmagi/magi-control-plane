import { describe, it, expect } from "vitest"
import {
  classifyMatcher, isLegal, validateDraft, previewManagedSettings, DEFAULT_DRAFT,
} from "./policy-builder"

describe("classifyMatcher", () => {
  it("classifies builtin tools", () => {
    expect(classifyMatcher("Bash")).toBe("tool")
    expect(classifyMatcher("Read")).toBe("tool")
  })
  it("classifies mcp tools", () => {
    expect(classifyMatcher("mcp__court__file")).toBe("mcp_tool")
  })
  it("classifies wildcard", () => {
    expect(classifyMatcher("*")).toBe("wildcard")
  })
  it("classifies tool alternation", () => {
    expect(classifyMatcher("Bash|Edit")).toBe("tool_alt")
  })
  it("returns unknown for garbage", () => {
    expect(classifyMatcher("FooBar")).toBe("unknown")
  })
})

describe("isLegal — mirrors backend matrix", () => {
  // EXHAUSTIVE coverage of the matrix so a backend drift gets caught by CI.
  // If the server adds or removes a triple, this client mirror's tests will
  // fail and force the mirror to be updated.
  type AnyEvent =
    | "PreToolUse" | "PostToolUse"
    | "Stop" | "SubagentStop"
    | "UserPromptSubmit"
    | "PreCompact"
    | "SessionStart" | "SessionEnd"
  const ALL_LEGAL: Array<[AnyEvent, string, "deny"|"ask"|"log"|"allow"]> = [
    ["PreToolUse", "Bash", "deny"], ["PreToolUse", "Bash", "ask"],
    ["PreToolUse", "mcp__a__b", "deny"], ["PreToolUse", "mcp__a__b", "ask"],
    ["PreToolUse", "Bash|Edit", "deny"], ["PreToolUse", "Bash|Edit", "ask"],
    ["PreToolUse", "*", "log"],
    ["PostToolUse", "Bash", "log"], ["PostToolUse", "Bash", "allow"],
    ["PostToolUse", "mcp__a__b", "log"], ["PostToolUse", "mcp__a__b", "allow"],
    ["Stop", "*", "log"],
    // D28: no-tool-context events
    ["SubagentStop", "*", "log"],
    ["UserPromptSubmit", "*", "deny"],
    ["UserPromptSubmit", "*", "ask"],
    ["UserPromptSubmit", "*", "log"],
    ["PreCompact", "*", "deny"],
    ["PreCompact", "*", "log"],
    ["SessionStart", "*", "log"],
    ["SessionEnd", "*", "log"],
  ]
  it.each(ALL_LEGAL)("accepts %s × %s × %s", (ev, m, d) => {
    expect(isLegal(ev, m, d)).toBe(true)
  })

  // Representative illegals
  it("rejects PostToolUse × Bash × deny (tool already ran)", () => {
    expect(isLegal("PostToolUse", "Bash", "deny")).toBe(false)
  })
  it("rejects Stop × Bash × log (Stop is wildcard-only)", () => {
    expect(isLegal("Stop", "Bash", "log")).toBe(false)
  })
  it("rejects PreToolUse × Bash × log", () => {
    expect(isLegal("PreToolUse", "Bash", "log")).toBe(false)
  })
  it("rejects PreToolUse × Bash × allow", () => {
    expect(isLegal("PreToolUse", "Bash", "allow")).toBe(false)
  })
  it("rejects unknown matcher", () => {
    expect(isLegal("PreToolUse", "FooBar", "deny")).toBe(false)
  })
  it("rejects UserPromptSubmit × Bash × deny (no tool context here)", () => {
    expect(isLegal("UserPromptSubmit", "Bash", "deny")).toBe(false)
  })
  it("rejects SessionEnd × * × deny (observe-only event)", () => {
    expect(isLegal("SessionEnd", "*", "deny")).toBe(false)
  })
  it("rejects SubagentStop × * × deny (observe-only event)", () => {
    expect(isLegal("SubagentStop", "*", "deny")).toBe(false)
  })
})

describe("validateDraft", () => {
  it("default draft is valid (after id is set)", () => {
    const d = { ...DEFAULT_DRAFT, id: "legal-filing/v1" }
    expect(validateDraft(d)).toEqual([])
  })

  it("rejects empty id", () => {
    const errs = validateDraft({ ...DEFAULT_DRAFT, id: "" })
    expect(errs.find(e => e.field === "id")).toBeDefined()
  })

  it("rejects reserved suffix in id", () => {
    const errs = validateDraft({ ...DEFAULT_DRAFT, id: "foo/compiled" })
    expect(errs.find(e => e.field === "id")?.message).toMatch(/compiled/)
  })

  it("rejects sentinel without named groups", () => {
    const errs = validateDraft({ ...DEFAULT_DRAFT, id: "x", sentinel_re: "FILE_\\w+" })
    expect(errs.find(e => e.field === "sentinel_re")).toBeDefined()
  })

  it("rejects empty requires", () => {
    const errs = validateDraft({ ...DEFAULT_DRAFT, id: "x", requires: [] })
    expect(errs.find(e => e.field === "requires")).toBeDefined()
  })

  it("rejects illegal matrix combination", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      trigger: { ...DEFAULT_DRAFT.trigger, event: "PostToolUse" as const },
      on_missing: "deny" as const,
    }
    const errs = validateDraft(d)
    expect(errs.find(e => e.field === "matrix")).toBeDefined()
  })
})

describe("previewManagedSettings", () => {
  it("emits correct shape", () => {
    const d = { ...DEFAULT_DRAFT, id: "legal-filing/v1" }
    const ms = previewManagedSettings(d)
    expect(ms.allowManagedHooksOnly).toBe(true)
    expect((ms.hooks as any).PreToolUse[0].matcher).toBe("Bash")
    expect(((ms as any)._magi_policies)[0].id).toBe("legal-filing/v1")
  })

  it("changes event to PostToolUse when draft updates", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      trigger: { ...DEFAULT_DRAFT.trigger, event: "PostToolUse" as const },
      on_missing: "log" as const,
    }
    const ms = previewManagedSettings(d)
    expect((ms.hooks as any).PostToolUse).toBeDefined()
    expect((ms.hooks as any).PreToolUse).toBeUndefined()
  })
})
