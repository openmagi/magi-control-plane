"use client"

import { useTransition, type MouseEvent } from "react"
import type { togglePresetAction } from "../actions"

export interface PresetToggleProps {
  presetId: string
  enabled: boolean
  action: typeof togglePresetAction
  labelOn: string
  labelOff: string
}

/**
 * Toggle switch. Uses absolute positioning + inline-style transform
 * instead of Tailwind translate classes — earlier attempts using
 * `translate-x-6` / `translate-x-1` rendered the thumb in the middle
 * of the track (item layout collision inside an inline-flex), so we
 * pin the thumb absolutely and animate `transform` directly. No
 * ambiguity left for the layout engine.
 *
 * Dimensions:
 *   Track 24×44  (h-6 w-11), rounded-full
 *   Thumb 20×20  (h-5 w-5), pinned 2px from top, 2px from left
 *   On  → transform: translateX(20px) — thumb sits 2px from RIGHT edge
 *   Off → transform: translateX(0)    — thumb sits 2px from LEFT edge
 */
export function PresetToggle({
  presetId, enabled, action, labelOn, labelOff,
}: PresetToggleProps) {
  const [pending, startTransition] = useTransition()
  const checked = pending ? !enabled : enabled
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={checked ? labelOn : labelOff}
      aria-busy={pending || undefined}
      disabled={pending}
      onClick={(e: MouseEvent<HTMLButtonElement>) => {
        e.stopPropagation()
        e.preventDefault()
        startTransition(async () => { await action(presetId) })
      }}
      className={`relative inline-block h-6 w-11 shrink-0 rounded-full transition-colors duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
        checked ? "bg-[var(--color-accent)]" : "bg-gray-300"
      }`}
    >
      <span
        aria-hidden="true"
        style={{ transform: `translateX(${checked ? "20px" : "0"})` }}
        className="absolute top-0.5 left-0.5 inline-block h-5 w-5 rounded-full bg-white shadow ring-1 ring-black/[0.06] transition-transform duration-200 ease-out"
      />
    </button>
  )
}
