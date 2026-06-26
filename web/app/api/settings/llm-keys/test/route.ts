import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"

/**
 * Q97b — same-origin proxy for the /settings page's "Test connection"
 * button. The cloud's POST /admin/llm-keys/test runs one cheap "ping"
 * completion per provider; admin-key gated, key never reaches the
 * browser.
 *
 * Body: {provider?: "anthropic" | "openai"} — omit for both.
 */
export const dynamic = "force-dynamic"

function j(body: unknown, status: number): Response {
  return Response.json(body, {
    status,
    headers: { "cache-control": "no-store" },
  })
}

function cloudErrToStatus(e: unknown): number {
  if (e instanceof CloudConfigError) return 503
  const msg = e instanceof Error ? e.message : String(e)
  const m = /cloud (\d{3})/.exec(msg)
  if (m) {
    const upstream = Number(m[1])
    if (upstream === 401 || upstream === 403) return 401
    if (upstream === 422) return 422
    if (upstream === 503) return 503
  }
  return 502
}

export async function POST(req: NextRequest) {
  let body: unknown = {}
  try {
    body = await req.json()
  } catch {
    // empty body is allowed: probes both providers
    body = {}
  }
  if (body == null || typeof body !== "object" || Array.isArray(body)) {
    return j({ error: "body must be an object" }, 400)
  }
  const obj = body as Record<string, unknown>
  let provider: "anthropic" | "openai" | undefined
  if (obj.provider !== undefined && obj.provider !== null) {
    if (obj.provider !== "anthropic" && obj.provider !== "openai") {
      return j({ error: "provider must be anthropic or openai" }, 400)
    }
    provider = obj.provider
  }
  try {
    const r = await cloud.testLlmKeys(provider)
    return j(r, 200)
  } catch (e) {
    return j({ error: "upstream" }, cloudErrToStatus(e))
  }
}
