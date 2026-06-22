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
 * Compact switch (28×16 / thumb 12px). Optimistic flip via
 * useTransition so the thumb slides immediately on tap; server
 * action persists the cookie + path-revalidates.
 *
 * stopPropagation is essential — this button often lives inside a
 * <summary> element, where a regular click would also toggle the
 * <details> disclosure.
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
      disabled={pending}
      onClick={(e: MouseEvent<HTMLButtonElement>) => {
        e.stopPropagation()
        e.preventDefault()
        startTransition(async () => { await action(presetId) })
      }}
      className={cn(
        "relative inline-flex h-4 w-7 shrink-0 items-center rounded-full",
        "transition-colors duration-150 cursor-pointer",
        "outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]/30 focus-visible:ring-offset-2",
        renderEnabled
          ? "bg-[var(--color-accent)]"
          : "bg-gray-300 hover:bg-gray-400",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "inline-block h-3 w-3 rounded-full bg-white shadow-sm",
          "transition-transform duration-150",
          renderEnabled ? "translate-x-[14px]" : "translate-x-0.5",
        )}
      />
    </button>
  )
}
