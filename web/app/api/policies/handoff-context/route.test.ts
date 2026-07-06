import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"

/**
 * D57g: same-origin proxy validation. The cloud call is mocked; we
 * exercise the request shape, byte-size guard, and admin-key
 * suppression boundary.
 */

const OLD_FETCH = globalThis.fetch
const OLD_ENV = { ...process.env }

beforeEach(() => {
  process.env.MAGI_CP_CLOUD_URL = "http://127.0.0.1:8787"
  process.env.MAGI_CP_ADMIN_API_KEY = "test-admin-key"
})

afterEach(() => {
  globalThis.fetch = OLD_FETCH
  process.env = { ...OLD_ENV }
  vi.restoreAllMocks()
})

async function callRoute(body: unknown): Promise<Response> {
  // Lazy import so env vars are picked up after beforeEach.
  const mod = await import("./route")
  const req = new Request("http://localhost/api/policies/handoff-context", {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  })
  // The Next route is permissive about NextRequest's shape; cast.
  return mod.POST(req as unknown as Parameters<typeof mod.POST>[0])
}

describe("/api/policies/handoff-context proxy", () => {
  it("forwards a valid body to the cloud and returns the response", async () => {
    const cloudPayload = {
      assistant_message: "Continuing from where you were.",
      draft: { id: "block-bash" },
      missing_fields: ["id"],
      questions: [],
      needs_more: false,
      ready_to_save: true,
    }
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(
        JSON.stringify(cloudPayload),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    const r = await callRoute({
      wizard_state: { lifecycle: "before_tool_use", toolScope: "Bash" },
      draft_ir: null,
    })
    expect(r.status).toBe(200)
    const body = await r.json()
    expect(body).toEqual(cloudPayload)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [sentUrl, init] = fetchSpy.mock.calls[0]!
    expect(sentUrl).toBe("http://127.0.0.1:8787/policies/handoff-context")
    const headers = init.headers as Record<string, string>
    expect(headers["X-Admin-Api-Key"]).toBe("test-admin-key")
    // Body should be a stringified shape with both keys preserved.
    const sentBody = JSON.parse(init.body as string)
    expect(sentBody.wizard_state).toEqual({
      lifecycle: "before_tool_use", toolScope: "Bash",
    })
    expect(sentBody.draft_ir).toBeNull()
  })

  it("returns 400 on a non-object body", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const r = await callRoute([1, 2, 3])
    expect(r.status).toBe(400)
  })

  it("returns 400 when wizard_state is not an object", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const r = await callRoute({ wizard_state: "nope", draft_ir: null })
    expect(r.status).toBe(400)
  })

  it("returns 413 when wizard_state exceeds the byte cap", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const huge = "x".repeat(20_000)
    const r = await callRoute({
      wizard_state: { description: huge },
      draft_ir: null,
    })
    expect(r.status).toBe(413)
  })

  it("returns 503 when admin key is unset", async () => {
    delete process.env.MAGI_CP_ADMIN_API_KEY
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const r = await callRoute({ wizard_state: {}, draft_ir: null })
    expect(r.status).toBe(503)
    // The cloud was never contacted.
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mock.calls.length).toBe(0)
  })

  it("maps cloud 422 to 422 invalid_input", async () => {
    globalThis.fetch = (vi.fn(async () => new Response(
      JSON.stringify({ detail: "wizard_state too large" }),
      { status: 422 },
    )) as unknown) as typeof fetch
    const r = await callRoute({
      wizard_state: { lifecycle: "before_tool_use" },
      draft_ir: null,
    })
    expect(r.status).toBe(422)
    const body = await r.json()
    expect(body.error).toBe("invalid_input")
  })

  it("maps cloud 403 to 403 forbidden", async () => {
    globalThis.fetch = (vi.fn(async () => new Response(
      "{}", { status: 403 },
    )) as unknown) as typeof fetch
    const r = await callRoute({
      wizard_state: {},
      draft_ir: null,
    })
    expect(r.status).toBe(403)
    const body = await r.json()
    expect(body.error).toBe("forbidden")
  })

  it("maps cloud 502 / network errors to upstream 502", async () => {
    globalThis.fetch = (vi.fn(async () => {
      throw new Error("ECONNREFUSED")
    }) as unknown) as typeof fetch
    const r = await callRoute({
      wizard_state: {},
      draft_ir: null,
    })
    expect(r.status).toBe(502)
    const body = await r.json()
    expect(body.error).toBe("upstream")
  })

  it("forwards origin and locale fields to the cloud", async () => {
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(
        JSON.stringify({}), { status: 200 },
      ),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    const r = await callRoute({
      wizard_state: { lifecycle: "before_tool_use" },
      draft_ir: null,
      origin: "advanced",
      locale: "ko",
    })
    expect(r.status).toBe(200)
    const [, init] = fetchSpy.mock.calls[0]!
    const sentBody = JSON.parse(init.body as string)
    expect(sentBody.origin).toBe("advanced")
    expect(sentBody.locale).toBe("ko")
  })

  it("silently drops unknown origin / locale values (cloud has extra=forbid)", async () => {
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(
        JSON.stringify({}), { status: 200 },
      ),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    await callRoute({
      wizard_state: {},
      draft_ir: null,
      origin: "junk",
      locale: "ja",
    })
    const [, init] = fetchSpy.mock.calls[0]!
    const sentBody = JSON.parse(init.body as string)
    expect(sentBody.origin).toBeUndefined()
    expect(sentBody.locale).toBeUndefined()
  })

  it("byte cap counts UTF-8 bytes, not JS code units (Hangul does not slip past)", async () => {
    // 6000 Hangul chars = 18000 UTF-8 bytes — exceeds the 16k cap but
    // would have passed a char-count check (6000 < 16000). The fix
    // catches it at the proxy boundary so the cloud round-trip is
    // skipped.
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const hangul = "한".repeat(6000)
    const r = await callRoute({
      wizard_state: { description: hangul },
      draft_ir: null,
    })
    expect(r.status).toBe(413)
    // The cloud must NOT have been contacted.
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mock.calls.length).toBe(0)
  })

  // ── runtime_id forwarding (PR-7) ─────────────────────────────────────

  it("forwards valid runtime_id to the cloud", async () => {
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(
        JSON.stringify({ assistant_message: "ok", feasibility: null }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    const r = await callRoute({
      wizard_state: { lifecycle: "before_tool_use", toolScope: "Read" },
      draft_ir: null,
      runtime_id: "codex",
    })
    expect(r.status).toBe(200)
    const [, init] = fetchSpy.mock.calls[0]!
    const sentBody = JSON.parse(init.body as string)
    expect(sentBody.runtime_id).toBe("codex")
  })

  it("returns 400 for an invalid runtime_id value", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const r = await callRoute({
      wizard_state: {},
      draft_ir: null,
      runtime_id: "gpt-4o",
    })
    expect(r.status).toBe(400)
    const body = await r.json()
    expect(body.error).toBe("invalid body")
    // Cloud must NOT have been contacted.
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mock.calls.length).toBe(0)
  })

  it("omits runtime_id from cloud body when absent in request", async () => {
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(
        JSON.stringify({}), { status: 200 },
      ),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    await callRoute({
      wizard_state: {},
      draft_ir: null,
    })
    const [, init] = fetchSpy.mock.calls[0]!
    const sentBody = JSON.parse(init.body as string)
    expect(sentBody.runtime_id).toBeUndefined()
  })

  it("accepts claude-code as a valid runtime_id", async () => {
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(
        JSON.stringify({}), { status: 200 },
      ),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    const r = await callRoute({
      wizard_state: {},
      draft_ir: null,
      runtime_id: "claude-code",
    })
    expect(r.status).toBe(200)
    const [, init] = fetchSpy.mock.calls[0]!
    const sentBody = JSON.parse(init.body as string)
    expect(sentBody.runtime_id).toBe("claude-code")
  })
})
