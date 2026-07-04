import { Skeleton } from "@/components/ui"

/**
 * Default console route loading state. Every console page is
 * `force-dynamic` with blocking server fetches, so without this the
 * operator gets a blank column while the cloud call resolves. Route
 * groups that want a shape-matched skeleton (overview KPIs, ledger
 * table) override this with their own loading.tsx.
 */
export default function ConsoleLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      <div className="mb-6 space-y-3">
        <Skeleton className="h-7 w-64 max-w-full" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    </div>
  )
}
