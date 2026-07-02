import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"
import { isSameOrigin } from "@/lib/same-origin"

/**
 * D77 — same-origin proxy for the synthetic CC hook payload simulator.
 *
 * The cloud's `/policies/{id}/test` and `/policy-packs/{id}/test`
 * endpoints are admin-key gated. The dashboard never ships the admin
 * key to the browser; this proxy forwards the request server-side
 * (cloud.ts reads the key from env).
 *
 * Body shape:
 *   { kind: "policy" | "pack",
 *     id: string,
 *     payload: object,
 *     event?: string }
 *
 * Errors surface as JSON with a 4xx / 5xx status so the client can
 * render an inline friendly message. Upstream cloud error text is
 * never echoed back (logged to server stderr only).
 */
export const dynamic = "force-dynamic"

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
  const kind = obj.kind
  if (kind !== "policy" && kind !== "pack") {
    return Response.json(
      { error: "kind must be 'policy' or 'pack'" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  const id = obj.id
  if (typeof id !== "string" || !id) {
    return Response.json(
      { error: "id is required" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  const payload = obj.payload
  if (payload == null || typeof payload !== "object" || Array.isArray(payload)) {
    return Response.json(
      { error: "payload must be an object" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  const event = obj.event
  if (event !== undefined && typeof event !== "string") {
    return Response.json(
      { error: "event must be a string" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }

  try {
    const out = kind === "policy"
      ? await cloud.testPolicy(
        id, payload as Record<string, unknown>, event as string | undefined,
      )
      : await cloud.testPack(
        id, payload as Record<string, unknown>, event as string | undefined,
      )
    return Response.json(out, { headers: { "cache-control": "no-store" } })
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "server config" },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    const msg = e instanceof Error ? e.message : String(e)
    const m = /cloud (\d{3})/.exec(msg)
    const upstream = m ? Number(m[1]) : 0
    if (upstream === 404) {
      return Response.json(
        { error: "not found" },
        { status: 404, headers: { "cache-control": "no-store" } },
      )
    }
    if (upstream === 422) {
      return Response.json(
        { error: "invalid payload" },
        { status: 422, headers: { "cache-control": "no-store" } },
      )
    }
    return Response.json(
      { error: "upstream" },
      { status: 502, headers: { "cache-control": "no-store" } },
    )
  }
}
