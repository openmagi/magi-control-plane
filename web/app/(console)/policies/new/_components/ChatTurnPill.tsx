"use client"

/**
 * D55b: a single clickable option pill inside an assistant chat turn.
 *
 * Wraps a real <button> (not a div with role=button) so keyboard
 * activation (Enter / Space) and focus rings come for free. The
 * `aria-pressed` attribute carries the multi-select picked state.
 *
 * Sub-path imports only ("@/components/ui/Button"). The barrel
 * "@/components/ui" pulls a server-only chain (NavBarShell) into the
 * client bundle and breaks `next build`.
 */

type Tone = "default" | "pressed" | "disabled"

export interface ChatTurnPillProps {
  value: string
  label: string
  hint?: string
  pressed?: boolean
  disabled?: boolean
  onPick: (value: string) => void
  testId?: string
}

function toneFor(p: { pressed?: boolean; disabled?: boolean }): Tone {
  if (p.disabled) return "disabled"
  if (p.pressed) return "pressed"
  return "default"
}

function toneClass(t: Tone): string {
  switch (t) {
    case "pressed":
      return (
        "border-[var(--color-accent)] bg-[var(--color-accent)]/[0.08] " +
        "text-[var(--color-accent)]"
      )
    case "disabled":
      return (
        "border-black/[0.06] bg-gray-50 text-[var(--color-text-tertiary)] " +
        "cursor-not-allowed"
      )
    default:
      return (
        "border-black/[0.08] bg-white text-[var(--color-text-primary)] " +
        "hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.04]"
      )
  }
}

export function ChatTurnPill({
  value, label, hint, pressed, disabled, onPick, testId,
}: ChatTurnPillProps) {
  const t = toneFor({ pressed, disabled })
  return (
    <button
      type="button"
      aria-pressed={pressed ?? false}
      disabled={disabled}
      onClick={() => { if (!disabled) onPick(value) }}
      data-testid={testId}
      className={
        "inline-flex flex-col items-start gap-0.5 rounded-xl border px-3 py-2 " +
        "text-left text-xs leading-snug transition-colors " +
        toneClass(t)
      }
    >
      <span className="font-medium">{label}</span>
      {hint && (
        <span className="text-[11px] text-[var(--color-text-tertiary)] leading-snug">
          {hint}
        </span>
      )}
    </button>
  )
}

export default ChatTurnPill
