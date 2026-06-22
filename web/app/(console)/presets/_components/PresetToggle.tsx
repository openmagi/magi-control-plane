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
 * Toggle — byte-equivalent copy of the magi-agent OSS Customize modal
 * switch (Toggle component in verification-rule-modal.tsx +
 * custom-tool-modal.tsx). Same track / thumb / colour pattern so the
 * magi-cp Presets surface matches the upstream visual language.
 *
 * Track  h-6 w-11   (24×44)
 * Thumb  h-4 w-4    (16×16) — smaller than shadcn's 20×20, more
 *                           "breathing room" inside the track which
 *                           was the source of the earlier "blob" look
 * Off    bg-black/15      → muted dark
 * On     bg-[--color-accent]
 * Slide  translate-x-6 / translate-x-1
 *
 * Local extensions vs magi-agent's Toggle:
 * - useTransition + optimistic flip (server action, not a setState)
 * - aria-busy=true while the action is in flight
 * - stopPropagation()+preventDefault() because this lives inside a
 *   <summary> and a normal click would also toggle the parent
 *   <details> disclosure
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
        checked ? "bg-[var(--color-accent)]" : "bg-black/15"
      }`}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  )
}
