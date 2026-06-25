import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"

/**
 * D76: same-origin proxy for the /overview auto-refresh loop.
 *
 * The /overview page is a server component that renders an initial
 * snapshot. After mount, the OverviewLive client island polls this
 * route every 30s (when the tab is visible) so the headline + KPI
 * grid + chart stay current without a full page reload.
 *
 * Why a same-origin proxy and not direct browser → cloud calls:
 *   - The tenant API key never crosses the network boundary; it's
 *     read from env inside `cloud.ts` (server-only module).
 *   - One round-trip carries both summary + aggregate, so the client
 *     does not fan-out two requests on every tick.
 *   - Errors collapse to a structured envelope (`error: "upstream"`)
 *     so the client can render a small fallback without ever seeing
 *     upstream detail.
 *
 * Query:
 *   since_secs   optional, defaults to 86400 (24h). Clamped server-side.
 *   bucket_secs  optional, defaults to 3600 (1h). Clamped server-side.
 *
 * Returns:
 *   { summary, aggregate, ts }
 *   `ts` is the server's wall-clock at fetch time so the client can
 *   render "last refreshed Ns ago" without trusting browser time.
 */
export const dynamic = "force-dynamic"

const DEFAULT_SINCE_SECS = 86_400
const DEFAULT_BUCKET_SECS = 3_600
// Upper bounds mirror the cloud-side `MAX_SINCE_SECS` + `MAX_BUCKETS`
// guardrails in src/magi_cp/cloud/metrics.py; we clamp here too so a
// malformed URL never reaches the cloud.
const MAX_SINCE_SECS = 30 * 86_400
const MIN_BUCKET_SECS = 60

function parsePositiveInt(raw: string | null, fallback: number,
                          max: number, min: number = 1): number {
  if (raw == null) return fallback
  const n = Math.floor(Number(raw))
  if (!Number.isFinite(n) || n < min) return fallback
  return Math.min(n, max)
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url)
  const sinceSecs = parsePositiveInt(
    url.searchParams.get("since_secs"), DEFAULT_SINCE_SECS, MAX_SINCE_SECS,
  )
  const bucketSecs = parsePositiveInt(
    url.searchParams.get("bucket_secs"), DEFAULT_BUCKET_SECS, MAX_SINCE_SECS,
    MIN_BUCKET_SECS,
  )

  try {
    const [summary, aggregate] = await Promise.all([
      cloud.overviewSummary(),
      cloud.ledgerAggregate(sinceSecs, bucketSecs),
    ])
    return Response.json(
      { summary, aggregate, ts: Math.floor(Date.now() / 1000) },
      { headers: { "cache-control": "no-store" } },
    )
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "server config" },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    return Response.json(
      { error: "upstream" },
      { status: 502, headers: { "cache-control": "no-store" } },
    )
  }
}
