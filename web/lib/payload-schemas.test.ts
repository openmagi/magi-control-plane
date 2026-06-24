import { describe, it, expect } from "vitest"
import {
  availableFields,
  availableFieldPaths,
  allSchemas,
  lifecycleToEvent,
  lintShaclTargets,
  MAGI_HOOK_NS,
} from "./payload-schemas"

describe("availableFields — tool-context resolution", () => {
  it("exposes tool_input.command for PreToolUse + Bash", () => {
    const paths = availableFieldPaths("PreToolUse", "Bash")
    expect(paths).toContain("tool_input.command")
  })

  it("exposes tool_input.url for PreToolUse + WebFetch", () => {
    const paths = availableFieldPaths("PreToolUse", "WebFetch")
    expect(paths).toContain("tool_input.url")
  })

  it("exposes tool_input.file_path for Edit + Write + Read", () => {
    for (const t of ["Edit", "Write", "Read"] as const) {
      const paths = availableFieldPaths("PreToolUse", t)
      expect(paths).toContain("tool_input.file_path")
    }
  })

  it("falls back to generic tool_input for wildcard matcher", () => {
    const paths = availableFieldPaths("PreToolUse", "*")
    expect(paths).toContain("tool_input")
    // Bash-specific must NOT leak under wildcard — that's the very
    // vacuous-satisfaction failure mode this menu exists to prevent.
    expect(paths).not.toContain("tool_input.command")
  })

  it("falls back to generic for alternation matcher", () => {
    const paths = availableFieldPaths("PreToolUse", "Bash|Edit")
    expect(paths).toContain("tool_input")
    expect(paths).not.toContain("tool_input.command")
  })

  it("falls back to generic for mcp tool matcher", () => {
    const paths = availableFieldPaths("PreToolUse", "mcp__court__file")
    expect(paths).toContain("tool_input")
    expect(paths).not.toContain("tool_input.command")
  })
})

describe("availableFields — PostToolUse", () => {
  it("exposes tool_response.output", () => {
    const paths = availableFieldPaths("PostToolUse", "Bash")
    expect(paths).toContain("tool_response.output")
    expect(paths).toContain("tool_response.is_error")
  })

  it("still carries the common envelope", () => {
    const paths = availableFieldPaths("PostToolUse", "Bash")
    expect(paths).toContain("session_id")
    expect(paths).toContain("tool_use_id")
  })
})

describe("availableFields — final / no_tool events", () => {
  it("Stop exposes final_message + transcript_path", () => {
    const paths = availableFieldPaths("Stop")
    expect(paths).toContain("final_message")
    expect(paths).toContain("transcript_path")
  })

  it("UserPromptSubmit exposes prompt", () => {
    const paths = availableFieldPaths("UserPromptSubmit")
    expect(paths).toContain("prompt")
  })

  it("SessionStart exposes cwd", () => {
    const paths = availableFieldPaths("SessionStart")
    expect(paths).toContain("cwd")
  })
})

describe("availableFields — error / unknown handling", () => {
  it("returns [] for unknown event so chip row hides", () => {
    expect(availableFields("BogusEvent", "Bash")).toEqual([])
    expect(availableFields("BogusEvent")).toEqual([])
  })

  it("returns FieldDescriptors with description + type", () => {
    const fields = availableFields("PreToolUse", "Bash")
    for (const f of fields) {
      expect(f.description.length).toBeGreaterThan(0)
      expect(["str", "int", "bool", "list", "dict"]).toContain(f.type)
    }
  })
})

describe("allSchemas", () => {
  it("includes at least PreToolUse, PostToolUse, Stop", () => {
    const events = new Set(allSchemas().map((s) => s.event))
    expect(events.has("PreToolUse")).toBe(true)
    expect(events.has("PostToolUse")).toBe(true)
    expect(events.has("Stop")).toBe(true)
  })

  it("every entry has at least one field", () => {
    for (const s of allSchemas()) {
      expect(s.fields.length).toBeGreaterThan(0)
    }
  })
})

describe("lifecycleToEvent", () => {
  it("maps wizard lifecycles to CC events", () => {
    expect(lifecycleToEvent("before_tool_use")).toBe("PreToolUse")
    expect(lifecycleToEvent("after_tool_use")).toBe("PostToolUse")
    expect(lifecycleToEvent("pre_final")).toBe("Stop")
  })
})

describe("P7: Edit / Write split (issue #1, P2)", () => {
  it("Write does not expose Edit-only fields", () => {
    const paths = availableFieldPaths("PreToolUse", "Write")
    expect(paths).toContain("tool_input.file_path")
    expect(paths).toContain("tool_input.content")
    expect(paths).not.toContain("tool_input.old_string")
    expect(paths).not.toContain("tool_input.new_string")
  })

  it("Edit does not expose Write-only content field", () => {
    const paths = availableFieldPaths("PreToolUse", "Edit")
    expect(paths).toContain("tool_input.file_path")
    expect(paths).toContain("tool_input.old_string")
    expect(paths).toContain("tool_input.new_string")
    expect(paths).not.toContain("tool_input.content")
  })
})

