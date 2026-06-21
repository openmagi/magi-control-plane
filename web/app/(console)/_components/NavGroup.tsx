import type { ReactNode } from "react"

export interface NavGroupProps {
  /** Uppercase group label shown above the items (e.g. "AUTHORING"). */
  label: string
  /** NavItem children. */
  children: ReactNode
}

/**
 * Sidebar group header + slot. Pure server component — no state, no
 * interactivity. Layout-only; group label is decorative (the items
 * inside are the actual nav targets).
 */
export function NavGroup({ label, children }: NavGroupProps) {
  return (
    <div className="mt-5 first:mt-0">
      <div
        className="px-3 mb-2 text-[10px] font-semibold tracking-wider text-[var(--color-text-tertiary)] uppercase select-none"
        aria-hidden="true"
      >
        {label}
      </div>
      <ul role="list" className="flex flex-col gap-0.5">
        {children}
      </ul>
    </div>
  )
}
