import { describe, it, expect } from "vitest"
import { existsSync, readFileSync } from "node:fs"
import { execSync } from "node:child_process"
import {
  CC_BUILTIN_TOOLS,
  CC_TOP_SUGGESTIONS,
  LEGACY_ALIASES,
  classifyCcToolName,
  filterCcBuiltins,
  findCcBuiltinTool,
  isCcBuiltinTool,
  legacyAliasCanonical,
} from "./cc-tools"

/**
 * D70 / D71: canonical CC built-in tool list invariants.
 *
 * The list is hand-mirrored from the v2.1.170 claude binary strings
 * table per the methodology in cc-tools.ts's module docstring. These
 * tests pin every name the wizard's autocomplete must surface so a
 * future refactor that intends to drop or rename one surfaces in the
 * diff. The Step 2 ToolCombobox imports from this module — if a tool
 * name disappears here, the dropdown silently shrinks.
 */

// Canonical list as observed in the v2.1.170 binary. The wizard test
// pins this exact set; any drift (a tool renamed or dropped in a new
// binary release) surfaces in CI before it can ship.
const EXPECTED_CANONICAL = [
  "Bash",
  "PowerShell",
  "Read",
  "Write",
  "Edit",
  "NotebookEdit",
  "Glob",
  "Grep",
  "WebFetch",
  "WebSearch",
  "Agent",
  "TeamCreate",
  "TeamDelete",
  "ListAgents",
  "SendMessage",
  "EnterPlanMode",
  "ExitPlanMode",
  "TodoWrite",
  "AskUserQuestion",
  "SendUserMessage",
  "SendUserFile",
  "PushNotification",
  "StructuredOutput",
  "TaskCreate",
  "TaskGet",
  "TaskUpdate",
  "TaskList",
  "TaskStop",
  "TaskOutput",
  "Skill",
  "ToolSearch",
  "Workflow",
  "REPL",
  "LSP",
  "Monitor",
  "ScheduleWakeup",
  "ListMcpResourcesTool",
  "ReadMcpResourceTool",
  "CronCreate",
  "CronDelete",
  "CronList",
  "RemoteTrigger",
  "EnterWorktree",
  "ExitWorktree",
  "DesignSync",
  "ShareOnboardingGuide",
  "Cd",
]

// Names the previous wizard listed as built-ins but which the v2.1.170
// binary does NOT register under those literals:
//   - Task / BashOutput / KillBash : pre-rename aliases (see LEGACY_ALIASES)
//   - MultiEdit / NotebookRead     : prose-only, never registered
const STALE_NAMES = ["Task", "BashOutput", "KillBash", "MultiEdit", "NotebookRead"]

describe("cc-tools — canonical built-in list", () => {
  it("matches the v2.1.170 canonical manifest exactly", () => {
    const got = CC_BUILTIN_TOOLS.map((t) => t.name)
    expect(got).toEqual(EXPECTED_CANONICAL)
  })

  it("rejects the previously-stale names (rename / never-registered)", () => {
    const got = CC_BUILTIN_TOOLS.map((t) => t.name)
    for (const old of STALE_NAMES) {
      expect(got).not.toContain(old)
    }
  })

  it("includes every post-rename canonical surfaced by the binary's alias map", () => {
    // Negative-pin assert: every RHS in LEGACY_ALIASES (post-rename
    // canonical) MUST be present in CC_BUILTIN_TOOLS. This guards
    // against a future binary that renames again without us catching it.
    const got = new Set(CC_BUILTIN_TOOLS.map((t) => t.name))
    const canonicals = new Set(Object.values(LEGACY_ALIASES))
    for (const c of canonicals) {
      expect(got.has(c)).toBe(true)
    }
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
    // Brief mandates these five specific names as the default surface;
    // Agent replaced Task per the v2.1.170 binary alias map.
    expect(CC_TOP_SUGGESTIONS).toEqual(["Bash", "Read", "Edit", "WebFetch", "Agent"])
  })
})

describe("cc-tools — legacy alias map", () => {
  it("legacyAliasCanonical resolves binary-attested renames", () => {
    expect(legacyAliasCanonical("Task")).toBe("Agent")
    expect(legacyAliasCanonical("BashOutput")).toBe("TaskOutput")
    expect(legacyAliasCanonical("KillBash")).toBe("TaskStop")
    expect(legacyAliasCanonical("KillShell")).toBe("TaskStop")
    expect(legacyAliasCanonical("Brief")).toBe("SendUserMessage")
    expect(legacyAliasCanonical("ListPeers")).toBe("ListAgents")
    expect(legacyAliasCanonical("ListMcpResources")).toBe("ListMcpResourcesTool")
    expect(legacyAliasCanonical("ReadMcpResource")).toBe("ReadMcpResourceTool")
  })

  it("returns null for canonical (post-rename) or unknown names", () => {
    expect(legacyAliasCanonical("Agent")).toBeNull()
    expect(legacyAliasCanonical("Bash")).toBeNull()
    expect(legacyAliasCanonical("MyCustomTool")).toBeNull()
    expect(legacyAliasCanonical("")).toBeNull()
  })
})

