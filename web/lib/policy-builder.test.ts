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

describe("isLegal. mirrors backend matrix", () => {
  // EXHAUSTIVE coverage of the matrix so a backend drift gets caught by CI.
  // If the server adds or removes a triple, this client mirror's tests will
  // fail and force the mirror to be updated.
  type AnyEvent =
    | "PreToolUse" | "PostToolUse"
    | "Stop" | "SubagentStop"
    | "UserPromptSubmit"
    | "PreCompact"
    | "SessionStart" | "SessionEnd"
  // D31: triples now use action archetypes (block/ask/audit).
  const ALL_LEGAL: Array<[AnyEvent, string, "block"|"ask"|"audit"]> = [
    ["PreToolUse", "Bash", "block"],     ["PreToolUse", "Bash", "ask"],     ["PreToolUse", "Bash", "audit"],
    ["PreToolUse", "mcp__a__b", "block"], ["PreToolUse", "mcp__a__b", "ask"], ["PreToolUse", "mcp__a__b", "audit"],
    ["PreToolUse", "Bash|Edit", "block"], ["PreToolUse", "Bash|Edit", "ask"], ["PreToolUse", "Bash|Edit", "audit"],
    ["PreToolUse", "*", "audit"],
    ["PostToolUse", "Bash", "audit"],
    ["PostToolUse", "mcp__a__b", "audit"],
    ["Stop", "*", "audit"],
    ["SubagentStop", "*", "audit"],
    ["UserPromptSubmit", "*", "block"],
    ["UserPromptSubmit", "*", "ask"],
    ["UserPromptSubmit", "*", "audit"],
    ["PreCompact", "*", "block"],
    ["PreCompact", "*", "audit"],
    ["SessionStart", "*", "audit"],
    ["SessionEnd", "*", "audit"],
  ]
  it.each(ALL_LEGAL)("accepts %s × %s × %s", (ev, m, d) => {
    expect(isLegal(ev, m, d)).toBe(true)
  })

  // Representative illegals
  it("rejects PostToolUse × Bash × block (tool already ran)", () => {
    expect(isLegal("PostToolUse", "Bash", "block")).toBe(false)
  })
  it("rejects Stop × Bash × audit (Stop is wildcard-only)", () => {
    expect(isLegal("Stop", "Bash", "audit")).toBe(false)
  })
  // D31: legacy log/allow fold into audit via LEGACY_DECISION_TO_ACTION.
  // PreToolUse + Bash + audit is legal under the new matrix, so the
  // legacy aliases now map to a legal triple.
  it("legacy log/allow on PreToolUse + Bash maps to audit and is legal", () => {
    expect(isLegal("PreToolUse", "Bash", "log")).toBe(true)
    expect(isLegal("PreToolUse", "Bash", "allow")).toBe(true)
  })
  it("rejects unknown matcher", () => {
    expect(isLegal("PreToolUse", "FooBar", "block")).toBe(false)
  })
  it("rejects UserPromptSubmit × Bash × block (no tool context here)", () => {
    expect(isLegal("UserPromptSubmit", "Bash", "block")).toBe(false)
  })
  it("rejects SessionEnd × * × block (observe-only event)", () => {
    expect(isLegal("SessionEnd", "*", "block")).toBe(false)
  })
  it("rejects SubagentStop × * × block (observe-only event)", () => {
    expect(isLegal("SubagentStop", "*", "block")).toBe(false)
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

  it("D43: accepts sentinel without prescribed named groups", () => {
    // PR1 dropped the matter/doc_id named-group requirement; the
    // runtime no longer reads specific group names from sentinel_re.
    const errs = validateDraft({ ...DEFAULT_DRAFT, id: "x", sentinel_re: "FILE_\\w+" })
    expect(errs.find(e => e.field === "sentinel_re")).toBeUndefined()
  })

  it("D43: accepts policy without sentinel_re entirely", () => {
    const errs = validateDraft({ ...DEFAULT_DRAFT, id: "x", sentinel_re: undefined })
    expect(errs.find(e => e.field === "sentinel_re")).toBeUndefined()
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
