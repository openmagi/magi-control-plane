import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  allVerifierDescriptors,
  getVerifierDescriptor,
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

  it("citation_verify carries Stop + PostToolUse triggers", () => {
    const d = getVerifierDescriptor("citation_verify")
    expect(d).not.toBeNull()
    const events = d!.triggers.map((t) => t.event)
    expect(events).toContain("Stop")
    expect(events).toContain("PostToolUse")
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

  // D52d
  it("every built-in descriptor declares >= 1 field_check row", () => {
    for (const d of allVerifierDescriptors()) {
      const fcs = d.field_checks ?? []
      expect(fcs.length, `field_checks empty on ${d.step}`).toBeGreaterThan(0)
    }
  })

  it("every field_check row carries a non-empty path + <= 200-char description", () => {
    for (const d of allVerifierDescriptors()) {
      for (const fc of d.field_checks ?? []) {
        expect(fc.path).toBeTruthy()
        expect(fc.check_description).toBeTruthy()
        expect(fc.check_description.length).toBeLessThanOrEqual(200)
      }
    }
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

  it("field_checks paths match per step", () => {
    // D52d follow-up: extend the parity gate to cover field_checks.
    // The TS and Python sides shipped ~5 field_checks rows each with
    // no drift gate; this test would catch a future edit that
    // updates one side without the other. We assert the PATH set per
    // step (path strings are short and stable); the prose tolerance
    // the description tests grant elsewhere applies here too, so we
    // do not pin char-level descriptions.
    for (const d of allVerifierDescriptors()) {
      const stepRe = new RegExp(
        `"${d.step}":\\s*\\{[\\s\\S]*?"field_checks":\\s*\\[`,
        "m",
      )
      const m = pySrc.match(stepRe)
      expect(m, `field_checks block not found for ${d.step}`).not.toBeNull()
      const startIdx = (m!.index ?? 0) + m![0].length
      // Walk bracket depth manually because path strings can carry
      // `[]` literals (citation_verify's `citations[].quote`).
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
          else if (ch === "[") depth += 1
          else if (ch === "]") depth -= 1
        }
        i += 1
        if (depth === 0) break
      }
      const body = pySrc.slice(startIdx, i - 1)
      // Each row is a dict literal with `"path": "..."`. We only need
      // the path strings; pull them in source order.
      const pyPaths = Array.from(
        body.matchAll(/"path":\s*"([^"]+)"/g),
      ).map((mm) => mm[1])
      const tsPaths = (d.field_checks ?? []).map((fc) => fc.path)
      expect(
        pyPaths,
        `field_checks path set drift on ${d.step}`,
      ).toEqual(tsPaths)
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
