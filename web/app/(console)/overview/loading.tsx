import { Skeleton, SkeletonKPI } from "@/components/ui"

/** Overview loading state: KPI row + chart + recent-activity block, so the
 *  layout does not jump when the 24h aggregate resolves. */
export default function OverviewLoading() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      <div className="mb-6 space-y-3">
        <Skeleton className="h-7 w-56 max-w-full" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SkeletonKPI />
        <SkeletonKPI />
        <SkeletonKPI />
        <SkeletonKPI />
      </div>
      <Skeleton className="mt-6 h-56 w-full" />
      <Skeleton className="mt-6 h-40 w-full" />
    </div>
  )
}
