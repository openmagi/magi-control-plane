import { getLocale } from "@/lib/i18n/server"

export interface WorkspaceCardProps {
  tenantId: string | null
  plan: string | null
  /** true = cloud /healthz returned ok, false = unreachable / down */
  healthOk: boolean
  /** Cloud hostname shown in the card header (e.g. "cloud.openmagi.ai"). */
  host: string
}

/**
 * Sidebar workspace context card. Three render states:
 *
 *   1. Pro+ subscriber: tenant prefix + plan + green dot
 *   2. Self-host / env-key tenant: "Self-host" label + cloud host
 *   3. Fetch failed: amber dot + "cloud unreachable" hint
 *
 * Pure server component. Receives data from the parent Sidebar's
 * cached getWorkspaceData() (D4) so it never blocks render.
 */
export function WorkspaceCard({ tenantId, plan, healthOk, host }: WorkspaceCardProps) {
  const locale = getLocale()
  const isKo = locale === "ko"
  const isSelfHost = tenantId === null || tenantId === "default"

  const dotColor = healthOk
    ? "bg-[var(--color-pass-fg)]"
    : "bg-[var(--color-review-fg)]"
  const dotLabel = healthOk
    ? (isKo ? "정상" : "OK")
    : (isKo ? "응답 없음" : "unreachable")

  return (
    <div className="mx-3 mb-4 mt-2 rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] px-3 py-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor}`}
          aria-label={dotLabel}
        />
        <span className="text-xs text-[var(--color-text-tertiary)] truncate font-mono" translate="no">
          {host}
        </span>
      </div>
      {isSelfHost ? (
        <div>
          <div className="text-sm font-medium text-[var(--color-text-primary)]">
            {isKo ? "자체 호스트" : "Self-host"}
          </div>
          <div className="text-[11px] text-[var(--color-text-tertiary)] mt-0.5">
            {isKo ? "로컬 키 사용 중" : "using local key"}
          </div>
        </div>
      ) : (
        <div>
          <div className="text-sm font-mono text-[var(--color-text-primary)] truncate" translate="no">
            {tenantId!.length > 16 ? `${tenantId!.slice(0, 14)}…` : tenantId}
          </div>
          <div className="text-[11px] text-[var(--color-text-tertiary)] mt-0.5">
            {isKo ? "플랜" : "plan"}: <span className="text-[var(--color-text-secondary)]">{plan ?? "—"}</span>
          </div>
        </div>
      )}
    </div>
  )
}
