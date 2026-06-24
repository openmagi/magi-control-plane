import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D63 — RunCommandForm source invariants.
 *
 * The wizard's Step 4b carries the inline-command / attach-script
 * lane; this file pins the surface so a refactor cannot silently
 * drop the runtime select, the timeout slider, the warning callout,
 * or the dismissal localStorage key.
 *
 * We grep the source rather than importing the component because
 * the repo's vitest setup runs in node-only mode without the React
 * + Next path alias chain.
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("RunCommandForm source invariants", () => {
  const src = read("RunCommandForm.tsx")

  it("ships the mode toggle (inline + attach radios)", () => {
    expect(src).toMatch(/name="run_command_mode"/)
    expect(src).toMatch(/value="inline"/)
    expect(src).toMatch(/value="attach"/)
  })

  it("exposes runtime select, args input, timeout slider", () => {
    expect(src).toMatch(/value="bash"/)
    expect(src).toMatch(/value="python3"/)
    expect(src).toMatch(/value="node"/)
    expect(src).toMatch(/type="range"/)
    expect(src).toMatch(/min=\{MIN_TIMEOUT\}/)
    expect(src).toMatch(/max=\{MAX_TIMEOUT\}/)
  })

  it("renders the dismissible warning callout with a localStorage key", () => {
    expect(src).toMatch(/magi_cp\.run_command_warning_dismissed/)
    expect(src).toMatch(/dismissWarning/)
    expect(src).toMatch(/newPolicy\.step4\.runCommand\.warning/)
  })

  it("posts the uploaded file to /api/scripts", () => {
    expect(src).toMatch(/fetch\("\/api\/scripts"/)
    expect(src).toMatch(/method: "POST"/)
    expect(src).toMatch(/FormData/)
  })

  it("exports runCommandDraftToIr + runCommandDraftSummary", () => {
    expect(src).toMatch(/export function runCommandDraftToIr/)
    expect(src).toMatch(/export function runCommandDraftSummary/)
  })

  it("caps inline command at 4000 chars", () => {
    expect(src).toMatch(/MAX_INLINE_LEN = 4000/)
    expect(src).toMatch(/maxLength=\{MAX_INLINE_LEN\}/)
  })

  it("auto-detects runtime from shebang", () => {
    expect(src).toMatch(/parseShebangRuntime/)
    expect(src).toMatch(/python/)
    expect(src).toMatch(/node/)
    expect(src).toMatch(/bash/)
  })
})