describe("P7: sh_datatype + sh_kind hints (issue #1, P1 #5)", () => {
  it("string field carries xsd:string + property kind", () => {
    const fields = availableFields("PreToolUse", "Bash")
    const cmd = fields.find((f) => f.path === "tool_input.command")
    expect(cmd?.sh_datatype).toBe("xsd:string")
    expect(cmd?.sh_kind).toBe("property")
  })

  it("int field carries xsd:integer", () => {
    const fields = availableFields("PreToolUse", "Bash")
    const t = fields.find((f) => f.path === "tool_input.timeout")
    expect(t?.sh_datatype).toBe("xsd:integer")
  })

  it("generic dict tool_input carries rdf:JSON + node kind", () => {
    const fields = availableFields("PreToolUse", "*")
    const t = fields.find((f) => f.path === "tool_input")
    expect(t?.sh_datatype).toBe("rdf:JSON")
    expect(t?.sh_kind).toBe("node")
  })
})

describe("P7: MAGI_HOOK_NS constant matches Python", () => {
  it("namespace matches the runtime contract", () => {
    expect(MAGI_HOOK_NS).toBe("https://magi.openmagi.ai/cc/hook#")
  })
})

describe("P7: lintShaclTargets (issue #1, P0 #3 / P1 #4)", () => {
  const shape = (parts: { tn?: string; tc?: string; path?: string }) => {
    const body: string[] = []
    if (parts.tn) body.push(`sh:targetNode magi:${parts.tn}`)
    if (parts.tc) body.push(`sh:targetClass magi:${parts.tc}`)
    if (parts.path) body.push(`sh:path magi:${parts.path}`)
    body.push("sh:minCount 1")
    return [
      "@prefix sh:   <http://www.w3.org/ns/shacl#> .",
      `@prefix magi: <${MAGI_HOOK_NS}> .`,
      `[] ${body.join(" ; ")} .`,
    ].join("\n")
  }

  it("passes when sh:path is in the menu", () => {
    expect(
      lintShaclTargets(shape({ path: "tool_input.command" }), "PreToolUse", "Bash"),
    ).toEqual([])
  })

  it("flags unknown sh:path", () => {
    const issues = lintShaclTargets(
      shape({ path: "tool_input.bogus" }),
      "PreToolUse", "Bash",
    )
    expect(issues.length).toBe(1)
    expect(issues[0]).toContain("magi:tool_input.bogus")
  })

  it("flags unknown sh:targetNode", () => {
    const issues = lintShaclTargets(
      shape({ tn: "tool_input.bogus" }),
      "PreToolUse", "Bash",
    )
    expect(issues.length).toBe(1)
    expect(issues[0]).toContain("magi:tool_input.bogus")
  })

  it("accepts sh:targetClass magi:Hook", () => {
    expect(
      lintShaclTargets(shape({ tc: "Hook" }), "PreToolUse", "Bash"),
    ).toEqual([])
  })

  it("flags unknown sh:targetClass", () => {
    const issues = lintShaclTargets(
      shape({ tc: "BogusType" }),
      "PreToolUse", "Bash",
    )
    expect(issues.length).toBe(1)
    expect(issues[0]).toContain("BogusType")
  })

  it("suggests close match for one-character typo", () => {
    const issues = lintShaclTargets(
      shape({ path: "tool_input.commandz" }),
      "PreToolUse", "Bash",
    )
    expect(issues[0]).toContain("did you mean")
  })

  it("accepts the canonical hook subject anchor", () => {
    expect(
      lintShaclTargets(shape({ tn: "__hook__" }), "PreToolUse", "Bash"),
    ).toEqual([])
  })

  it("ignores anchors outside the magi: namespace", () => {
    const ttl = [
      "@prefix sh: <http://www.w3.org/ns/shacl#> .",
      "@prefix ex: <http://example.com/> .",
      "[] sh:targetClass ex:Filing ; sh:minCount 1 .",
    ].join("\n")
    expect(lintShaclTargets(ttl, "PreToolUse", "Bash")).toEqual([])
  })

  it("accepts absolute IRI form of canonical namespace", () => {
    const ttl = [
      "@prefix sh: <http://www.w3.org/ns/shacl#> .",
      `[] sh:path <${MAGI_HOOK_NS}tool_input.command> ; sh:minCount 1 .`,
    ].join("\n")
    expect(lintShaclTargets(ttl, "PreToolUse", "Bash")).toEqual([])
  })
})

describe("no duplicate paths in resolved view", () => {
  // The Python registry has the same invariant; we mirror it here so
  // a drift between TS and Python on one side fails the test on the
  // other.
  it("PreToolUse+Bash has unique paths", () => {
    const paths = availableFieldPaths("PreToolUse", "Bash")
    expect(new Set(paths).size).toBe(paths.length)
  })

  it("PostToolUse+Bash has unique paths", () => {
    const paths = availableFieldPaths("PostToolUse", "Bash")
    expect(new Set(paths).size).toBe(paths.length)
  })
})
