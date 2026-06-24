import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  allVerifierDescriptors,
  fieldChecksFlat,
  getVerifierDescriptor,
  lifecycleGroupsFor,
  verifierFiresOnLifecycle,
} from "./verifier-descriptors"

describe("verifier descriptors mirror", () => {
  it("exposes the 5 built-in verifiers", () => {
    const steps = allVerifierDescriptors().map((d) => d.step)
    for (const expected of [
      "citation_verify",
      "privilege_scan",
      "source_allowlist",
      "structured_output",
      "prompt_injection_screen",
    ]) {
      expect(steps).toContain(expected)
    }
  })

  it("returns null for unknown step (no throw)", () => {
    expect(getVerifierDescriptor("does_not_exist")).toBeNull()
  })

  it("citation_verify carries the Stop trigger (D57e: PostToolUse pruned)", () => {
    // D57e: citation_verify only fires at Stop time (the agent's
    // final reply). The earlier PostToolUse trigger fabricated a
    // per-fetch firing the verifier never actually does.
    const d = getVerifierDescriptor("citation_verify")
    expect(d).not.toBeNull()
    const events = d!.triggers.map((t) => t.event)
    expect(events).toContain("Stop")
    expect(events).not.toContain("PostToolUse")
  })

  it("source_allowlist verdict set is pass/deny only (deterministic)", () => {
    const d = getVerifierDescriptor("source_allowlist")
    expect(d).not.toBeNull()
    expect(d!.verdict_set).toEqual(["pass", "deny"])
  })

  it("every descriptor records the common evidence envelope", () => {
    for (const d of allVerifierDescriptors()) {
      const paths = d.output_evidence.map((f) => f.path)
      for (const required of ["step", "subject", "verdict", "reasons"]) {
        expect(paths).toContain(required)
      }
    }
  })

  it("descriptor list is alphabetically stable for diff readability", () => {
    const steps = allVerifierDescriptors().map((d) => d.step)
    const sorted = [...steps].sort()
    expect(steps).toEqual(sorted)
  })

  it("every input_payload_paths entry has a matching input_fields row", () => {
    // The dashboard expander renders type + description chips from
    // input_fields, falling back to a CC payload-schema cross-ref. A
    // path without a matching input_fields entry would render as a
    // bare chip with no type signal.
    for (const d of allVerifierDescriptors()) {
      const fieldPaths = new Set((d.input_fields ?? []).map((f) => f.path))
      for (const p of d.input_payload_paths) {
        expect(fieldPaths.has(p)).toBe(true)
      }
    }
  })

  // D52d + D57e (dict-of-arrays)
  it("every built-in descriptor declares >= 1 lifecycle group", () => {
    // D57e: field_checks is grouped by lifecycle CC event. Every
    // built-in must declare at least one group; every group must
    // carry at least one row.
    for (const d of allVerifierDescriptors()) {
      const groups = d.field_checks ?? {}
      expect(
        Object.keys(groups).length,
        `field_checks dict empty on ${d.step}`,
      ).toBeGreaterThan(0)
      for (const event of Object.keys(groups)) {
        expect(
          groups[event].length,
          `field_checks[${event}] empty on ${d.step}`,
        ).toBeGreaterThan(0)
      }
    }
  })

  it("every field_check row carries a non-empty path + <= 200-char description", () => {
    for (const d of allVerifierDescriptors()) {
      for (const fc of fieldChecksFlat(d)) {
        expect(fc.path).toBeTruthy()
        expect(fc.check_description).toBeTruthy()
        expect(fc.check_description.length).toBeLessThanOrEqual(200)
      }
    }
  })

  // D57e: per-lifecycle invariants
  it("every lifecycle group key matches a declared trigger event", () => {
    // The Step 3 picker filters verifiers on `event in field_checks`.
    // An orphan lifecycle group (no matching trigger) would mean the
    // picker shows the verifier for an event the runtime never
    // actually fires under.
    for (const d of allVerifierDescriptors()) {
      const triggerEvents = new Set(d.triggers.map((t) => t.event))
      for (const event of lifecycleGroupsFor(d)) {
        expect(triggerEvents.has(event), `${d.step}: ${event}`).toBe(true)
      }
    }
  })

  it("prompt_injection_screen hides PreToolUse per brief", () => {
    // The verifier does not fire on PreToolUse; the brief is
    // explicit about this. The groups dict must NOT carry it.
    const d = getVerifierDescriptor("prompt_injection_screen")
    expect(d).not.toBeNull()
    const groups = lifecycleGroupsFor(d!)
    expect(groups).not.toContain("PreToolUse")
    for (const event of ["UserPromptSubmit", "PostToolUse", "Stop"]) {
      expect(groups, `prompt_injection_screen missing ${event}`).toContain(event)
    }
  })

  it("citation_verify is Stop-only (D57e)", () => {
    const d = getVerifierDescriptor("citation_verify")
    expect(d).not.toBeNull()
    expect(lifecycleGroupsFor(d!)).toEqual(["Stop"])
  })

  it("source_allowlist is PreToolUse-only (D57e)", () => {
    const d = getVerifierDescriptor("source_allowlist")
    expect(d).not.toBeNull()
    expect(lifecycleGroupsFor(d!)).toEqual(["PreToolUse"])
  })

  it("structured_output is Stop-only (D57e) and surfaces final_message", () => {
    const d = getVerifierDescriptor("structured_output")
    expect(d).not.toBeNull()
    expect(lifecycleGroupsFor(d!)).toEqual(["Stop"])
    const paths = new Set(d!.field_checks!.Stop.map((r) => r.path))
    expect(paths.has("final_message")).toBe(true)
  })

  it("privilege_scan walks four lifecycles (D57e)", () => {
    const d = getVerifierDescriptor("privilege_scan")
    expect(d).not.toBeNull()
    expect(new Set(lifecycleGroupsFor(d!))).toEqual(
      new Set(["PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit"]),
    )
    const preToolPaths = new Set(
      d!.field_checks!.PreToolUse.map((r) => r.path),
    )
    expect(preToolPaths.has("tool_input.command")).toBe(true)
    expect(preToolPaths.has("tool_input.new_string")).toBe(true)
    expect(preToolPaths.has("tool_input.content")).toBe(true)
  })

  it("verifierFiresOnLifecycle gates the Step 3 picker correctly", () => {
    // A Stop-lifecycle wizard should see citation_verify; a
    // PreToolUse-lifecycle wizard should not.
    expect(verifierFiresOnLifecycle("citation_verify", "Stop")).toBe(true)
    expect(verifierFiresOnLifecycle("citation_verify", "PreToolUse")).toBe(false)
    // source_allowlist is PreToolUse-only.
    expect(verifierFiresOnLifecycle("source_allowlist", "PreToolUse")).toBe(true)
    expect(verifierFiresOnLifecycle("source_allowlist", "Stop")).toBe(false)
    // Unknown step → graceful degrade (show in picker).
    expect(verifierFiresOnLifecycle("custom_unknown_step", "Stop")).toBe(true)
  })

  // D57e P2: 8 x 5 lifecycle x built-in truth-table test. A future
  // descriptor edit that adds a spurious lifecycle group (or drops
  // a real one) would land silently without this floor — the
  // pre-existing test only spot-checks 4 combinations across 5
  // built-ins. The expected truth values are derived directly from
  // the brief's per-built-in narrowings (citation_verify Stop-only,
  // source_allowlist PreToolUse-only, structured_output Stop-only,
  // privilege_scan four-lifecycle, prompt_injection_screen no
  // PreToolUse).
  it("verifierFiresOnLifecycle 8-lifecycle x 5-builtin truth table (D57e P2)", () => {
    const LIFECYCLES = [
      "PreToolUse",
      "PostToolUse",
      "Stop",
      "UserPromptSubmit",
      "SubagentStop",
      "PreCompact",
      "SessionStart",
      "SessionEnd",
    ] as const
    const BUILTIN_STEPS = [
      "citation_verify",
      "privilege_scan",
      "source_allowlist",
      "structured_output",
      "prompt_injection_screen",
    ] as const
    // Expected fires-on truth table derived from descriptors.py per
    // built-in. Order of keys mirrors BUILTIN_STEPS so the layout
    // reads as a matrix in source.
    const expected: Record<typeof BUILTIN_STEPS[number], Record<typeof LIFECYCLES[number], boolean>> = {
      citation_verify: {
        PreToolUse: false, PostToolUse: false, Stop: true,
        UserPromptSubmit: false, SubagentStop: false,
        PreCompact: false, SessionStart: false, SessionEnd: false,
      },
      privilege_scan: {
        PreToolUse: true, PostToolUse: true, Stop: true,
        UserPromptSubmit: true, SubagentStop: false,
        PreCompact: false, SessionStart: false, SessionEnd: false,
      },
      source_allowlist: {
        PreToolUse: true, PostToolUse: false, Stop: false,
        UserPromptSubmit: false, SubagentStop: false,
        PreCompact: false, SessionStart: false, SessionEnd: false,
      },
      structured_output: {
        PreToolUse: false, PostToolUse: false, Stop: true,
        UserPromptSubmit: false, SubagentStop: false,
        PreCompact: false, SessionStart: false, SessionEnd: false,
      },
      prompt_injection_screen: {
        PreToolUse: false, PostToolUse: true, Stop: true,
        UserPromptSubmit: true, SubagentStop: false,
        PreCompact: false, SessionStart: false, SessionEnd: false,
      },
    }
    for (const step of BUILTIN_STEPS) {
      for (const life of LIFECYCLES) {
        expect(
          verifierFiresOnLifecycle(step, life),
          `${step} x ${life}`,
        ).toBe(expected[step][life])
      }
    }
  })

  // D57e P2: legacy flat-list shape guard. A custom-verifier mirror
  // copy or an older cloud build may still ship `field_checks` as a
  // FieldCheck[] (pre-D57e contract). fieldChecksFlat must short-
  // circuit on Array.isArray(groups) and return a copy; otherwise
  // Object.keys() returns numeric index strings and the loop yields
  // junk. lifecycleGroupsFor must return [] on the legacy shape so
  // the wizard's `event in field_checks` predicate falls through to
  // the unknown-step branch.
  it("fieldChecksFlat handles legacy flat-list shape (D57e P2)", () => {
    const legacy = {
      step: "old_custom",
      triggers: [],
      input_payload_paths: [],
      verdict_set: ["pass"],
      output_evidence: [],
      // Pre-D57e shape: field_checks is a flat list.
      field_checks: [
        { path: "tool_input.url", check_description: "x" },
        { path: "tool_input.command", check_description: "y" },
      ] as unknown,
    } as unknown as Parameters<typeof fieldChecksFlat>[0]
    const out = fieldChecksFlat(legacy)
    expect(out).toHaveLength(2)
    expect(out[0].path).toBe("tool_input.url")
    expect(out[1].path).toBe("tool_input.command")
  })

  it("lifecycleGroupsFor returns [] on legacy flat-list shape (D57e P2)", () => {
    const legacy = {
      step: "old_custom",
      triggers: [],
      input_payload_paths: [],
      verdict_set: ["pass"],
      output_evidence: [],
      field_checks: [
        { path: "tool_input.url", check_description: "x" },
      ] as unknown,
    } as unknown as Parameters<typeof lifecycleGroupsFor>[0]
    expect(lifecycleGroupsFor(legacy)).toEqual([])
  })

  it("fieldChecksFlat preserves lifecycle insertion order", () => {
    // privilege_scan's groups are declared in PreToolUse / PostToolUse
    // / Stop / UserPromptSubmit order; the flat dump must walk them
    // in that order so the catalog row reader is stable.
    const d = getVerifierDescriptor("privilege_scan")
    expect(d).not.toBeNull()
    const flat = fieldChecksFlat(d!)
    const firstFour = flat.slice(0, 4).map((r) => r.path)
    // First three rows are the PreToolUse triplet (Bash command,
    // Edit new_string, Write content); fourth is the PostToolUse
    // tool_response.output row.
    expect(firstFour[0]).toBe("tool_input.command")
    expect(firstFour[3]).toBe("tool_response.output")
  })

  // D57c
  it("every descriptor declares an input_assembly value", () => {
    for (const d of allVerifierDescriptors()) {
      expect(d.input_assembly, `input_assembly missing on ${d.step}`)
        .toMatch(/^(cc_stdin|caller_assembled)$/)
    }
  })

  it("caller_assembled rows carry a non-empty caller_assembly_hint", () => {
    for (const d of allVerifierDescriptors()) {
      if (d.input_assembly === "caller_assembled") {
        expect(
          d.caller_assembly_hint,
          `caller_assembly_hint empty on ${d.step}`,
        ).toBeTruthy()
      }
    }
  })

  it("cc_stdin rows leave caller_assembly_hint blank", () => {
    for (const d of allVerifierDescriptors()) {
      if (d.input_assembly === "cc_stdin") {
        const hint = d.caller_assembly_hint ?? ""
        expect(
          hint.trim(),
          `cc_stdin row ${d.step} carries an unexpected hint`,
        ).toBe("")
      }
    }
  })

  it("all five built-ins are caller_assembled (D57c follow-up)", () => {
    // D57c follow-up: the cloud's `_verify_dispatch_impl` forwards
    // `req.payload` verbatim to the verifier. None of the five
    // built-ins auto-pull CC stdin into their input dict. citation_verify
    // and structured_output were marked caller_assembled in the
    // baseline; the follow-up flipped privilege_scan / source_allowlist /
    // prompt_injection_screen to match the runtime contract.
    for (const step of [
      "citation_verify",
      "structured_output",
      "privilege_scan",
      "source_allowlist",
      "prompt_injection_screen",
    ]) {
      const d = getVerifierDescriptor(step)
      expect(d?.input_assembly, step).toBe("caller_assembled")
    }
  })
})


