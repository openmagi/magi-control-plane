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
 * Collapsible category container — list shell, not a grid. Children
 * are PresetRow elements that supply their own border-bottom dividers
 * (last:border-b-0 strips the trailing one).
 *
 * Layout mirrors the magi-agent Customize section: bold inline header,
 * single-column list of rows on a flat white card.
 */
export function CategorySection({
  id, title, hint, countLabel, defaultOpen = true, children,
}: CategorySectionProps) {
  return (
    <details
      id={id}
      open={defaultOpen}
      className="group rounded-2xl border border-black/[0.06] bg-white overflow-hidden"
    >
      <summary className="flex items-center gap-3 px-5 py-3.5 cursor-pointer list-none select-none hover:bg-gray-50/60 transition-colors duration-150 border-b border-transparent group-open:border-black/[0.06]">
        <ChevronDownIcon
          aria-hidden="true"
          className="w-4 h-4 text-[var(--color-text-tertiary)] transition-transform duration-200 group-open:rotate-0 -rotate-90"
        />
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0">
          {title}
        </h2>
        <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums ml-auto">
          {countLabel}
        </span>
      </summary>
      <div className="px-5 pt-2 pb-1 text-[11px] uppercase tracking-[0.14em] text-[var(--color-text-tertiary)]">
        {hint}
      </div>
      <div>{children}</div>
    </details>
  )
}
