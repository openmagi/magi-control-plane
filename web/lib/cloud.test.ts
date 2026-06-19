import { describe, it, expect, vi, beforeEach } from "vitest"
import { cloud } from "./cloud"

describe("cloud client", () => {
  beforeEach(() => {
    process.env.MAGI_CP_CLOUD_URL = "http://test"
    process.env.MAGI_CP_API_KEY = "api-test"
    process.env.MAGI_CP_HITL_API_KEY = "hitl-test"
  })

  it("listHitl sends X-Hitl-Api-Key", async () => {
    const calls: { url: string; init: any }[] = []
    global.fetch = vi.fn(async (url: any, init: any) => {
      calls.push({ url: String(url), init })
      return new Response(JSON.stringify({ items: [] }), { status: 200 }) as any
    })
    await cloud.listHitl()
    expect(calls[0].url).toBe("http://test/hitl")
    expect(calls[0].init.headers.get("X-Hitl-Api-Key")).toBe("hitl-test")
    expect(calls[0].init.headers.get("X-Api-Key")).toBeNull()
  })

  it("ledger uses X-Api-Key and paginates", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ chain_ok: true, next_since_id: 0, entries: [] }),
                          { status: 200 }) as any
    })
    await cloud.ledger(42, 10)
    expect(String(captured.url)).toBe("http://test/ledger?since_id=42&limit=10")
    expect(captured.init.headers.get("X-Api-Key")).toBe("api-test")
  })

  it("throws on non-200 with redacted message (no body leak)", async () => {
    global.fetch = vi.fn(async () => new Response("internal token leaked", { status: 401 }) as any)
    try {
      await cloud.listHitl()
      expect.fail("should have thrown")
    } catch (e) {
      const msg = (e as Error).message
      expect(msg).toBe("cloud 401")           // status only
      expect(msg).not.toContain("internal")    // body not echoed to caller
    }
  })

  it("attaches AbortSignal for timeout", async () => {
    let captured: any
    global.fetch = vi.fn(async (_url: any, init: any) => {
      captured = init
      return new Response(JSON.stringify({ items: [] }), { status: 200 }) as any
    })
    await cloud.listHitl()
    expect(captured.signal).toBeDefined()
  })

  it("approve POSTs JSON body to /hitl/:id/approve", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({}), { status: 200 }) as any
    })
    await cloud.approve(7, "p@firm.example", "ok")
    expect(captured.url).toBe("http://test/hitl/7/approve")
    expect(captured.init.method).toBe("POST")
    expect(JSON.parse(captured.init.body)).toEqual({ approver: "p@firm.example", note: "ok" })
  })

  it("missing api key on server throws clearly", async () => {
    delete process.env.MAGI_CP_API_KEY
    global.fetch = vi.fn() as any
    await expect(cloud.ledger()).rejects.toThrow(/MAGI_CP_API_KEY/)
  })

  it("missing hitl key on server throws clearly", async () => {
    delete process.env.MAGI_CP_HITL_API_KEY
    global.fetch = vi.fn() as any
    await expect(cloud.listHitl()).rejects.toThrow(/MAGI_CP_HITL_API_KEY/)
  })
})
