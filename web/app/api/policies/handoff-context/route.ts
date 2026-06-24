import { NextRequest } from "next/server"

/**
 * D57g: same-origin proxy for "Continue in conversation" handoff.
 *
 * The cloud's POST /policies/handoff-context is admin-key gated; the
 * key never reaches the browser. Like the compile-interactive proxy,
 * this route forwards the wizard / raw editor snapshot to the cloud
 * server-side and returns the seeded first-turn back to the client.
 *
 * The cloud's serializer is OFFLINE (no LLM round-trip) so this route
 * uses a short fetch timeout — there is no provider tail to wait on.
 *
 * Body shape:
 *   {
 *     wizard_state: object | null,
 *     draft_ir: object | null
 *   }
 *
 * Caps mirror the cloud's library limits: each input dict is bounded
 * at MAX_STATE_BYTES so a malformed body 413s / 422s before the cloud
 * round-trip. We do a cheap byte-length check on the JSON-stringified
 * inputs here so the cloud isn't pinned on big drafts.
 */
export const dynamic = "force-dynamic"

const MAX_STATE_BYTES = 16_000
const FETCH_TIMEOUT_MS = 10_000

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

function isPlainObject(x: unknown): x is Record<string, unknown> {
  return x !== null && typeof x === "object" && !Array.isArray(x)
}

export async function POST(req: NextRequest) {
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return j({ error: "invalid body" }, 400)
  }
  if (!isPlainObject(body)) {
    return j({ error: "body must be an object" }, 400)
  }
  const ws = body.wizard_state
  const di = body.draft_ir
  if (ws !== undefined && ws !== null && !isPlainObject(ws)) {
    return j({ error: "wizard_state must be an object" }, 400)
  }
  if (di !== undefined && di !== null && !isPlainObject(di)) {
    return j({ error: "draft_ir must be an object" }, 400)
  }
  // Cheap upper-bound: any single input over MAX_STATE_BYTES bytes is
  // refused before the cloud round-trip. The cloud enforces the same
  // bound canonically; this just shortens the failure path.
  try {
    if (ws && JSON.stringify(ws).length > MAX_STATE_BYTES) {
      return j({ error: "wizard_state too large" }, 413)
    }
    if (di && JSON.stringify(di).length > MAX_STATE_BYTES) {
      return j({ error: "draft_ir too large" }, 413)
    }
  } catch {
    return j({ error: "invalid body" }, 400)
  }

  const key = adminKey()
  if (!key) {
    return j({ error: "server config" }, 503)
  }

  let r: Response
  try {
    r = await fetch(`${cloudUrl()}/policies/handoff-context`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Api-Key": key,
      },
      cache: "no-store",
      body: JSON.stringify({
        wizard_state: ws ?? null,
        draft_ir: di ?? null,
      }),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    })
  } catch (e) {
    console.error("handoff-context proxy fetch failed:", e)
    return j({ error: "upstream" }, 502)
  }

  if (!r.ok) {
    const status = r.status
    const upstreamBody = await r.text().catch(() => "")
    console.error(`cloud ${status} /policies/handoff-context: ${upstreamBody}`)
    if (status === 401 || status === 403) {
      return j({ error: "forbidden" }, status)
    }
    if (status === 422) {
      return j({ error: "invalid_input" }, 422)
    }
    return j({ error: "upstream" }, 502)
  }

  let payload: unknown
  try {
    payload = await r.json()
  } catch {
    return j({ error: "upstream" }, 502)
  }
  return j(payload, 200)
}
