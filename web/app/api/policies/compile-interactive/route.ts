import { NextRequest } from "next/server"
import { isSameOrigin } from "@/lib/same-origin"

/**
 * D55b: same-origin proxy for the Conversational compose UI.
 *
 * The cloud's POST /policies/compile-interactive is admin-key gated;
 * the key never reaches the browser. This route forwards the
 * conversational turn body (history + draft_so_far + answers) to the
 * cloud server-side, keeps the key on stderr / env, and returns the
 * server's next turn back to the client.
 *
 * Why this route does NOT delegate to lib/cloud.ts's `_fetch`:
 *   - `_fetch` swallows upstream response bodies and re-throws
 *     `cloud {status}`, so the dashboard could not distinguish a 503
 *     "providers not configured" from a 502 cloud crash. The brief's
 *     D52e hotfix 2 maps `provider_unconfigured` to an actionable
 *     copy, so we MUST preserve the upstream classification.
 *   - We inspect the upstream body for the "providers not configured"
 *     marker (the cloud raises this verbatim from
 *     `policies_compile_interactive`) and return a stable code the
 *     client component matches on.
 *
 * Body shape:
 *   {
 *     history: [{role: "user"|"assistant", content: string}, ...],
 *     draft_so_far: object | null,
 *     answers: {[qid: string]: string} | null
 *   }
 *
 * Caps mirror D55a's library limits (MAX_HISTORY_TURNS=16,
 * MAX_USER_MESSAGE_CHARS=2000). We enforce here too so a malformed
 * body 400s before the cloud round-trip.
 */
export const dynamic = "force-dynamic"

const MAX_HISTORY_TURNS = 16
const MAX_USER_MESSAGE_CHARS = 2_000
const MAX_ANSWERS = 8
const MAX_ANSWER_KEY_CHARS = 64
const MAX_ANSWER_VALUE_CHARS = 2_000
const FETCH_TIMEOUT_MS = 90_000

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
    return Response.json(
      { error: "cross-origin request rejected" },
      { status: 403, headers: { "cache-control": "no-store" } },
    )
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

  // ── history ────────────────────────────────────────────────────────
  const histRaw = obj.history
  let history: { role: "user" | "assistant"; content: string }[] | null = null
  if (histRaw !== undefined && histRaw !== null) {
    if (!Array.isArray(histRaw)) {
      return j({ error: "history must be an array" }, 400)
    }
    if (histRaw.length > MAX_HISTORY_TURNS) {
      return j({ error: "history too long" }, 400)
    }
    history = []
    for (let i = 0; i < histRaw.length; i++) {
      const t = histRaw[i]
      if (t == null || typeof t !== "object" || Array.isArray(t)) {
        return j({ error: `history[${i}] must be an object` }, 400)
      }
      const tt = t as Record<string, unknown>
      if (tt.role !== "user" && tt.role !== "assistant") {
        return j({ error: `history[${i}].role invalid` }, 400)
      }
      if (typeof tt.content !== "string") {
        return j({ error: `history[${i}].content must be a string` }, 400)
      }
      if (tt.content.length > MAX_USER_MESSAGE_CHARS * 5) {
        // Soft outer cap, the per-role cap is enforced on the cloud
        // side. We just refuse multi-megabyte payloads here.
        return j({ error: `history[${i}].content too long` }, 400)
      }
      history.push({ role: tt.role, content: tt.content })
    }
  }

  // ── draft_so_far ───────────────────────────────────────────────────
  const draft = obj.draft_so_far
  if (
    draft !== undefined && draft !== null &&
    (typeof draft !== "object" || Array.isArray(draft))
  ) {
    return j({ error: "draft_so_far must be an object" }, 400)
  }

  // ── answers ────────────────────────────────────────────────────────
  const answers = obj.answers
  if (
    answers !== undefined && answers !== null &&
    (typeof answers !== "object" || Array.isArray(answers))
  ) {
    return j({ error: "answers must be an object" }, 400)
  }
  if (answers && typeof answers === "object") {
    const a = answers as Record<string, unknown>
    const keys = Object.keys(a)
    if (keys.length > MAX_ANSWERS) {
      return j({ error: "answers too many keys" }, 400)
    }
    for (const k of keys) {
      if (k.length > MAX_ANSWER_KEY_CHARS) {
        return j({ error: "answer key too long" }, 400)
      }
      const v = a[k]
      if (typeof v !== "string") {
        return j({ error: `answer ${k} must be a string` }, 400)
      }
      if (v.length > MAX_ANSWER_VALUE_CHARS) {
        return j({ error: `answer ${k} too long` }, 400)
      }
    }
  }

  // ── runtime_id ─────────────────────────────────────────────────────
  // PR-6: optional runtime override. Must be one of the two known
  // runtimes or absent; anything else is an operator error (400).
  const KNOWN_RUNTIMES = ["claude-code", "codex"] as const
  type KnownRuntime = (typeof KNOWN_RUNTIMES)[number]
  const rawRuntimeId = obj.runtime_id
  let runtimeId: KnownRuntime | null = null
  if (rawRuntimeId !== undefined && rawRuntimeId !== null) {
    if (
      typeof rawRuntimeId !== "string" ||
      !(KNOWN_RUNTIMES as readonly string[]).includes(rawRuntimeId)
    ) {
      return j(
        { error: "invalid body", detail: "runtime_id must be claude-code or codex" },
        400,
      )
    }
    runtimeId = rawRuntimeId as KnownRuntime
  }

  const key = adminKey()
  if (!key) {
    return j({ error: "server config" }, 503)
  }

  let r: Response
  try {
    r = await fetch(`${cloudUrl()}/policies/compile-interactive`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Api-Key": key,
      },
      cache: "no-store",
      body: JSON.stringify({
        history,
        draft_so_far: draft ?? null,
        answers: answers ?? null,
        runtime_id: runtimeId ?? null,
      }),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    })
  } catch (e) {
    console.error("compile-interactive proxy fetch failed:", e)
    return j({ error: "upstream" }, 502)
  }

  if (!r.ok) {
    const status = r.status
    // Pull the upstream body so we can classify 503 providers-not-configured
    // vs 503 server-config. Body is server-only; we strip it before returning.
    const upstreamBody = await r.text().catch(() => "")
    console.error(`cloud ${status} /policies/compile-interactive: ${upstreamBody}`)
    if (status === 503) {
      // The cloud raises "LLM providers not configured on this deployment"
      // verbatim. Classify so the client renders the actionable banner.
      if (/provider.*not configured/i.test(upstreamBody)) {
        return j({ error: "provider_unconfigured" }, 503)
      }
      return j({ error: "server config" }, 503)
    }
    // A configured provider that fails (wrong key, rate-limit, network error)
    // returns 502 with "LLM provider error: ..." from the cloud. Classify it
    // separately from infrastructure-level 502s so the dashboard can show an
    // actionable "check your API key / quota" flash (R5-01).
    if (status === 502 && /llm provider error/i.test(upstreamBody)) {
      return j({ error: "provider_error" }, 502)
    }
    if (status === 422) {
      return j({ error: "invalid_input" }, 422)
    }
    if (status === 401 || status === 403) {
      return j({ error: "forbidden" }, status)
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
