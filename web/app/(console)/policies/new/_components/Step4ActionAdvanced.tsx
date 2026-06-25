"use client"

/**
 * D80: Step 4 action picker layered disclosure.
 *
 * The Step 4 action picker used to render all 6 archetype cards
 * stacked vertically: block / ask / audit / inject_context /
 * input_rewrite / run_command. Operators only need the top three for
 * the common case; the other three are advanced.
 *
 * This client island wraps the advanced cards (inject_context,
 * input_rewrite, run_command) inside a collapsible section that
 * mirrors the D61 Step 1 layered disclosure pattern:
 *
 *   - Default-collapsed; click the header to expand.
 *   - Persisted per-user via localStorage key
 *     `magi_cp.step4_advanced_open` (boolean payload).
 *   - Header reads "Advanced (3 actions) >" closed and
 *     "Advanced (3 actions) v" open.
 *   - The collapsed section uses display:none on the wrapper, NOT
 *     conditional render, so radio inputs nested inside still mount.
 *     This keeps the surrounding <form action={advanceAction}>'s
 *     radio reading honest: a previously-picked advanced action
 *     survives a server round-trip even when the operator's local
 *     localStorage is empty.
 *
 * Sub-path imports only (NOT from "@/components/ui") so the barrel
 * does not yank a server-only chain into the client bundle.
 */

import { useCallback, useEffect, useState } from "react"

export const STEP4_ADVANCED_OPEN_STORAGE_KEY = "magi_cp.step4_advanced_open"

function readPersistedOpen(): boolean {
  if (typeof window === "undefined") return false
  try {
    const raw = window.localStorage.getItem(STEP4_ADVANCED_OPEN_STORAGE_KEY)
    if (raw == null) return false
    return raw === "true" || raw === "1"
  } catch {
    return false
  }
}

function writePersistedOpen(next: boolean): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(
      STEP4_ADVANCED_OPEN_STORAGE_KEY,
      next ? "true" : "false",
    )
  } catch {
    /* quota / private-mode noop */
  }
}

function Caret({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 12 12"
      className={
        "h-3 w-3 transition-transform duration-150 " +
        (open ? "rotate-90" : "rotate-0")
      }
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 2l4 4-4 4" />
    </svg>
  )
}

export interface Step4ActionAdvancedProps {
  /** Bare section label ("Advanced" / "고급"). Matches the Step 1
   *  layered-disclosure shape: the left-side label is the group
   *  name only, the right-side count carries the numeric. */
  headerLabel: string
  /** Count of advanced actions surfaced inside (used for SR label and
   *  rendered on the right side of the toggle). */
  advancedCount: number
  /** SR label override for the toggle (collapse / expand verb). */
  expandLabel: string
  collapseLabel: string
  /** When TRUE, the section starts expanded regardless of localStorage.
   *  Used when the operator's currently-selected action lives in the
   *  Advanced tier so the picked card is visible after a round-trip. */
  forceOpen?: boolean
  children: React.ReactNode
}

export default function Step4ActionAdvanced({
  headerLabel,
  advancedCount,
  expandLabel,
  collapseLabel,
  forceOpen,
  children,
}: Step4ActionAdvancedProps) {
  // D80 follow-up (SSR-hydration #5): lazy initializer so the server-
  // rendered first paint matches the post-hydration state when the
  // operator's pick already lives in the Advanced tier. forceOpen is
  // a server-rendered prop (computed from defaultPick + lifecycle in
  // page.tsx), so its value is identical on server and client and
  // does not produce a hydration mismatch. Before this change, the
  // SSR markup hid the picked card (display:none on children) on
  // every paint, then JS revealed it after hydration, producing a
  // visible flash where the operator saw only block/ask/audit for a
  // frame even when they had previously selected an advanced action.
  //
  // localStorage hydration still runs in the effect below for the
  // persisted-open-without-forceOpen case (the operator manually
  // expanded the section on a prior visit, no advanced action picked).
  const [open, setOpen] = useState<boolean>(() => Boolean(forceOpen))
  useEffect(() => {
    const persisted = readPersistedOpen()
    setOpen(persisted || Boolean(forceOpen))
  }, [forceOpen])

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev
      writePersistedOpen(next)
      return next
    })
  }, [])

  return (
    <section
      data-testid="step4-advanced-section"
      data-advanced-open={open ? "true" : "false"}
      className="space-y-2 pt-2"
    >
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls="step4-advanced-rows"
        aria-label={open ? collapseLabel : expandLabel}
        data-testid="step4-advanced-toggle"
        className={
          "flex w-full items-center gap-2 rounded-md px-1 py-1 text-left " +
          "transition-colors hover:bg-black/[0.02]"
        }
      >
        <Caret open={open} />
        <span className="flex-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]">
          {headerLabel}
        </span>
        <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
          {advancedCount}
        </span>
      </button>
      {/* display:none on the wrapper (not conditional render) so the
       *  nested radios mount unconditionally. A previously-picked
       *  advanced action survives a server round-trip even when
       *  localStorage is empty: the form still posts the right value. */}
      <div
        id="step4-advanced-rows"
        data-testid="step4-advanced-rows"
        className={"space-y-3 " + (open ? "" : "hidden")}
      >
        {children}
      </div>
    </section>
  )
}
