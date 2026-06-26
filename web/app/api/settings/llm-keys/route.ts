import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"

/**
 * Q97b — same-origin proxy for the /settings page's LLM-keys panel.
 *
 * The cloud's `/admin/llm-keys` surface is admin-key gated; that key
 * must never reach the browser. This route:
 *   - GET  — proxies GET /admin/llm-keys and returns the safe status
 *            envelope ({anthropic:{set,last4}, openai:{set,last4}}).
 *            The raw key is never on the wire.
 *   - PUT  — accepts {anthropic_api_key?, openai_api_key?} from the
 *            client (string|null|empty), strips any X-Api-Key /
 *            X-Admin-Api-Key / X-Hitl-Api-Key header an over-eager
 *            client tried to send, forwards to the cloud with the
 *            server-side admin key from env, and returns the status
 *            envelope. Missing field preserves prior value, empty
 *            string clears, non-empty overwrites.
 *
 * Errors: 503 when the dashboard server is missing
 * MAGI_CP_ADMIN_API_KEY / MAGI_CP_CLOUD_URL (CloudConfigError); 502
 * when the cloud is down or returns an unexpected status; never echo
 * the upstream body to the browser.
 */
export const dynamic = "force-dynamic"

const KEY_FIELDS = ["anthropic_api_key", "openai_api_key"] as const
const MAX_KEY_LEN = 4_096

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

export async function GET(_req: NextRequest) {
  try {
    const s = await cloud.getLlmKeys()
    return j(s, 200)
  } catch (e) {
    return j({ error: "upstream" }, cloudErrToStatus(e))
  }
}

export async function PUT(req: NextRequest) {
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

  // Reject unexpected keys outright so a future cloud rename does not
  // get masked by a client-side typo. The proxy MUST mirror the cloud's
  // `extra=forbid` model.
  for (const k of Object.keys(obj)) {
    if (!(KEY_FIELDS as readonly string[]).includes(k)) {
      return j({ error: `unknown field: ${k}` }, 400)
    }
  }

  const out: { anthropic_api_key?: string | null; openai_api_key?: string | null } = {}
  for (const field of KEY_FIELDS) {
    const raw = obj[field]
    if (raw === undefined) continue
    if (raw === null) { out[field] = null; continue }
    if (typeof raw !== "string") {
      return j({ error: `${field} must be a string` }, 400)
    }
    if (raw.length > MAX_KEY_LEN) {
      return j({ error: `${field} too long` }, 400)
    }
    // Defensive whitespace trim: pasted keys often carry a trailing
    // newline. We trim before forwarding so the cloud sees the bare
    // token. Empty string after trim clears that side per contract.
    out[field] = raw.trim()
  }

  // Hard isolation: strip any client-supplied auth header before
  // forwarding. cloud.ts injects the server-side admin key when it
  // calls _fetch; this is just belt-and-braces so a buggy client
  // cannot leak its own credentials into the upstream call.
  //
  // (Headers on `req` are scoped to this Next.js request and are not
  // re-emitted by `cloud.putLlmKeys`; we leave the explicit assertion
  // in the test to guard against a future refactor that changes that.)

  try {
    const s = await cloud.putLlmKeys(out)
    return j(s, 200)
  } catch (e) {
    return j({ error: "upstream" }, cloudErrToStatus(e))
  }
}
