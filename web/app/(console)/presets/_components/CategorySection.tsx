import type { ReactNode } from "react"
import { ChevronDownIcon } from "@heroicons/react/24/outline"

export interface CategorySectionProps {
  id: string
  title: string
  hint: string
  countLabel: string
  /** When true, the section starts in the open state. */
  defaultOpen?: boolean
  children: ReactNode
}

/**
 * Collapsible category container using native <details>. No JS state
 * required — the disclosure widget is keyboard + screen-reader
 * friendly out of the box. The chevron rotates via the
 * group-open Tailwind variant.
 */
export function CategorySection({
  id, title, hint, countLabel, defaultOpen = true, children,
}: CategorySectionProps) {
  return (
    <details
      id={id}
      open={defaultOpen}
      className="group rounded-2xl border border-black/[0.06] bg-white/60 backdrop-blur-xl overflow-hidden"
    >
      <summary className="flex items-center gap-3 px-5 py-4 cursor-pointer list-none select-none hover:bg-white/80 transition-colors duration-150">
        <ChevronDownIcon
          aria-hidden="true"
          className="w-4 h-4 text-[var(--color-text-tertiary)] transition-transform duration-200 group-open:rotate-0 -rotate-90"
        />
        <h2 className="text-md font-semibold text-[var(--color-text-primary)] m-0">
          {title}
        </h2>
        <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
          {countLabel}
        </span>
      </summary>
      <div className="px-5 pb-5 pt-1 space-y-2">
        <p className="text-xs text-[var(--color-text-tertiary)] -mt-1">
          {hint}
        </p>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {children}
        </div>
      </div>
    </details>
  )
}
