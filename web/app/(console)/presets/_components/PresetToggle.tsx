"use client"

import { useTransition, type MouseEvent } from "react"
import { cn } from "@/lib/cn"
import type { togglePresetAction } from "../actions"

export interface PresetToggleProps {
  presetId: string
  enabled: boolean
  action: typeof togglePresetAction
  labelOn: string
  labelOff: string
}

/**
 * Toggle switch matching shadcn proportions (track 24×44, thumb 20×20)
 * — wide enough that the thumb has breathing room on both ends, which
 * the earlier 16×28 size lacked (the "blob" look).
 *
 * - Transform-based slide (ease-out 200ms, not the cheaper width anim)
 * - `role="switch"` + `aria-checked` for screen readers
 * - Visible focus ring with white offset (light-mode contrast)
 * - Optimistic UI via useTransition so the thumb slides instantly
 * - stopPropagation()+preventDefault() because the button lives inside
 *   a <summary> element — without it, clicking the switch would also
 *   toggle the parent <details> disclosure
 * - prefers-reduced-motion collapses the transition via globals.css
 */
export function PresetToggle({
  presetId, enabled, action, labelOn, labelOff,
}: PresetToggleProps) {
  const [pending, startTransition] = useTransition()
  const renderEnabled = pending ? !enabled : enabled
  return (
    <button
      type="button"
      role="switch"
      aria-checked={renderEnabled}
      aria-label={renderEnabled ? labelOn : labelOff}
      aria-busy={pending || undefined}
      disabled={pending}
      onClick={(e: MouseEvent<HTMLButtonElement>) => {
        e.stopPropagation()
        e.preventDefault()
        startTransition(async () => { await action(presetId) })
      }}
      className={cn(
        "group/sw relative inline-flex h-6 w-11 shrink-0 items-center rounded-full",
        "transition-colors duration-200 ease-out cursor-pointer",
        "outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40 focus-visible:ring-offset-2 focus-visible:ring-offset-white",
        "disabled:cursor-not-allowed disabled:opacity-70",
        renderEnabled
          ? "bg-[var(--color-accent)] hover:bg-[var(--color-accent-hover)] active:bg-[var(--color-accent-press)]"
          : "bg-gray-200 hover:bg-gray-300 active:bg-gray-400",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none inline-block h-5 w-5 rounded-full bg-white",
          "shadow-[0_1px_2px_rgba(15,23,42,0.16)] ring-1 ring-black/[0.04]",
          "transition-transform duration-200 ease-out",
          renderEnabled ? "translate-x-[22px]" : "translate-x-0.5",
        )}
      />
    </button>
  )
}
