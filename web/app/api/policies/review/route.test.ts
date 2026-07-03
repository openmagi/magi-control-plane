import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"

/**
 * Same-origin proxy for the policy-integrity review. The cloud call is
 * mocked; we exercise the request shape, byte-size guard, and admin-key
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
  const mod = await import("./route")
  const req = new Request("http://localhost/api/policies/review", {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  })
  return mod.POST(req as unknown as Parameters<typeof mod.POST>[0])
}

describe("/api/policies/review proxy", () => {
  it("forwards {draft, intent} to the cloud and returns the verdict", async () => {
    const verdict = { ok: true, summary: "looks good", issues: [] }
    const fetchSpy = vi.fn(
      async (_url: string, _init: RequestInit) => new Response(JSON.stringify(verdict), {
        status: 200, headers: { "Content-Type": "application/json" },
      }),
    )
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    const r = await callRoute({
      draft: { type: "evidence_gate", id: "verified-trade" },
      intent: "block trades without a credible source",
    })
    expect(r.status).toBe(200)
    expect(await r.json()).toEqual(verdict)
    const [sentUrl, init] = fetchSpy.mock.calls[0]!
    expect(sentUrl).toBe("http://127.0.0.1:8787/policies/review")
    const headers = (init as RequestInit).headers as Record<string, string>
    expect(headers["X-Admin-Api-Key"]).toBe("test-admin-key")
    const sentBody = JSON.parse((init as RequestInit).body as string)
    expect(sentBody.draft.id).toBe("verified-trade")
    expect(sentBody.intent).toBe("block trades without a credible source")
  })

  it("returns 400 when draft is missing/not an object", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    expect((await callRoute({ intent: "x" })).status).toBe(400)
    expect((await callRoute({ draft: [1, 2] })).status).toBe(400)
  })

  it("returns 400 when the draft exceeds the byte cap", async () => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    const r = await callRoute({ draft: { blob: "x".repeat(70_000) } })
    expect(r.status).toBe(400)
  })

  it("returns 503 when admin key is unset (cloud never contacted)", async () => {
    delete process.env.MAGI_CP_ADMIN_API_KEY
    const fetchSpy = vi.fn()
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    const r = await callRoute({ draft: { id: "x" } })
    expect(r.status).toBe(503)
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
