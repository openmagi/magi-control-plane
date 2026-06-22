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
 * Toggle — track h-6 w-11 (24×44), thumb h-5 w-5 (20×20). The thumb
 * fills more of the track than magi-agent's literal source (h-4 thumb)
 * because the rendered shadcn-style proportion is what their dashboard
 * actually shows in screenshots — slightly bigger thumb, 2px symmetric
 * padding.
 *
 * Slide: translate-x-[22px] when on, translate-x-0.5 when off
 * (44 - 2 - 20 = 22, gives 2px gap on the right).
 *
 * Off track: bg-gray-200 (was bg-black/15 which was too subtle —
 * the white thumb practically disappeared on it).
 *
 * Local extensions over the upstream Toggle:
 * - useTransition() + optimistic flip (server action)
 * - aria-busy={pending}
 * - stopPropagation()+preventDefault() so the parent <summary>
 *   doesn't also toggle <details>
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
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
        checked ? "bg-[var(--color-accent)]" : "bg-gray-200"
      }`}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-sm ring-1 ring-black/[0.04] transition-transform duration-200 ${
          checked ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  )
}
