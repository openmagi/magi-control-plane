import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for the SteeringAwareField client island.
 *
 * Matches the SidebarClient.test.ts / hitl page.test.ts convention
 * (grep the rendered TSX for the contract rather than spinning up
 * React Testing Library mounts). The runtime live-typing UX is
 * exercised manually in the dev server.
 *
 * The invariants here are the ones a future refactor is most likely
 * to silently break:
 *
 *   - "use client" pragma (otherwise it server-renders and the
 *     "appear as type" property regresses to the old defaultValue
 *     contract).
 *   - The heuristic actually reruns on each keystroke (debounced).
 *   - Hrefs for the two switch affordances are REBUILT from local
 *     state on each render, not consumed verbatim from the server-
 *     built starting point.
 *   - The "keep payload-kind" affordance is a <button> (not a Link)
 *     so we can preventDefault + write to sessionStorage without a
 *     navigation.
 *   - sessionStorage suppression flag uses the documented key shape.
 *   - The DOM input still has `name="..."` so the surrounding server
 *     form posts the live value back to advanceWizard.
 *   - An aria-live status region announces the tip appearing.
 */
describe("SteeringAwareField invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "SteeringAwareField.tsx"),
    "utf-8",
  )

  it("is marked \"use client\"", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("imports detectCumulativeSteering", () => {
    expect(src).toMatch(/detectCumulativeSteering/)
  })

  it("debounces the heuristic input (per-keystroke without thrash)", () => {
    // The detector reruns on `debouncedText`, not `text`, with a
    // setTimeout(...,DEBOUNCE_MS) bridge.
    expect(src).toMatch(/const DEBOUNCE_MS = 150/)
    expect(src).toMatch(/setDebouncedText/)
    expect(src).toMatch(/window\.setTimeout/)
  })

  it("rebuilds switchHref / switchPreFinalHref from live local text", () => {
    expect(src).toMatch(/rebuildHrefWithLiveText\(baseSwitchHref, name, text\)/)
    expect(src).toMatch(/rebuildHrefWithLiveText\(baseSwitchPreFinalHref, name, text\)/)
  })

  it("uses sessionStorage (not URL state) for dismissal", () => {
    expect(src).toMatch(/window\.sessionStorage/)
    expect(src).toMatch(/magi-cp:steering-dismissed:/)
    // No URL writes for keepKind (the value should not appear as a
    // URLSearchParams key or hidden form input).
    expect(src).not.toMatch(/params\.set\(['"]keepKind['"]/)
    expect(src).not.toMatch(/name=['"]keepKind['"]/)
    expect(src).not.toMatch(/searchParams\.keepKind/)
  })

  it("'keep payload-kind' is a real <button>, not a navigation Link", () => {
    // The dismissal must preventDefault — clicking a Link would
    // server-navigate, losing live state.
    expect(src).toMatch(/onClick=\{onKeep\}/)
    expect(src).toMatch(/e\.preventDefault\(\)/)
  })

  it("forwards a `name` attribute so the surrounding form picks the live value", () => {
    expect(src).toMatch(/name,/) // destructured from props
    expect(src).toMatch(/name: "pattern" \| "llmCriterion" \| "shaclTtl"/)
  })

  it("renders an aria-live status region for SR users", () => {
    expect(src).toMatch(/aria-live="polite"/)
  })

  it("listens for external 'input' events (so PayloadFieldChips splices propagate to state)", () => {
    // The chip insert dispatches a bubbling 'input' event on the
    // textarea — React won't see it because we control `value`, so
    // the island wires an addEventListener and pulls el.value back
    // into state.
    expect(src).toMatch(/addEventListener\("input"/)
  })
})
