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

  it("missing api key on server throws sentinel (no env name leak)", async () => {
    delete process.env.MAGI_CP_API_KEY
    global.fetch = vi.fn() as any
    await expect(cloud.ledger()).rejects.toThrow("cloud config error")
    // critical: env var name MUST NOT appear in the user-facing message
    try { await cloud.ledger() } catch (e: any) {
      expect(e.message).not.toContain("MAGI_CP")
    }
  })

  it("missing hitl key on server throws sentinel", async () => {
    delete process.env.MAGI_CP_HITL_API_KEY
    global.fetch = vi.fn() as any
    await expect(cloud.listHitl()).rejects.toThrow("cloud config error")
  })

  // ── v1: policies CRUD via X-Admin-Api-Key ─────────────────────────
  it("listPolicies uses X-Admin-Api-Key", async () => {
    process.env.MAGI_CP_ADMIN_API_KEY = "admin-test"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ items: [] }), { status: 200 }) as any
    })
    await cloud.listPolicies()
    expect(String(captured.url)).toBe("http://test/policies")
    expect(captured.init.headers.get("X-Admin-Api-Key")).toBe("admin-test")
    expect(captured.init.headers.get("X-Api-Key")).toBeNull()
    expect(captured.init.headers.get("X-Hitl-Api-Key")).toBeNull()
  })

  it("getCompiled returns managed_settings + sha", async () => {
    process.env.MAGI_CP_ADMIN_API_KEY = "admin-test"
    global.fetch = vi.fn(async () => new Response(
      JSON.stringify({ managed_settings: { allowManagedHooksOnly: true }, sha256: "deadbeef" }),
      { status: 200 }) as any)
    const r = await cloud.getCompiled("legal-filing/v1")
    expect(r.sha256).toBe("deadbeef")
    expect(r.managed_settings.allowManagedHooksOnly).toBe(true)
  })

  it("setEnabled PATCHes /enabled", async () => {
    process.env.MAGI_CP_ADMIN_API_KEY = "admin-test"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ id: "x", enabled: false }), { status: 200 }) as any
    })
    await cloud.setEnabled("x", false)
    expect(String(captured.url)).toBe("http://test/policies/x/enabled")
    expect(captured.init.method).toBe("PATCH")
    expect(JSON.parse(captured.init.body)).toEqual({ enabled: false })
  })

  it("missing admin key on server throws sentinel (no env name leak)", async () => {
    delete process.env.MAGI_CP_ADMIN_API_KEY
    global.fetch = vi.fn() as any
    await expect(cloud.listPolicies()).rejects.toThrow("cloud config error")
    try { await cloud.listPolicies() } catch (e: any) {
      expect(e.message).not.toContain("MAGI_CP")
    }
  })

  // ── v1.1: presets catalog (no auth) ────────────────────────────────
  it("listPresets sends NO auth header", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ presets: [] }), { status: 200 }) as any
    })
    await cloud.listPresets()
    expect(String(captured.url)).toBe("http://test/presets")
    const h = new Headers(captured.init?.headers || {})
    expect(h.get("X-Api-Key")).toBeNull()
    expect(h.get("X-Admin-Api-Key")).toBeNull()
    expect(h.get("X-Hitl-Api-Key")).toBeNull()
  })

  it("listPresets returns the presets array", async () => {
    global.fetch = vi.fn(async () => new Response(
      JSON.stringify({ presets: [
        { id: "citation-verify", category: "FACT", description: "x",
          enforcement: "enforcing", step: "citation_verify" },
        { id: "answer-quality", category: "ANSWER", description: "y",
          enforcement: "preview", step: null },
      ] }),
      { status: 200 }) as any)
    const r = await cloud.listPresets()
    expect(r).toHaveLength(2)
    expect(r[0].id).toBe("citation-verify")
    expect(r[0].enforcement).toBe("enforcing")
    expect(r[1].step).toBeNull()
  })

  it("listPresets propagates 5xx as cloud N", async () => {
    global.fetch = vi.fn(async () => new Response("", { status: 503 }) as any)
    await expect(cloud.listPresets()).rejects.toThrow("cloud 503")
  })

  // ── v1.2-W1: compilePolicy ─────────────────────────────────────────
  it("compilePolicy uses X-Admin-Api-Key + POSTs nl", async () => {
    process.env.MAGI_CP_ADMIN_API_KEY = "admin-test"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({
        ir: { id: "x/v1" }, review: { ok: true, issues: [] }, schema_issues: [],
      }), { status: 200 }) as any
    })
    await cloud.compilePolicy("법원 filing 정책 강제")
    expect(String(captured.url)).toBe("http://test/policies/compile")
    expect(captured.init.method).toBe("POST")
    expect(captured.init.headers.get("X-Admin-Api-Key")).toBe("admin-test")
    const body = JSON.parse(captured.init.body)
    expect(body.nl).toBe("법원 filing 정책 강제")
    expect(body.prior_turns).toBeNull()
  })

  it("compilePolicy passes prior_turns when given", async () => {
    process.env.MAGI_CP_ADMIN_API_KEY = "admin-test"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = init
      return new Response(JSON.stringify({
        ir: { id: "x/v1" }, review: { ok: true, issues: [] }, schema_issues: [],
      }), { status: 200 }) as any
    })
    await cloud.compilePolicy("Bash 도구만 게이트하자", [
      { role: "user", content: "법률 정책" },
      { role: "assistant", content: "어떤 도구?" },
    ])
    const body = JSON.parse(captured.body)
    expect(body.prior_turns).toHaveLength(2)
    expect(body.prior_turns[0].role).toBe("user")
  })

  it("compilePolicy maps 503 to cloud unreachable on the client", async () => {
    global.fetch = vi.fn(async () => new Response("", { status: 503 }) as any)
    await expect(cloud.compilePolicy("x")).rejects.toThrow("cloud 503")
  })
})
