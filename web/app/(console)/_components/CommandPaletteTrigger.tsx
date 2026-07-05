"use client"

import { useEffect, useState } from "react"
import { OPEN_COMMAND_PALETTE_EVENT } from "./CommandPalette"

/** Visible search affordance for the ⌘K palette. Dispatches the open event
 * so it stays decoupled from the palette's React state. Shows the platform
 * shortcut hint (⌘K on mac, Ctrl K elsewhere). */
export function CommandPaletteTrigger({ label }: { label: string }) {
  const [isMac, setIsMac] = useState(true)
  useEffect(() => {
    setIsMac(/mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent))
  }, [])
  return (
    <button
      type="button"
      onClick={() => window.dispatchEvent(new Event(OPEN_COMMAND_PALETTE_EVENT))}
      aria-label={label}
      className="inline-flex items-center gap-2 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-secondary)] cursor-pointer"
    >
      <svg
        aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      >
        <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
      </svg>
      <span className="hidden sm:inline">{label}</span>
      <kbd className="hidden items-center rounded border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] px-1.5 font-mono text-[10px] text-[var(--color-text-tertiary)] sm:inline-flex">
        {isMac ? "⌘K" : "Ctrl K"}
      </kbd>
    </button>
  )
}

export default CommandPaletteTrigger
