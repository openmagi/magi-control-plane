import { NextRequest } from "next/server"
import { cloud, CloudConfigError, type ScriptRuntime } from "@/lib/cloud"

/**
 * D63 — same-origin proxy for script uploads.
 *
 * The wizard's "Attach a script file" lane POSTs `multipart/form-data`
 * here. We decode the file bytes server-side and forward to the cloud's
 * JSON-only `/scripts` endpoint as `{name, runtime, body_b64}`. This
 * keeps the cloud free of a `python-multipart` dep while still giving
 * the browser the native `<input type="file">` UX.
 *
 * GET returns the script list (proxied; admin key never reaches the
 * browser). DELETE removes a script by id.
 */
export const dynamic = "force-dynamic"

const ALLOWED_RUNTIMES = new Set<ScriptRuntime>(["bash", "python3", "node"])
const MAX_BODY_BYTES = 64 * 1024

export async function GET() {
  try {
    const data = await cloud.listScripts()
    return Response.json(data, {
      headers: { "cache-control": "no-store" },
    })
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "cloud not configured" },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    const msg = e instanceof Error ? e.message : String(e)
    return Response.json(
      { error: msg },
      { status: 502, headers: { "cache-control": "no-store" } },
    )
  }
}

export async function POST(req: NextRequest) {
  const form = await req.formData().catch(() => null)
  if (form == null) {
    return Response.json(
      { error: "expected multipart/form-data" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  const file = form.get("file")
  const name = form.get("name")
  const runtime = form.get("runtime")
  if (typeof name !== "string" || !name.trim()) {
    return Response.json(
      { error: "name is required" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  if (typeof runtime !== "string"
        || !ALLOWED_RUNTIMES.has(runtime as ScriptRuntime)) {
    return Response.json(
      { error: "runtime must be bash | python3 | node" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  if (!(file instanceof File) || file.size === 0) {
    return Response.json(
      { error: "file is required" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  if (file.size > MAX_BODY_BYTES) {
    return Response.json(
      { error: `file too large (max ${MAX_BODY_BYTES} bytes)` },
      { status: 413, headers: { "cache-control": "no-store" } },
    )
  }
  const buf = Buffer.from(await file.arrayBuffer())
  const body_b64 = buf.toString("base64")
  try {
    const data = await cloud.uploadScript({
      name: name.trim(),
      runtime: runtime as ScriptRuntime,
      body_b64,
    })
    return Response.json(data, {
      headers: { "cache-control": "no-store" },
    })
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "cloud not configured" },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    const msg = e instanceof Error ? e.message : String(e)
    // Surface the cloud's 403 (env-gated) and 409 (name conflict) as
    // matching client-side statuses so the wizard can render an
    // intentional message instead of a generic 502.
    let status = 502
    if (/\b403\b/.test(msg)) status = 403
    if (/\b409\b/.test(msg)) status = 409
    if (/\b422\b/.test(msg)) status = 422
    return Response.json(
      { error: msg },
      { status, headers: { "cache-control": "no-store" } },
    )
  }
}

export async function DELETE(req: NextRequest) {
  const u = new URL(req.url)
  const id = u.searchParams.get("id")?.trim() ?? ""
  if (!id) {
    return Response.json(
      { error: "id query param is required" },
      { status: 400, headers: { "cache-control": "no-store" } },
    )
  }
  try {
    const data = await cloud.deleteScript(id)
    return Response.json(data, {
      headers: { "cache-control": "no-store" },
    })
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "cloud not configured" },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    const msg = e instanceof Error ? e.message : String(e)
    let status = 502
    if (/\b403\b/.test(msg)) status = 403
    if (/\b404\b/.test(msg)) status = 404
    if (/\b409\b/.test(msg)) status = 409
    return Response.json(
      { error: msg },
      { status, headers: { "cache-control": "no-store" } },
    )
  }
}
