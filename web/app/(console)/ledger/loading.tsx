import { Skeleton } from "@/components/ui"

/** Ledger loading state: a row-shaped table skeleton (timestamp, actor,
 *  action, verdict) so the audit table does not pop in on data arrival. */
export default function LedgerLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      <div className="mb-6 space-y-3">
        <Skeleton className="h-7 w-40 max-w-full" />
        <Skeleton className="h-4 w-72 max-w-full" />
      </div>
      <div className="overflow-hidden rounded-lg border border-[var(--color-border-subtle)]">
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-4 border-b border-[var(--color-border-subtle)] px-4 py-3 last:border-b-0"
          >
            <Skeleton className="h-3 w-28 shrink-0" />
            <Skeleton className="h-3 w-20 shrink-0" />
            <Skeleton className="h-3 w-40 max-w-full" />
            <Skeleton className="ml-auto h-3 w-16 shrink-0" />
          </div>
        ))}
      </div>
    </div>
  )
}
