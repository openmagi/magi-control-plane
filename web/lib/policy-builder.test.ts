import { describe, it, expect } from "vitest"
import {
  classifyMatcher, isLegal, validateDraft, previewManagedSettings, DEFAULT_DRAFT,
  PREVIEW_PREFIX,
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
  // D82d — PostToolUse + Bash + block is now LEGAL as the CC
  // retry-feedback channel. ask stays illegal on PostToolUse* events
  // because there is no interactive surface to interrupt to after
  // the tool ran.
  it("rejects PostToolUse × Bash × ask (no interactive surface post-tool)", () => {
    expect(isLegal("PostToolUse", "Bash", "ask")).toBe(false)
  })
  it("admits PostToolUse × Bash × block (D82d retry-feedback channel)", () => {
    expect(isLegal("PostToolUse", "Bash", "block")).toBe(true)
  })
  it("admits PostToolUseFailure × Bash × block (D82d failure recovery)", () => {
    expect(isLegal("PostToolUseFailure", "Bash", "block")).toBe(true)
  })
  it("admits PostToolBatch × * × block (D82d whole-batch retry)", () => {
    expect(isLegal("PostToolBatch", "*", "block")).toBe(true)
  })
  it("rejects PostToolBatch × Bash × block (wildcard-only matcher)", () => {
    expect(isLegal("PostToolBatch", "Bash", "block")).toBe(false)
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

  // ── D58: full CC hook surface ───────────────────────────────────
  it("D58 accepts the gate-style permission + elicitation hooks with block/ask/audit", () => {
    for (const ev of ["PermissionRequest", "Elicitation"] as const) {
      expect(isLegal(ev, "*", "block")).toBe(true)
      expect(isLegal(ev, "*", "ask")).toBe(true)
      expect(isLegal(ev, "*", "audit")).toBe(true)
    }
  })

  it("D58 accepts block/audit (no ask) on the mid-process hooks", () => {
    for (const ev of ["UserPromptExpansion"] as const) {
      expect(isLegal(ev, "*", "block")).toBe(true)
      expect(isLegal(ev, "*", "audit")).toBe(true)
      expect(isLegal(ev, "*", "ask")).toBe(false)
    }
  })

  it("D58 audit-only events accept audit on wildcard and reject block", () => {
    // D82d — PostToolUseFailure / PostToolBatch are no longer pure
    // audit-only on the block dimension: PostToolUseFailure admits
    // block on per-tool matchers (failure recovery), PostToolBatch
    // admits block on wildcard (whole-batch retry). The event names
    // stay in the audit list because audit + wildcard is still legal
    // on both; we split the block-rejection assertion into the
    // events whose payload genuinely has no retry-feedback channel.
    const auditAndBlockBoth = [
      "PermissionDenied",
      "PostCompact", "ElicitationResult",
      "SubagentStart", "StopFailure",
      "Setup", "Notification",
      "TeammateIdle", "TaskCreated", "TaskCompleted",
      "ConfigChange",
      "WorktreeCreate", "WorktreeRemove",
      "InstructionsLoaded",
      "CwdChanged", "FileChanged",
      "MessageDisplay",
    ] as const
    for (const ev of auditAndBlockBoth) {
      expect(isLegal(ev, "*", "audit")).toBe(true)
      expect(isLegal(ev, "*", "block")).toBe(false)
    }
    // PostToolUseFailure: audit on wildcard legal; block on wildcard
    // illegal (block is per-tool only on this event).
    expect(isLegal("PostToolUseFailure", "*", "audit")).toBe(true)
    expect(isLegal("PostToolUseFailure", "*", "block")).toBe(false)
    // PostToolBatch: both audit and block legal on wildcard.
    expect(isLegal("PostToolBatch", "*", "audit")).toBe(true)
    expect(isLegal("PostToolBatch", "*", "block")).toBe(true)
  })

  // D70+D82d lockstep — block + audit must move together on the
  // per-tool matcher × tool-context event cross-product so a future
  // widening of one action without the other fails CI. matrix.py
  // registers audit on tool / mcp_tool / tool_alt / wildcard for
  // PostToolUseFailure + PostToolBatch via `_AUDIT_TOOL_CONTEXT_EVENTS`
  // and admits block on the per-tool subset for D82d; the TS mirror
  // must match exactly.
  describe("D70+D82d audit-block lockstep on PostToolUse* events", () => {
    type PostToolEvent =
      | "PostToolUse"
      | "PostToolUseFailure"
      | "PostToolBatch"

    const cases: Array<[PostToolEvent, string]> = [
      ["PostToolUse",        "Bash"],
      ["PostToolUse",        "Edit"],
      ["PostToolUse",        "Bash|Edit"],
      ["PostToolUse",        "mcp__court__file"],
      ["PostToolUseFailure", "Bash"],
      ["PostToolUseFailure", "Edit"],
      ["PostToolUseFailure", "mcp__court__file"],
    ]
    it.each(cases)("%s × %s accepts BOTH block AND audit", (ev, m) => {
      expect(isLegal(ev, m, "audit")).toBe(true)
      expect(isLegal(ev, m, "block")).toBe(true)
    })

    it("PostToolUseFailure × tool_alt accepts audit but NOT block", () => {
      // tool_alt stays excluded on block (batched-tool retry belongs
      // on PostToolBatch); audit is symmetric with the matrix.
      expect(isLegal("PostToolUseFailure", "Bash|Edit", "audit")).toBe(true)
      expect(isLegal("PostToolUseFailure", "Bash|Edit", "block")).toBe(false)
    })

    it("PostToolUse × wildcard accepts audit (matrix.py:397)", () => {
      expect(isLegal("PostToolUse", "*", "audit")).toBe(true)
      // wildcard + block stays illegal — "block every PostToolUse"
      // is rarely the operator's intent.
      expect(isLegal("PostToolUse", "*", "block")).toBe(false)
    })

    it("PostToolUseFailure × wildcard accepts audit (matrix.py lockstep)", () => {
      expect(isLegal("PostToolUseFailure", "*", "audit")).toBe(true)
    })

    it("PostToolBatch × per-tool accepts audit but NOT block", () => {
      // matrix.py registers audit on tool / mcp_tool / tool_alt /
      // wildcard for PostToolBatch; block stays wildcard-only because
      // the batch event covers the whole turn's tool calls.
      expect(isLegal("PostToolBatch", "Bash", "audit")).toBe(true)
      expect(isLegal("PostToolBatch", "Bash|Edit", "audit")).toBe(true)
      expect(isLegal("PostToolBatch", "mcp__a__b", "audit")).toBe(true)
      expect(isLegal("PostToolBatch", "Bash", "block")).toBe(false)
    })
  })

  it("D58 no-tool-context events reject tool matchers", () => {
    for (const ev of [
      "PermissionRequest", "Elicitation", "UserPromptExpansion",
      "WorktreeCreate", "FileChanged", "Notification",
    ] as const) {
      expect(isLegal(ev, "Bash", "audit")).toBe(false)
    }
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
    // D82d — PostToolUse + Bash + block is now LEGAL (CC retry-feedback
    // channel). ask stays illegal on post-tool events because there
    // is no interactive surface to interrupt to. Switch to ask so
    // the matrix-illegal guard still has a witness here.
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      trigger: { ...DEFAULT_DRAFT.trigger, event: "PostToolUse" as const },
      action: "ask" as const,
      on_missing: "deny" as const,
    }
    const errs = validateDraft(d)
    expect(errs.find(e => e.field === "matrix")).toBeDefined()
  })
})

