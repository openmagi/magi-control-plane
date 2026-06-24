/**
 * P10 — endpoint attestation dashboard.
 *
 * Lists endpoints that have heartbeat'd into the cloud, their last_seen,
 * confirmed managed-settings digest, and freshness. The sidebar
 * WorkspaceCard surfaces the count summary; this page renders the full
 * roster.
 *
 * Source of truth = `endpoint_heartbeat` table populated by the gate's
 * `post_heartbeat()` helper (see local/gate.py).
 *
 * Issue #1 P0 (#1, #2): the dashboard does NOT claim
 * "endpoint-confirmed enforcement". The cloud only sees what the gate
 * (or anyone holding the tenant API key) asserts. The digest column
 * is annotated with one of:
 *   confirmed     — gate digest matches the current cloud-active compile
 *   stale-policy  — gate digest matches a historical compile we authored
 *   unknown       — gate digest matches nothing we authored
 *   not-loaded    — gate posted a null digest (first boot)
 * Until per-endpoint enrollment keys ship the `attested` column stays
 * empty and the operator-facing copy reads "claimed" rather than
 * "verified".
 */
import Link from "next/link"
import { cloud, type EndpointEntry, type EndpointListing } from "@/lib/cloud"
import { fmtUtc } from "@/lib/format"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function fmtAgo(epochSeconds: number, locale: "ko" | "en"): string {
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds))
  if (diff < 60) return locale === "ko" ? `${diff}초 전` : `${diff}s ago`
  if (diff < 3600) {
    const m = Math.floor(diff / 60)
    return locale === "ko" ? `${m}분 전` : `${m}m ago`
  }
  if (diff < 86400) {
    const h = Math.floor(diff / 3600)
    return locale === "ko" ? `${h}시간 전` : `${h}h ago`
  }
  const d = Math.floor(diff / 86400)
  return locale === "ko" ? `${d}일 전` : `${d}d ago`
}

function fmtHoursMinutes(seconds: number, isKo: boolean): string {
  if (seconds < 60) return isKo ? `${seconds}초` : `${seconds}s`
  if (seconds < 3600) {
    const m = Math.round(seconds / 60)
    return isKo ? `${m}분` : `${m}m`
  }
  if (seconds < 86400) {
    const h = Math.round(seconds / 3600)
    return isKo ? `${h}시간` : `${h}h`
  }
  const d = Math.round(seconds / 86400)
  return isKo ? `${d}일` : `${d}d`
}

function statusBadge(
  status: EndpointEntry["policy_status"],
  isKo: boolean,
): { variant: "ok" | "deny" | "info" | "review"; label: string } {
  switch (status) {
    case "confirmed":
      return { variant: "ok", label: isKo ? "확정" : "Confirmed" }
    case "stale-policy":
      return { variant: "review", label: isKo ? "구버전 정책" : "Stale policy" }
    case "unknown":
      return { variant: "deny", label: isKo ? "미확인" : "Unknown" }
    case "not-loaded":
    case undefined:
    default:
      return { variant: "info", label: isKo ? "미로드" : "Not loaded" }
  }
}

