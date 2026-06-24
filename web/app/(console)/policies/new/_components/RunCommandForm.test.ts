import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D63 — run_command Step 4b source invariants.
 *
 * D63 review (P1 dead-code/contract-mismatch + test-coverage): the
 * wizard now uses `Step4bRunCommandFields.tsx` instead of the
 * orphaned `RunCommandForm.tsx`. This file's invariants moved with
 * the live component; the legacy `RunCommandForm.tsx` is retained
 * for back-compat consumers (the helpers `runCommandDraftToIr` /
 * `runCommandDraftSummary` are still exported for potential reuse)
 * but Step 4b no longer mounts it.
 *
 * We grep the source rather than importing the component because
 * the repo's vitest setup runs in node-only mode without the React
 * + Next path alias chain. Behavioural coverage (file upload,
 * mode toggle, localStorage dismissal) lives at the integration
 * level in policies/new/wizard-wiring tests; the invariants here
 * pin the surface so a refactor cannot silently drop the runtime
 * select, the warning callout, or the dismissal localStorage key.
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("Step4bRunCommandFields source invariants", () => {
  const src = read("Step4bRunCommandFields.tsx")

  it("ships the mode toggle (inline + attach options)", () => {
    expect(src).toMatch(/name="runCommandMode"/)
    expect(src).toMatch(/value="inline"/)
    expect(src).toMatch(/value="attach"/)
  })

  it("renders inline and attach lanes mutually exclusive (mode === '...')", () => {
    expect(src).toMatch(/mode === "inline"/)
    expect(src).toMatch(/mode === "attach"/)
  })

  it("exposes runtime select, args input, timeout input", () => {
    expect(src).toMatch(/name="runCommandRuntime"/)
    expect(src).toMatch(/name="runCommandArgs"/)
    expect(src).toMatch(/name="runCommandTimeoutMs"/)
    expect(src).toMatch(/value="bash"/)
    expect(src).toMatch(/value="python3"/)
    expect(src).toMatch(/value="node"/)
  })

  it("renders the dismissible warning callout with a localStorage key", () => {
    expect(src).toMatch(/magi_cp\.run_command_warning_dismissed/)
    expect(src).toMatch(/dismissWarning/)
    expect(src).toMatch(/newPolicy\.step4\.runCommand\.warning/)
  })

  it("posts the uploaded file to /api/scripts via the multipart proxy", () => {
    expect(src).toMatch(/fetch\("\/api\/scripts"/)
    expect(src).toMatch(/method: "POST"/)
    expect(src).toMatch(/FormData/)
  })

  it("renders a file input in the attach lane", () => {
    expect(src).toMatch(/type="file"/)
    // file input only renders when mode === "attach"
    expect(src).toMatch(/mode === "attach"/)
  })

  it("surfaces a Browse uploaded scripts link to /scripts", () => {
    expect(src).toMatch(/\/scripts/)
    expect(src).toMatch(/Browse uploaded scripts|스크립트 목록 열기/)
  })

  it("caps inline command at 4000 chars", () => {
    expect(src).toMatch(/MAX_INLINE_LEN = 4000/)
    expect(src).toMatch(/maxLength=\{MAX_INLINE_LEN\}/)
  })

  it("auto-detects runtime from shebang in the attach lane", () => {
    expect(src).toMatch(/parseShebangRuntime/)
    expect(src).toMatch(/python/)
    expect(src).toMatch(/node/)
    expect(src).toMatch(/bash/)
  })

  it("renders the commandHint i18n key (not the attachHint mis-labelling)", () => {
    expect(src).toMatch(/newPolicy\.step4\.runCommand\.commandHint/)
  })
})

describe("RunCommandForm legacy helpers (unmounted)", () => {
  const src = read("RunCommandForm.tsx")

  it("still exports runCommandDraftToIr + runCommandDraftSummary helpers", () => {
    // The helpers are kept around for any future re-mount or import
    // from a different surface. The component itself is no longer
    // wired into the wizard (see Step4bRunCommandFields).
    expect(src).toMatch(/export function runCommandDraftToIr/)
    expect(src).toMatch(/export function runCommandDraftSummary/)
  })
})

describe("plainSummary runs run_command body verbatim (page.tsx)", () => {
  const src = readFileSync(
    path.join(HERE, "..", "page.tsx"),
    "utf-8",
  )

  it("renders the verbatim runCommandBody in the Step 6 plain summary", () => {
    // D63 review (P1 brief-mismatch): the summary must NOT strip the
    // actual command body — the operator needs to see what will run
    // before saving.
    expect(src).toMatch(/runCommandBody/)
    expect(src).toMatch(/act === "run_command"/)
  })
})