// ── P8: step IR fail-closed mirror in the wizard ─────────────────────
describe("validateDraft P8 step registry check", () => {
  it("with no registry supplied, accepts any step (back-compat)", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      requires: [{ kind: "step" as const, step: "totally_made_up", verdict: "pass" }],
    }
    expect(validateDraft(d)).toEqual([])
  })

  it("with registry supplied, accepts a wired step", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      requires: [{ kind: "step" as const, step: "citation_verify", verdict: "pass" }],
    }
    const errs = validateDraft(d, { availableSteps: ["citation_verify"] })
    expect(errs.filter(e => e.field.startsWith("requires["))).toEqual([])
  })

  it("with registry supplied, flags an unknown step", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      requires: [{ kind: "step" as const, step: "ghost_check", verdict: "pass" }],
    }
    const errs = validateDraft(d, { availableSteps: ["citation_verify"] })
    const stepErr = errs.find(e => e.field === "requires[0].step")
    expect(stepErr).toBeDefined()
    expect(stepErr!.message).toMatch(/not in the catalog/i)
    expect(stepErr!.message).toContain(PREVIEW_PREFIX)
  })

  it("flags a vendor-catalog-but-inactive step with the activate-or-preview hint", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      requires: [{ kind: "step" as const, step: "answer_quality", verdict: "pass" }],
    }
    const errs = validateDraft(d, {
      availableSteps: ["citation_verify"],
      vendorStepSet: ["answer_quality", "claim_citation"],
    })
    const stepErr = errs.find(e => e.field === "requires[0].step")
    expect(stepErr).toBeDefined()
    expect(stepErr!.message).toMatch(/not active/i)
    expect(stepErr!.message).toMatch(/preset|preview:/i)
  })

  it("accepts a preview:-prefixed step even when unwired", () => {
    const d = {
      ...DEFAULT_DRAFT, id: "x",
      requires: [{ kind: "step" as const, step: "preview:my_future_check", verdict: "pass" }],
    }
    const errs = validateDraft(d, {
      availableSteps: ["citation_verify"],
      vendorStepSet: ["answer_quality"],
    })
    expect(errs.filter(e => e.field.startsWith("requires["))).toEqual([])
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
