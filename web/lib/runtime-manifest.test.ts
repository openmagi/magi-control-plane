import { describe, it, expect } from "vitest"
import { readFileSync, existsSync } from "node:fs"
import path from "node:path"
import {
  LEDGER_VERDICTS_ORDERED,
  HOOK_EVENTS_ALL,
  CONTEXT_INJECTION_EXCLUDED_EVENTS,
  CONTEXT_INJECTION_ALTERNATE_CHANNEL,
  REWRITER_KINDS,
  RUN_COMMAND_RUNTIMES,
  RUN_COMMAND_TIMEOUT_DEFAULT_MS,
  RUN_COMMAND_TIMEOUT_MAX_MS,
  CONTEXT_ACCEPTING_EVENT_COUNT,
} from "./runtime-manifest"

/**
 * D78 review fix: runtime-manifest is a TS mirror of canonical Python
 * constants used by /docs/*. This file is the drift gate.
 *
 * Each block re-parses the python source and asserts the TS constant
 * matches. If a future PR mutates the python source (adds a verdict,
 * widens the excluded-events set, etc.) without touching the manifest
 * the test fails loudly with a clear message naming the mismatched set.
 */

const REPO_ROOT = path.resolve(__dirname, "..", "..")

function readSrc(rel: string): string {
  const abs = path.join(REPO_ROOT, rel)
  if (!existsSync(abs)) {
    throw new Error(`source file missing for runtime-manifest gate: ${rel}`)
  }
  return readFileSync(abs, "utf-8")
}

function extractPythonStringTuple(src: string, marker: string): string[] {
  /* Find the line `<marker>: ... = (` or `<marker> = (`, then read up to
   * the closing `)` and collect every quoted token. Tolerant of single
   * or double quotes, line breaks, trailing commas. Matches the few
   * tuple-shaped constants we read; sets are read with a `{ ... }`
   * variant below. */
  const idx = src.indexOf(marker)
  if (idx < 0) throw new Error(`marker not found: ${marker}`)
  const after = src.slice(idx)
  const open = after.indexOf("(")
  const close = after.indexOf(")", open + 1)
  if (open < 0 || close < 0) throw new Error(`tuple body not found for ${marker}`)
  const body = after.slice(open + 1, close)
  const tokens: string[] = []
  const re = /"([^"]+)"|'([^']+)'/g
  let m: RegExpExecArray | null
  while ((m = re.exec(body)) !== null) tokens.push(m[1] ?? m[2])
  return tokens
}

function extractPythonStringSet(src: string, marker: string): string[] {
  const idx = src.indexOf(marker)
  if (idx < 0) throw new Error(`marker not found: ${marker}`)
  const after = src.slice(idx)
  const open = after.indexOf("{")
  const close = after.indexOf("}", open + 1)
  if (open < 0 || close < 0) throw new Error(`set body not found for ${marker}`)
  const body = after.slice(open + 1, close)
  const tokens: string[] = []
  const re = /"([^"]+)"|'([^']+)'/g
  let m: RegExpExecArray | null
  while ((m = re.exec(body)) !== null) tokens.push(m[1] ?? m[2])
  return tokens
}

function extractPythonLiteralTuple(src: string, marker: string): string[] {
  const idx = src.indexOf(marker)
  if (idx < 0) throw new Error(`marker not found: ${marker}`)
  const after = src.slice(idx)
  const open = after.indexOf("[")
  const close = after.indexOf("]", open + 1)
  if (open < 0 || close < 0) throw new Error(`literal body not found for ${marker}`)
  const body = after.slice(open + 1, close)
  const tokens: string[] = []
  const re = /"([^"]+)"|'([^']+)'/g
  let m: RegExpExecArray | null
  while ((m = re.exec(body)) !== null) tokens.push(m[1] ?? m[2])
  return tokens
}