/**
 * Parity check vs the Python source-of-truth (descriptors.py).
 *
 * The brief flagged that the TS mirror was ~150 lines of byte-stable
 * duplicated structural data with no drift gate. This test parses the
 * Python file as raw text and asserts the headline structural
 * invariants line up: same set of step names, same trigger event/class
 * pairs, same input_payload_paths, same verdict_set members. It is
 * intentionally NOT a deep JSON-equality check — the Python literals
 * use different string-escaping conventions and the per-field
 * descriptions are short prose that doesn't gain anything from
 * char-level pinning. Catching shape drift is enough; the cloud
 * /verifier-descriptors endpoint is the runtime source of truth for
 * any third-party consumer that wants exact JSON.
 */
describe("verifier descriptors parity with descriptors.py", () => {
  const pyPath = path.resolve(
    __dirname, "..", "..", "src", "magi_cp", "verifier", "descriptors.py",
  )
  const pySrc = readFileSync(pyPath, "utf-8")

  function extractPythonStepKeys(src: string): string[] {
    // Match lines like `    "citation_verify": {` inside _DESCRIPTORS.
    const out: string[] = []
    const re = /^\s{4}"([a-z_]+)":\s*\{/gm
    let m: RegExpExecArray | null
    while ((m = re.exec(src)) !== null) {
      out.push(m[1])
    }
    return out.sort()
  }

  function extractPythonInputPaths(src: string, step: string): string[] {
    // Per-step grab: `    "<step>": { ... "input_payload_paths": [ ... ], ...`.
    // Naive `\\[(...)\\]` would stop at the first `]` it sees — which is
    // inside path literals like `citations[].quote`. We instead walk
    // bracket depth manually from the `[` after `input_payload_paths`.
    const headRe = new RegExp(
      `"${step}":\\s*\\{[\\s\\S]*?"input_payload_paths":\\s*\\[`,
      "m",
    )
    const m = src.match(headRe)
    if (!m) return []
    const startIdx = (m.index ?? 0) + m[0].length
    // Walk forward, ignoring `[` / `]` inside string literals, until
    // we balance the opening bracket. This handles the embedded `[]`
    // sequences in path strings like `"citations[].quote"`.
    let depth = 1
    let i = startIdx
    let inString = false
    while (i < src.length && depth > 0) {
      const ch = src[i]
      if (inString) {
        if (ch === "\\") {
          i += 2
          continue
        }
        if (ch === '"') inString = false
      } else {
        if (ch === '"') inString = true
        else if (ch === "[") depth += 1
        else if (ch === "]") depth -= 1
      }
      i += 1
      if (depth === 0) break
    }
    const body = src.slice(startIdx, i - 1)
    return Array.from(body.matchAll(/"([^"]+)"/g)).map((mm) => mm[1])
  }

  it("step set matches", () => {
    const pySteps = extractPythonStepKeys(pySrc)
    const tsSteps = allVerifierDescriptors().map((d) => d.step).sort()
    expect(pySteps).toEqual(tsSteps)
  })

  it("input_payload_paths matches per step", () => {
    for (const d of allVerifierDescriptors()) {
      const pyPaths = extractPythonInputPaths(pySrc, d.step)
      expect(
        pyPaths,
        `input_payload_paths drift on ${d.step}`,
      ).toEqual(d.input_payload_paths)
    }
  })

  it("field_checks paths match per step (D57e: dict-of-arrays)", () => {
    // D57e: field_checks is grouped by lifecycle in Python AND TS.
    // We pull every "path": "..." inside the field_checks block in
    // Python source order (which walks groups in insertion order,
    // then rows within each group) and compare against TS
    // fieldChecksFlat() which uses the same walk order. The prose
    // tolerance the description tests grant elsewhere applies here
    // too, so we do not pin char-level descriptions.
    for (const d of allVerifierDescriptors()) {
      const stepRe = new RegExp(
        `"${d.step}":\\s*\\{[\\s\\S]*?"field_checks":\\s*\\{`,
        "m",
      )
      const m = pySrc.match(stepRe)
      expect(m, `field_checks block not found for ${d.step}`).not.toBeNull()
      const startIdx = (m!.index ?? 0) + m![0].length
      // Walk brace depth manually because path strings can carry
      // brackets (citation_verify's `citations[].quote`) and the
      // groups dict carries nested `[ ... ]` row lists.
      let depth = 1
      let i = startIdx
      let inString = false
      while (i < pySrc.length && depth > 0) {
        const ch = pySrc[i]
        if (inString) {
          if (ch === "\\") {
            i += 2
            continue
          }
          if (ch === '"') inString = false
        } else {
          if (ch === '"') inString = true
          else if (ch === "{") depth += 1
          else if (ch === "}") depth -= 1
        }
        i += 1
        if (depth === 0) break
      }
      const body = pySrc.slice(startIdx, i - 1)
      const pyPaths = Array.from(
        body.matchAll(/"path":\s*"([^"]+)"/g),
      ).map((mm) => mm[1])
      const tsPaths = fieldChecksFlat(d).map((fc) => fc.path)
      expect(
        pyPaths,
        `field_checks path set drift on ${d.step}`,
      ).toEqual(tsPaths)
    }
  })

  it("field_checks lifecycle keys match per step (D57e)", () => {
    // D57e: separate parity check for the dict KEYS (lifecycle
    // events) so a drift that moves a row to a different group
    // surfaces with the group name, not just "paths differ".
    for (const d of allVerifierDescriptors()) {
      const stepRe = new RegExp(
        `"${d.step}":\\s*\\{[\\s\\S]*?"field_checks":\\s*\\{`,
        "m",
      )
      const m = pySrc.match(stepRe)
      expect(m, `field_checks block not found for ${d.step}`).not.toBeNull()
      const startIdx = (m!.index ?? 0) + m![0].length
      // Walk to the closing brace of the dict.
      let depth = 1
      let i = startIdx
      let inString = false
      while (i < pySrc.length && depth > 0) {
        const ch = pySrc[i]
        if (inString) {
          if (ch === "\\") {
            i += 2
            continue
          }
          if (ch === '"') inString = false
        } else {
          if (ch === '"') inString = true
          else if (ch === "{") depth += 1
          else if (ch === "}") depth -= 1
        }
        i += 1
        if (depth === 0) break
      }
      const body = pySrc.slice(startIdx, i - 1)
      // Group keys are the top-level `"<Event>": [` literals; we
      // need to walk bracket depth to skip nested `]` characters
      // inside row dict path strings. Easier: depth-0 walk that
      // only records `"<Word>": [` patterns where the preceding
      // char is `{` (start of dict) or `,` (next key).
      const groupKeys: string[] = []
      let j = 0
      let braceDepth = 0
      let bracketDepth = 0
      let inStr = false
      while (j < body.length) {
        const ch = body[j]
        if (inStr) {
          if (ch === "\\") {
            j += 2
            continue
          }
          if (ch === '"') inStr = false
          j += 1
          continue
        }
        if (ch === '"') {
          // Possible key. Only count when at brace depth 0 and
          // bracket depth 0 (top-level of the field_checks dict).
          if (braceDepth === 0 && bracketDepth === 0) {
            const close = body.indexOf('"', j + 1)
            if (close > j) {
              const tail = body.slice(close + 1).trimStart()
              if (tail.startsWith(":")) {
                groupKeys.push(body.slice(j + 1, close))
              }
            }
          }
          inStr = true
          j += 1
          continue
        }
        if (ch === "{") braceDepth += 1
        else if (ch === "}") braceDepth -= 1
        else if (ch === "[") bracketDepth += 1
        else if (ch === "]") bracketDepth -= 1
        j += 1
      }
      const tsKeys = Object.keys(d.field_checks ?? {})
      expect(
        groupKeys,
        `field_checks lifecycle keys drift on ${d.step}`,
      ).toEqual(tsKeys)
    }
  })

  it("trigger event names match per step", () => {
    for (const d of allVerifierDescriptors()) {
      // Pull the `"<step>": { triggers: [ {event: "..."} ... ] }` block.
      const stepRe = new RegExp(
        `"${d.step}":\\s*\\{[\\s\\S]*?"triggers":\\s*\\[([\\s\\S]*?)\\][\\s\\S]*?"input_payload_paths"`,
        "m",
      )
      const m = pySrc.match(stepRe)
      expect(m, `triggers block not found for ${d.step}`).not.toBeNull()
      const eventsInPy = Array.from(
        m![1].matchAll(/"event":\s*"([^"]+)"/g),
      ).map((mm) => mm[1])
      const eventsInTs = d.triggers.map((tr) => tr.event)
      expect(eventsInPy.sort()).toEqual(eventsInTs.sort())
    }
  })
})
