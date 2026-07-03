import { NextRequest } from "next/server"
import { isSameOrigin } from "@/lib/same-origin"

/**
 * Same-origin proxy for the policy-integrity review.
 *
 * The cloud's POST /policies/review is admin-key gated; the key never
 * reaches the browser. The Conversational compose UI calls this route
 * with {draft, intent} once a draft is ready to save, and renders the
 * returned verdict ({ok, issues, summary}) before the operator commits.
 *
 * The review is advisory: it never blocks Save. The endpoint works with
 * no reviewer LLM configured (deterministic structural checks only), so
 * this route stays useful on a key-less deployment.
 */
export const dynamic = "force-dynamic"

const MAX_INTENT_CHARS = 4_000
const MAX_DRAFT_BYTES = 64_000
const FETCH_TIMEOUT_MS = 60_000

function cloudUrl(): string {
  return process.env.MAGI_CP_CLOUD_URL || "http://127.0.0.1:8787"
}

function adminKey(): string | null {
  const k = process.env.MAGI_CP_ADMIN_API_KEY
  if (!k) {
    console.error("dashboard server: MAGI_CP_ADMIN_API_KEY not set")
    return null
  }
  return k
}

function j(body: unknown, status: number): Response {
  return Response.json(body, {
    status,
    headers: { "cache-control": "no-store" },
  })
}

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) {
    return j({ error: "cross-origin request rejected" }, 403)
  }
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return j({ error: "invalid body" }, 400)
  }
  if (body == null || typeof body !== "object" || Array.isArray(body)) {
    return j({ error: "body must be an object" }, 400)
  }
  const obj = body as Record<string, unknown>

  const draft = obj.draft
  if (draft == null || typeof draft !== "object" || Array.isArray(draft)) {
    return j({ error: "draft must be an object" }, 400)
  }
  if (JSON.stringify(draft).length > MAX_DRAFT_BYTES) {
    return j({ error: "draft too large" }, 400)
  }
  let intent = ""
  if (obj.intent !== undefined && obj.intent !== null) {
    if (typeof obj.intent !== "string") {
      return j({ error: "intent must be a string" }, 400)
    }
    intent = obj.intent.slice(0, MAX_INTENT_CHARS)
  }

  const key = adminKey()
  if (!key) {
    return j({ error: "server config" }, 503)
  }

  let r: Response
  try {
    r = await fetch(`${cloudUrl()}/policies/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Api-Key": key },
      cache: "no-store",
      body: JSON.stringify({ draft, intent }),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    })
  } catch (e) {
    console.error("policies/review proxy fetch failed:", e)
    return j({ error: "upstream" }, 502)
  }
  if (!r.ok) {
    return j({ error: `cloud ${r.status}` }, r.status === 503 ? 503 : 502)
  }
  const data = await r.json().catch(() => null)
  if (data == null) {
    return j({ error: "upstream returned no body" }, 502)
  }
  return j(data, 200)
}
