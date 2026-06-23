import { getLocale } from "@/lib/i18n/server"

export interface WorkspaceCardProps {
  tenantId: string | null
  plan: string | null
  /** true = cloud /healthz returned ok, false = unreachable / down */
  healthOk: boolean
  /** Cloud hostname shown subtly under the workspace label. */
  host: string
}

/**
 * Sidebar workspace context card. Matches magi-agent's pattern:
 * uppercase tracking-wide label, bold workspace name, muted descriptor,
 * subtle gradient bg from-white to gray-50.
 *
 * Three render states:
 *   1. Pro+ subscriber: tenant prefix + plan
 *   2. Self-host / env-key tenant: "Self-host" label + cloud host
 *   3. Fetch failed: shown as offline (amber dot). host still visible
 */
export function WorkspaceCard({ tenantId, plan, healthOk, host }: WorkspaceCardProps) {
  const locale = getLocale()
  const isKo = locale === "ko"
  const isSelfHost = tenantId === null || tenantId === "default"

  const labelTop = isKo ? "워크스페이스" : "Workspace"
  const headlineSelfHost = isKo ? "Self-host" : "Self-host"
  const descriptorSelfHost = isKo ? "Apache 2.0 OSS · 로컬 키" : "Apache 2.0 OSS · local key"

  const dotClass = healthOk ? "bg-emerald-500" : "bg-amber-500"
  const dotLabel = healthOk
    ? (isKo ? "정상" : "Healthy")
    : (isKo ? "응답 없음" : "Unreachable")

  return (
    <div className="mt-5 rounded-2xl border border-black/[0.06] bg-gradient-to-br from-white to-gray-50 px-4 py-3 shadow-sm">
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]">
        {labelTop}
      </div>
      {isSelfHost ? (
        <>
          <div className="mt-1 text-sm font-semibold text-[var(--color-text-primary)]">
            {headlineSelfHost}
          </div>
          <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
            {descriptorSelfHost}
          </div>
        </>
      ) : (
        <>
          <div className="mt-1 text-sm font-mono font-semibold text-[var(--color-text-primary)] truncate" translate="no">
            {tenantId!.length > 16 ? `${tenantId!.slice(0, 14)}…` : tenantId}
          </div>
          <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
            {isKo ? "플랜" : "Plan"}: <span className="text-[var(--color-text-secondary)] font-medium">{plan ?? ", "}</span>
          </div>
        </>
      )}
      <div className="mt-2 inline-flex items-center gap-1.5">
        <span role="img" aria-label={dotLabel} className={`inline-block h-1.5 w-1.5 rounded-full ${dotClass}`} />
        <span className="text-[11px] text-[var(--color-text-tertiary)] truncate font-mono" translate="no">
          {host}
        </span>
      </div>
    </div>
  )
}