export default async function EndpointsPage() {
  const { t, locale } = await getT()
  const isKo = locale === "ko"
  let listing: EndpointListing | null = null
  let err: string | null = null
  try {
    listing = await cloud.listEndpointsListing()
  } catch (e: unknown) {
    err = errMsg(e)
  }
  const items: EndpointEntry[] | null = listing ? listing.items : null
  const threshold = listing?.stale_threshold_s ?? 24 * 3600
  const recommendedInterval = listing?.recommended_heartbeat_interval_s ?? 300

  return (
    <>
      <PageHeader
        title={isKo ? "엔드포인트" : "Endpoints"}
        description={
          isKo
            ? `게이트가 cloud에 attest한 활성 endpoint와 적용된 managed-settings digest. ${fmtHoursMinutes(threshold, true)} 이상 응답 없는 endpoint는 stale로 표시됩니다. Cloud는 게이트의 attest를 검증하지 못합니다 (테넌트 API 키 신뢰).`
            : `Gates attesting their loaded managed-settings to the cloud. Endpoints silent for over ${fmtHoursMinutes(threshold, false)} are flagged stale. The cloud trusts the tenant API key — it does not verify per-endpoint signatures.`
        }
      />

      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={err}
        />
      )}

      {items && items.length === 0 && (
        <EmptyState
          title={isKo ? "Endpoint 없음" : "No endpoints"}
          body={
            isKo
              ? `게이트에 MAGI_CP_ENDPOINT_ID와 MAGI_CP_API_KEY를 설정하고 magi-cp-heartbeat를 cron / launchd / systemd-timer에 추가하세요. 권장 주기: ${fmtHoursMinutes(recommendedInterval, true)}. 설치 가이드는 /setup → Step 5 참고.`
              : `Configure MAGI_CP_ENDPOINT_ID + MAGI_CP_API_KEY on each gate and add magi-cp-heartbeat to cron / launchd / systemd-timer. Recommended interval: ${fmtHoursMinutes(recommendedInterval, false)}. See /setup → Step 5 for platform-specific snippets.`
          }
          action={
            <Link href="/setup" className="underline text-sm">
              {isKo ? "설치 가이드" : "Setup guide"}
            </Link>
          }
        />
      )}

      {items && items.length > 0 && (
        <Card>
          {listing?.cloud_active_digest && (
            <div className="border-b border-black/[0.06] p-3 text-xs text-[var(--color-text-secondary)]">
              {isKo ? "현재 클라우드 활성 digest" : "Cloud-active digest"}
              {": "}
              <code translate="no" className="font-mono">
                {listing.cloud_active_digest.slice(0, 16)}…
              </code>
            </div>
          )}
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-black/[0.06] text-left">
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "Endpoint" : "Endpoint"}
                </th>
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "라벨" : "Label"}
                </th>
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "마지막 응답" : "Last seen"}
                </th>
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "Claimed Digest" : "Claimed digest"}
                </th>
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "정책 상태" : "Policy"}
                </th>
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "버전" : "Version"}
                </th>
                <th className="px-3 py-2 font-semibold text-[var(--color-text-secondary)]">
                  {isKo ? "Freshness" : "Freshness"}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((ep) => {
                const badge = statusBadge(ep.policy_status, isKo)
                return (
                  <tr
                    key={ep.endpoint_id}
                    className={
                      ep.stale
                        ? "border-b border-black/[0.04] bg-red-50/40"
                        : "border-b border-black/[0.04]"
                    }
                  >
                    <td className="px-3 py-2 font-mono text-xs">
                      {ep.endpoint_id}
                    </td>
                    <td className="px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                      {ep.label || "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                      <span title={fmtUtc(ep.last_seen)}>
                        {fmtAgo(ep.last_seen, isKo ? "ko" : "en")}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-[11px] text-[var(--color-text-tertiary)]"
                        title={ep.active_policy_digest ?? ""}>
                      {/* Issue #1 non-blocking #d: render 24 hex
                          chars (96 bits) so partial-collision
                          impostors are harder to confuse with the
                          real digest. */}
                      {ep.active_policy_digest
                        ? ep.active_policy_digest.slice(0, 24) + "…"
                        : isKo ? "(미로드)" : "(not loaded)"}
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant={badge.variant}>{badge.label}</Badge>
                    </td>
                    <td className="px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                      {ep.agent_version || "—"}
                    </td>
                    <td className="px-3 py-2">
                      {ep.stale ? (
                        <Badge variant="deny">
                          {isKo ? "Stale" : "Stale"}
                        </Badge>
                      ) : (
                        <Badge variant="ok">
                          {isKo ? "Healthy" : "Healthy"}
                        </Badge>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Card>
      )}

      <p className="mt-4 text-xs text-[var(--color-text-tertiary)]">
        {isKo ? (
          <>
            게이트 측 attestation 도구는{" "}
            <Link href="/setup" className="underline">
              /setup
            </Link>{" "}
            에서 확인하세요.
          </>
        ) : (
          <>
            Configure gate-side attestation under{" "}
            <Link href="/setup" className="underline">
              /setup
            </Link>
            .
          </>
        )}
      </p>
    </>
  )
}