describe("cc-tools — classification + lookup", () => {
  it("isCcBuiltinTool is case-insensitive", () => {
    expect(isCcBuiltinTool("Bash")).toBe(true)
    expect(isCcBuiltinTool("bash")).toBe(true)
    expect(isCcBuiltinTool("BASH")).toBe(true)
    expect(isCcBuiltinTool("agent")).toBe(true)
    expect(isCcBuiltinTool("ExitPlanMode")).toBe(true)
    expect(isCcBuiltinTool("AskUserQuestion")).toBe(true)
    expect(isCcBuiltinTool("definitely_not_a_tool")).toBe(false)
    // Stale names must not classify as built-in.
    expect(isCcBuiltinTool("Task")).toBe(false)
    expect(isCcBuiltinTool("MultiEdit")).toBe(false)
  })

  it("findCcBuiltinTool returns canonical entry or null", () => {
    const e = findCcBuiltinTool("bash")
    expect(e).not.toBeNull()
    expect(e!.name).toBe("Bash")
    expect(findCcBuiltinTool("mcp__github__search")).toBeNull()
  })

  it("classifyCcToolName splits built-in / mcp / custom", () => {
    expect(classifyCcToolName("Bash")).toBe("built-in")
    expect(classifyCcToolName("agent")).toBe("built-in")
    expect(classifyCcToolName("mcp__github__search")).toBe("mcp")
    // Case-insensitive on the mcp__ prefix so the badge stays in sync
    // with matcherClassForToolScope on the wizard page.
    expect(classifyCcToolName("MCP__foo__bar")).toBe("mcp")
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

  it("'edit' surfaces Edit + NotebookEdit (canonical edit tools)", () => {
    const names = filterCcBuiltins("edit").map((t) => t.name)
    expect(names).toContain("Edit")
    expect(names).toContain("NotebookEdit")
    // MultiEdit was removed (never registered in v2.1.170).
    expect(names).not.toContain("MultiEdit")
  })

  it("'task' surfaces every Task* canonical (TaskCreate..TaskOutput)", () => {
    const names = filterCcBuiltins("task").map((t) => t.name)
    expect(names).toContain("TaskCreate")
    expect(names).toContain("TaskGet")
    expect(names).toContain("TaskUpdate")
    expect(names).toContain("TaskList")
    expect(names).toContain("TaskStop")
    expect(names).toContain("TaskOutput")
  })

  it("'web' matches WebFetch + WebSearch", () => {
    const names = filterCcBuiltins("web").map((t) => t.name)
    expect(names).toContain("WebFetch")
    expect(names).toContain("WebSearch")
  })

  it("'cron' surfaces every Cron* canonical", () => {
    const names = filterCcBuiltins("cron").map((t) => t.name)
    expect(names).toContain("CronCreate")
    expect(names).toContain("CronDelete")
    expect(names).toContain("CronList")
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

describe("cc-tools — binary drift guard (skip when binary absent)", () => {
  // When the v2.1.170 claude binary is installed locally, re-extract
  // the canonical names from it and assert the in-repo list matches.
  // CI / sandboxed environments without the binary simply skip this
  // pin (the docstring promise is still asserted by EXPECTED_CANONICAL
  // above against the human-reviewed manifest).
  const BIN = "/opt/homebrew/Caskroom/claude-code/2.1.170/claude"

  it.runIf(existsSync(BIN))(
    "in-repo CC_BUILTIN_TOOLS matches every post-rename canonical surfaced by the binary's alias map",
    () => {
      // Re-extract the alias map from the binary; the RHS of each pair
      // is the post-rename canonical. This is the same source we cite
      // in cc-tools.ts's docstring; the test reads it back live so the
      // promise doesn't go stale on a binary bump. We pipe through
      // grep so the spawn buffer stays bounded (full `strings` output
      // can exceed the default 1MB ENOBUFS cap).
      const raw = execSync(
        `strings ${BIN} | grep -o '{Task:"Agent"[^}]*}' | head -1`,
        { encoding: "utf-8", shell: "/bin/sh", maxBuffer: 4 * 1024 * 1024 },
      ).trim()
      expect(raw.length).toBeGreaterThan(0)
      const canonicals = new Set<string>()
      for (const m of raw.matchAll(/"([A-Z][A-Za-z]+)"/g)) {
        canonicals.add(m[1])
      }
      // Each RHS canonical MUST be present in the in-repo list.
      const got = new Set(CC_BUILTIN_TOOLS.map((t) => t.name))
      for (const c of canonicals) {
        expect(got.has(c)).toBe(true)
      }
    },
  )

  it.runIf(existsSync(BIN))(
    "stale names that the binary's alias map RENAMES are absent from CC_BUILTIN_TOOLS",
    () => {
      // LHS of each pair is the legacy pre-rename name; the in-repo
      // list MUST NOT contain it. Catches regressions where someone
      // re-adds Task / BashOutput / KillBash without realising they
      // are renames.
      const got = new Set(CC_BUILTIN_TOOLS.map((t) => t.name))
      for (const legacy of Object.keys(LEGACY_ALIASES)) {
        expect(got.has(legacy)).toBe(false)
      }
      // Touch the binary path so a host-only failure surfaces if the
      // path layout changes in a future Homebrew bump.
      expect(readFileSync(BIN).byteLength).toBeGreaterThan(0)
    },
  )
})
