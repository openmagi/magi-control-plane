import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"
import { isSameOrigin } from "@/lib/same-origin"

/**
 * D53b: same-origin proxy for the authoring-page Dry-run button.
 *
 * The cloud's `/policies/dry-run` endpoint requires the admin API
 * key, which we never want to ship to the browser. This route
 * forwards the IR + window + limit, calls the cloud server-side
 * (cloud.ts reads the key from env), and returns the already-redacted
 * payload to the client component.
 *
 * Body shape:
 *   { ir: object, since?: "24h" | "7d", limit?: number }
 *
 * Errors surface as JSON with a 4xx / 5xx status so the client can
 * render an inline friendly message. Upstream cloud error text is
 * never echoed back (logged to server stderr only). The Dry-run UX
 * is opt-in and best-effort: the brief explicitly says "Do not
 * block save on dry-run failure," so the client treats a failed
 * response as a recoverable, non-blocking error.
 */
export const dynamic = "force-dynamic"

// Cap matches the cloud's pydantic Field(ge=1, le=10_000). We validate
// here too so we can reject malformed requests with a clean 400 before
// burning a cloud round-trip.
const LIMIT_MAX = 10_000
const SINCE_VALUES = new Set(["24h", "7d"])

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) {
    return Response.json(
      { error: "cross-origin request rejected" },
      { status: 403, headers: { "cache-control": "no-store" } },
    )
  }
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return Response.json(
      { error: "invalid body" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }

  if (body == null || typeof body !== "object") {
    return Response.json(
      { error: "body must be an object" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  const obj = body as Record<string, unknown>
  const ir = obj.ir
  if (ir == null || typeof ir !== "object" || Array.isArray(ir)) {
    return Response.json(
      { error: "ir must be an object" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  const since = obj.since == null ? "24h" : String(obj.since)
  if (!SINCE_VALUES.has(since)) {
    return Response.json(
      { error: "since must be 24h or 7d" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  let limit: number = 1000
  if (obj.limit !== undefined) {
    const n = Math.floor(Number(obj.limit))
    if (!Number.isFinite(n) || n < 1 || n > LIMIT_MAX) {
      return Response.json(
        { error: "limit must be 1..10000" },
        { status: 400, headers: { "cache-control": "no-store" } },
      )
    }
    limit = n
  }

  try {
    const r = await cloud.dryRunPolicy(
      ir as Record<string, unknown>,
      since as "24h" | "7d",
      limit,
    )
    return Response.json(r, { headers: { "cache-control": "no-store" } })
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "server config" },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    // The cloud's 422 (invalid IR) lands here as `cloud 422`. We
    // surface it as 422 so the client can render the
    // validation-failed message inline. Anything else collapses to
    // 502 (upstream) so internal details never leak to the browser.
    const msg = e instanceof Error ? e.message : String(e)
    const m = /cloud (\d{3})/.exec(msg)
    const upstream = m ? Number(m[1]) : 0
    if (upstream === 422) {
      return Response.json(
        { error: "invalid policy" },
        { status: 422, headers: { "cache-control": "no-store" } },
      )
    }
    return Response.json(
      { error: "upstream" },
      { status: 502, headers: { "cache-control": "no-store" } },
    )
  }
}
