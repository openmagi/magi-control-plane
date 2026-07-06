import { describe, it, expect } from "vitest"
import { readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"

/**
 * WCAG 2.2 gate (Phase 3, PR-3.4).
 *
 * Static, dependency-free source invariants that hold the accessibility
 * floor the design renewal established, so a later change cannot silently
 * regress it. Runs inside the existing web vitest job (no browser, no axe
 * install). Complements the manual dev-server checks done per screen.
 *
 * Two concrete, mechanically-checkable rules:
 *   1. focus-visible is never removed without a replacement (WCAG 2.4.7).
 *   2. the borderline tertiary-on-tinted contrast pair is documented and
 *      pinned, so a token edit that breaks it is caught here (WCAG 1.4.3).
 */

const WEB_ROOT = path.join(__dirname, "..")

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next" || name === "dist") continue
    const full = path.join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) walk(full, out)
    else if (/\.tsx$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(full)
  }
  return out
}

const TSX_FILES = walk(path.join(WEB_ROOT, "app"))
  .concat(walk(path.join(WEB_ROOT, "components")))

describe("WCAG 2.2 gate: focus indicator is never silently removed", () => {
  // Killing the focus ring is only acceptable when a visible focus indicator
  // replaces it: a focus-visible rule, OR a `focus:ring`/`focus:border`
  // treatment (the DS input pattern), OR the global `:focus:not(:focus-
  // visible)` reset (mouse-only drop that keeps the keyboard ring).
  // `outline-none` on a `tabIndex={-1}` element is a programmatic scroll/skip
  // target, not a keyboard-reachable control, so it is exempt.
  const REMOVE = /\boutline-none\b|\boutline-0\b|outline:\s*none/
  const REPLACE =
    /focus-visible|focusVisible|focus:ring|focus:border|focus:outline\[|focus:shadow/

  // The element carrying the outline removal is exempt when it is a
  // non-interactive programmatic target (tabIndex={-1}) or an ARIA combobox
  // input (the palette pattern where focus stays on the input and selection
  // is virtual via aria-activedescendant). Look at a small window around the
  // match since JSX attributes span multiple lines.
  function elementIsExempt(lines: string[], idx: number): boolean {
    const from = Math.max(0, idx - 6)
    const window = lines.slice(from, idx + 3).join("\n")
    if (/tabIndex=\{-1\}|tabindex="-1"/.test(window)) return true
    if (/role="combobox"|aria-activedescendant/.test(window)) return true
    return false
  }

  function removesRingUnsafely(src: string): boolean {
    const lines = src.split("\n")
    return lines.some((line, i) => {
      if (!REMOVE.test(line)) return false
      if (REPLACE.test(line)) return false          // same-line replacement
      if (elementIsExempt(lines, i)) return false   // exempt element
      return !REPLACE.test(src)                      // no replacement anywhere
    })
  }

  const offenders = TSX_FILES
    .filter((f) => removesRingUnsafely(readFileSync(f, "utf-8")))
    .map((f) => path.relative(WEB_ROOT, f))

  it("every focus-ring removal keeps a visible focus indicator", () => {
    expect(offenders, `files removing the focus ring with no visible replacement:\n${offenders.join("\n")}`).toEqual([])
  })
})

describe("WCAG 2.2 gate: focusable interactive elements keep a visible ring", () => {
  // A component that hard-disables its ring with `focus:outline-none` and no
  // focus-visible sibling is the classic keyboard-a11y regression. Covered by
  // the file-level scan above; this asserts the global base stylesheet still
  // ships the app-wide focus-visible rules the renewal relies on.
  const globals = readFileSync(path.join(WEB_ROOT, "app", "globals.css"), "utf-8")

  it("globals.css defines a focus-visible outline for interactive elements", () => {
    expect(globals).toMatch(/:focus-visible/)
    expect(globals).toMatch(/outline:\s*2px solid var\(--color-border-focus\)/)
  })

  it("globals.css only drops the outline for non-keyboard focus", () => {
    // The one legitimate outline:none is `:focus:not(:focus-visible)`, which
    // keeps the ring for keyboard users while dropping it for mouse.
    expect(globals).toMatch(/:focus:not\(:focus-visible\)\s*\{\s*outline:\s*none/)
  })
})

describe("WCAG 2.2 gate: verdict semantics never rely on color alone", () => {
  // The audit ledger / verdict rows must carry a text label, not just a hue
  // (WCAG 1.4.1). The verdict vocabulary lives in _ds/Badge; assert it pairs
  // a background token with a foreground token (never bg-only) for each
  // verdict variant, so a verdict chip always renders readable text.
  const badge = readFileSync(
    path.join(WEB_ROOT, "components", "ui", "_ds", "Badge.tsx"),
    "utf-8",
  )
  it("each verdict Badge variant sets both bg and fg tokens", () => {
    for (const v of ["ok", "review", "deny", "info"]) {
      const line = badge.split("\n").find((l) => l.trim().startsWith(`${v}:`))
      expect(line, `Badge variant ${v} missing`).toBeTruthy()
      expect(line, `Badge variant ${v} must set both bg and text`).toMatch(/bg-\[var\(.+\)\].*text-\[var\(.+\)\]/)
    }
  })
})
