import type { ReactNode } from "react"

export interface NavGroupProps {
  /** Uppercase group label shown above the items (e.g. "AUTHORING"). */
  label: string
  children: ReactNode
}

/**
 * Sidebar group label + slot. Uses the magi-agent uppercase tracking
 * pattern (11px tracking-widest text-gray-400) for label-only header
 * with no enclosing border.
 */
export function NavGroup({ label, children }: NavGroupProps) {
  return (
    <div className="mt-5 first:mt-3">
      <div
        className="px-3 mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)] select-none"
        aria-hidden="true"
      >
        {label}
      </div>
      <ul role="list" className="flex flex-col gap-1">
        {children}
      </ul>
    </div>
  )
}