describe("D78 runtime-manifest drift gate", () => {
  it("LEDGER_VERDICTS_ORDERED matches verdicts.py", () => {
    const src = readSrc("src/magi_cp/policy/verdicts.py")
    const py = extractPythonStringTuple(src, "LEDGER_VERDICTS_ORDERED:")
    expect(py.length).toBeGreaterThan(0)
    expect([...LEDGER_VERDICTS_ORDERED]).toEqual(py)
  })

  it("HOOK_EVENTS_ALL matches the EventLiteral union in ir.py", () => {
    const src = readSrc("src/magi_cp/policy/ir.py")
    const py = extractPythonLiteralTuple(src, "EventLiteral = Literal[")
    expect(py.length).toBe(30)
    expect(new Set(HOOK_EVENTS_ALL)).toEqual(new Set(py))
    expect(HOOK_EVENTS_ALL.length).toBe(30)
  })

  it("CONTEXT_INJECTION_EXCLUDED_EVENTS matches _CONTEXT_INJECTION_EXCLUDED_EVENTS in ir.py", () => {
    const src = readSrc("src/magi_cp/policy/ir.py")
    const py = extractPythonStringSet(
      src, "_CONTEXT_INJECTION_EXCLUDED_EVENTS: frozenset[str] = frozenset(",
    )
    expect(py.length).toBe(8)
    expect(new Set(CONTEXT_INJECTION_EXCLUDED_EVENTS)).toEqual(new Set(py))
  })

  it("CONTEXT_INJECTION_ALTERNATE_CHANNEL has an entry for every excluded event", () => {
    for (const e of CONTEXT_INJECTION_EXCLUDED_EVENTS) {
      expect(
        CONTEXT_INJECTION_ALTERNATE_CHANNEL[e],
        `missing alternate-channel description for ${e}`,
      ).toBeTruthy()
    }
  })

  it("REWRITER_KINDS matches REWRITER_KINDS in rewriters.py", () => {
    const src = readSrc("src/magi_cp/policy/rewriters.py")
    const py = extractPythonStringSet(
      src, "REWRITER_KINDS: frozenset[str] = frozenset(",
    )
    expect(py.length).toBe(3)
    expect(new Set(REWRITER_KINDS)).toEqual(new Set(py))
  })

  it("RUN_COMMAND_RUNTIMES matches _RUN_COMMAND_RUNTIMES in ir.py", () => {
    const src = readSrc("src/magi_cp/policy/ir.py")
    const py = extractPythonStringTuple(
      src, "_RUN_COMMAND_RUNTIMES: tuple[str, ...] =",
    )
    expect(py.length).toBe(3)
    expect([...RUN_COMMAND_RUNTIMES]).toEqual(py)
  })

  it("RUN_COMMAND_TIMEOUT_DEFAULT_MS matches _DEFAULT_RUN_COMMAND_TIMEOUT_MS", () => {
    const src = readSrc("src/magi_cp/policy/ir.py")
    const m = src.match(/_DEFAULT_RUN_COMMAND_TIMEOUT_MS\s*=\s*([\d_]+)/)
    expect(m, "default timeout marker not found").toBeTruthy()
    const py = Number(m![1].replace(/_/g, ""))
    expect(RUN_COMMAND_TIMEOUT_DEFAULT_MS).toBe(py)
  })

  it("RUN_COMMAND_TIMEOUT_MAX_MS matches _MAX_RUN_COMMAND_TIMEOUT_MS", () => {
    const src = readSrc("src/magi_cp/policy/ir.py")
    const m = src.match(/_MAX_RUN_COMMAND_TIMEOUT_MS\s*=\s*([\d_]+)/)
    expect(m, "max timeout marker not found").toBeTruthy()
    const py = Number(m![1].replace(/_/g, ""))
    expect(RUN_COMMAND_TIMEOUT_MAX_MS).toBe(py)
  })

  it("CONTEXT_ACCEPTING_EVENT_COUNT is exactly 30 - 8 = 22", () => {
    expect(CONTEXT_ACCEPTING_EVENT_COUNT).toBe(22)
  })
})
