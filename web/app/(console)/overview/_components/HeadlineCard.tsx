import type { ReactNode } from "react"

// D76: imported by OverviewLive (a client component), so we use
// the subpath import to avoid pulling NavBarShell + i18n/server into
// the client bundle.
import { Card } from "@/components/ui/Card"

/**
 * D76: top-of-page narrative. Renders the "your control plane is
 * working" sentence with embedded counts, and degrades to the
 * "nothing has fired yet" copy when the cloud reports zero
 * emissions and zero pending HITL items.
 *
 * Server-renderable; the live-refresh path swaps the contents via
 * the OverviewLive client island.
 */
type Props = {
  total: number
  blocked: number
  pending: number
  audited: number
  hasActivity: boolean
  /** Rendered above the sentence (e.g. "지난 24h, 정책이 N건 처리했어요."). */
  headline: ReactNode
  /** Rendered below the sentence when `hasActivity` is true. */
  detail: ReactNode
  /** Fallback body for the no-activity case. */
  emptyBody: ReactNode
  /** Optional decorations (e.g. "last refreshed Ns ago"). */
  footer?: ReactNode
}

export function HeadlineCard({
  hasActivity, headline, detail, emptyBody, footer,
}: Props) {
  return (
    <Card className="flex flex-col gap-2">
      <div className="text-base font-semibold text-[var(--color-text-primary)]">
        {headline}
      </div>
      <div className="text-sm text-[var(--color-text-secondary)]">
        {hasActivity ? detail : emptyBody}
      </div>
      {footer && (
        <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          {footer}
        </div>
      )}
    </Card>
  )
}

export default HeadlineCard
