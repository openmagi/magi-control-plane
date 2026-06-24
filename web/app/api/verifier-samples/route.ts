import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"

/**
 * D53a: same-origin proxy for the client expander.
 *
 * The cloud's `/ledger/samples` endpoint requires the tenant API key,
 * which we never want to ship to the browser. This route fetches the
 * samples server-side (cloud.ts reads the key from env) and returns
 * the already-redacted payload to the client component.
 *
 * Query:
 *   verifier=<step>   required, non-empty, <=64 chars.
 *   limit=<n>         optional; clamped to [1, 25] by the SDK + cloud.
 *
 * Returns the same `{samples: [...]}` shape as the upstream so the
 * client renders without an adapter. Errors return JSON with status
 * 5xx so the expander can render a small fallback line; the upstream
 * detail never leaks to the browser (matches the cloud client's
 * "log server-side, expose status only" posture).
 */
export const dynamic = "force-dynamic"

const MAX_VERIFIER_LEN = 64
const VERIFIER_PATTERN = /^[A-Za-z0-9_\-]+$/

export async function GET(req: NextRequest) {
  const url = new URL(req.url)
  const verifier = url.searchParams.get("verifier") ?? ""
  const limitRaw = url.searchParams.get("limit")
  const limit = limitRaw ? Math.floor(Number(limitRaw)) : 5

  if (!verifier || verifier.length > MAX_VERIFIER_LEN
      || !VERIFIER_PATTERN.test(verifier)) {
    return Response.json(
      { error: "invalid verifier" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  if (!Number.isFinite(limit) || limit < 1 || limit > 25) {
    return Response.json(
      { error: "invalid limit" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }

  try {
    const r = await cloud.listVerifierSamples(verifier, limit)
    return Response.json(r, { headers: { "cache-control": "no-store" } })
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
