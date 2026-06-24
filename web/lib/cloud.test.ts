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

  // ── D52c: verifier filter on /ledger + /ledger/count ──────────────
  it("ledger appends repeated `verifier=` params when filter provided", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({
        chain_ok: true, next_since_id: 0, entries: [],
      }), { status: 200 }) as any
    })
    await cloud.ledger(0, 50, ["citation_verify", "privilege_scan"])
    const u = new URL(String(captured.url))
    expect(u.pathname).toBe("/ledger")
    expect(u.searchParams.get("since_id")).toBe("0")
    expect(u.searchParams.get("limit")).toBe("50")
    expect(u.searchParams.getAll("verifier")).toEqual([
      "citation_verify", "privilege_scan",
    ])
  })

  it("ledger omits the verifier param when filter is empty or undefined", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({
        chain_ok: true, next_since_id: 0, entries: [],
      }), { status: 200 }) as any
    })
    await cloud.ledger(0, 50, [])
    const u = new URL(String(captured.url))
    // Empty filter array -> no verifier params (server treats as
    // "no filter" same as omitting the query).
    expect(u.searchParams.getAll("verifier")).toEqual([])
  })

  it("ledgerCount hits /ledger/count with the given filter + window", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ count: 7 }), { status: 200 }) as any
    })
    const r = await cloud.ledgerCount("citation_verify", 24 * 60 * 60)
    const u = new URL(String(captured.url))
    expect(u.pathname).toBe("/ledger/count")
    expect(u.searchParams.get("verifier")).toBe("citation_verify")
    expect(u.searchParams.get("since_secs")).toBe("86400")
    expect(captured.init.headers.get("X-Api-Key")).toBe("api-test")
    expect(r).toEqual({ count: 7 })
  })

  it("ledgerCount drops non-positive since_secs", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ count: 0 }), { status: 200 }) as any
    })
    await cloud.ledgerCount("nope", 0)
    const u = new URL(String(captured.url))
    expect(u.searchParams.get("since_secs")).toBeNull()
  })

  it("ledgerCount with no args hits the bare endpoint", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ count: 0 }), { status: 200 }) as any
    })
    await cloud.ledgerCount()
    expect(String(captured.url)).toBe("http://test/ledger/count")
  })

  // ── D53a: listVerifierSamples ─────────────────────────────────────
  it("listVerifierSamples hits /ledger/samples with the given filter", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ samples: [] }), { status: 200 }) as any
    })
    const r = await cloud.listVerifierSamples("citation_verify", 5)
    const u = new URL(String(captured.url))
    expect(u.pathname).toBe("/ledger/samples")
    expect(u.searchParams.get("verifier")).toBe("citation_verify")
    expect(u.searchParams.get("limit")).toBe("5")
    expect(u.searchParams.get("since_secs")).toBe("86400")
    expect(captured.init.headers.get("X-Api-Key")).toBe("api-test")
    expect(r).toEqual({ samples: [] })
  })

  it("listVerifierSamples clamps limit to [1, 25]", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ samples: [] }), { status: 200 }) as any
    })
    await cloud.listVerifierSamples("x", 999)
    let u = new URL(String(captured.url))
    expect(u.searchParams.get("limit")).toBe("25")
    await cloud.listVerifierSamples("x", -10)
    u = new URL(String(captured.url))
    expect(u.searchParams.get("limit")).toBe("1")
  })

  it("listVerifierSamples drops non-positive sinceSecs", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ samples: [] }), { status: 200 }) as any
    })
    await cloud.listVerifierSamples("x", 5, 0)
    const u = new URL(String(captured.url))
    expect(u.searchParams.get("since_secs")).toBeNull()
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

  // ── verifiers catalog (was /presets in v1.1, now /verifiers with auth
  //    so the backend can merge tenant-scoped custom verifiers) ──────
  it("listVerifiers hits /verifiers with the tenant key", async () => {
    process.env.MAGI_CP_API_KEY = "tenant-test"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ presets: [] }), { status: 200 }) as any
    })
    await cloud.listVerifiers()
    expect(String(captured.url)).toBe("http://test/verifiers")
    const h = new Headers(captured.init?.headers || {})
    expect(h.get("X-Api-Key")).toBe("tenant-test")
    expect(h.get("X-Admin-Api-Key")).toBeNull()
  })

  it("listVerifiers returns the verifier array", async () => {
    process.env.MAGI_CP_API_KEY = "tenant-test"
    global.fetch = vi.fn(async () => new Response(
      JSON.stringify({ presets: [
        { id: "citation-verify", category: "FACT", description: "x",
          enforcement: "enforcing", step: "citation_verify" },
        { id: "answer-quality", category: "ANSWER", description: "y",
          enforcement: "preview", step: null },
      ] }),
      { status: 200 }) as any)
    const r = await cloud.listVerifiers()
    expect(r).toHaveLength(2)
    expect(r[0].id).toBe("citation-verify")
    expect(r[1].step).toBeNull()
  })

  // ── /catalog/*. pure-derivation catalogs (no separate storage) ────
  it("listEvidenceTypes hits /catalog/evidence-types with tenant key", async () => {
    process.env.MAGI_CP_API_KEY = "tenant-test"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url, init }
      return new Response(JSON.stringify({ items: [
        { step: "citation_verify", category: "FACT", description: "",
          enforcement: "enforcing", name: null, source: "builtin",
          used_by_policies: [] },
      ] }), { status: 200 }) as any
    })
    const r = await cloud.listEvidenceTypes()
    expect(String(captured.url)).toBe("http://test/catalog/evidence-types")
    expect(new Headers(captured.init?.headers || {}).get("X-Api-Key")).toBe("tenant-test")
    expect(r).toHaveLength(1)
    expect(r[0].source).toBe("builtin")
  })

  it("listConditions hits /catalog/conditions and parses kind/value rows", async () => {
    process.env.MAGI_CP_API_KEY = "tenant-test"
    global.fetch = vi.fn(async () => new Response(JSON.stringify({ items: [
      { kind: "sentinel_re", value: "AKIA[0-9A-Z]{16}", policy_id: "x/v1",
        trigger_event: "PostToolUse", tool_matcher: "Bash" },
      { kind: "tool_match", value: "Bash", policy_id: "x/v1",
        trigger_event: "PostToolUse", tool_matcher: "Bash" },
    ] }), { status: 200 }) as any)
    const r = await cloud.listConditions()
    expect(r.map(c => c.kind)).toEqual(["sentinel_re", "tool_match"])
    expect(r[0].policy_id).toBe("x/v1")
  })

  it("listVerifiers propagates 5xx as cloud N", async () => {
    process.env.MAGI_CP_API_KEY = "tenant-test"
    global.fetch = vi.fn(async () => new Response("", { status: 503 }) as any)
    await expect(cloud.listVerifiers()).rejects.toThrow("cloud 503")
  })

  // listPresets is a back-compat alias for listVerifiers. exercises
  // the same path. Kept as a one-shot smoke check so refactors that
  // accidentally break the alias surface here, not in pages.
  it("listPresets is an alias of listVerifiers", async () => {
    process.env.MAGI_CP_API_KEY = "tenant-test"
    global.fetch = vi.fn(async () => new Response(
      JSON.stringify({ presets: [
        { id: "x", category: "ANSWER", description: "", enforcement: "preview", step: null },
      ] }), { status: 200 }) as any)
    const r = await cloud.listPresets()
    expect(r).toHaveLength(1)
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

  // ── v2.2: tenant provisioning (signup queue retired) ─────────────────
  it("createTenant signs body with HMAC and sends x-magi-signature", async () => {
    process.env.MAGI_CP_ADMIN_HMAC_SECRET = "shared-secret-xxxx"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url: String(url), init }
      return new Response(JSON.stringify({
        id: "acme-co-abcd", status: "active", plan: "alpha", expires_at: null,
      }), { status: 200 }) as any
    })
    const out = await cloud.createTenant("acme-co-abcd", "alpha")
    expect(captured.url).toBe("http://test/admin/tenants")
    expect(captured.init.headers["x-magi-signature"]).toBeTruthy()
    const body = JSON.parse(captured.init.body)
    expect(body.tenant_id).toBe("acme-co-abcd")
    expect(body.plan).toBe("alpha")
    expect(body.expires_at).toBeNull()
    const crypto = await import("node:crypto")
    const expected = crypto.createHmac("sha256", "shared-secret-xxxx")
      .update(captured.init.body).digest("hex")
    expect(captured.init.headers["x-magi-signature"]).toBe(expected)
    expect(out.id).toBe("acme-co-abcd")
  })

  it("issueKey HMACs an empty body and returns cleartext key", async () => {
    process.env.MAGI_CP_ADMIN_HMAC_SECRET = "shared-secret"
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url: String(url), init }
      return new Response(JSON.stringify({
        id: 7, tenant_id: "t-1", api_key: "mcp_secret-once", prefix: "mcp_secre",
      }), { status: 200 }) as any
    })
    const out = await cloud.issueKey("t-1")
    expect(captured.url).toBe("http://test/admin/tenants/t-1/keys")
    expect(captured.init.body).toBe("{}")
    expect(out.api_key).toBe("mcp_secret-once")
  })

  // ── PR4: verifyDispatch sends canonical fields ─────────────────
  it("verifyDispatch sends subject/payload_hash (no legacy mirror)", async () => {
    let captured: any
    global.fetch = vi.fn(async (url: any, init: any) => {
      captured = { url: String(url), init }
      return new Response(JSON.stringify({
        verdict: "pass", token: "tok", reasons: [],
      }), { status: 200 }) as any
    })
    await cloud.verifyDispatch("privilege_scan",
                               { text: "x" }, "MY_SUBJ", "MY_HASH")
    const body = JSON.parse(captured.init.body)
    expect(body.subject).toBe("MY_SUBJ")
    expect(body.payload_hash).toBe("MY_HASH")
    // PR4: legacy alias keys MUST NOT be present on the wire — the
    // cloud's `extra="forbid"` validator would 422 them.
    expect(body.matter).toBeUndefined()
    expect(body.doc_id).toBeUndefined()
  })

  it("provisionTenant chains createTenant + issueKey", async () => {
    process.env.MAGI_CP_ADMIN_HMAC_SECRET = "shared-secret"
    const responses = [
      new Response(JSON.stringify({ id: "t-1", status: "active", plan: "alpha", expires_at: null }), { status: 200 }),
      new Response(JSON.stringify({ id: 9, tenant_id: "t-1", api_key: "mcp_abc", prefix: "mcp_abc" }), { status: 200 }),
    ]
    const urls: string[] = []
    global.fetch = vi.fn(async (url: any) => {
      urls.push(String(url))
      return responses.shift() as any
    })
    const out = await cloud.provisionTenant("t-1", "alpha")
    expect(urls).toEqual([
      "http://test/admin/tenants",
      "http://test/admin/tenants/t-1/keys",
    ])
    expect(out.apiKey).toBe("mcp_abc")
    expect(out.tenantId).toBe("t-1")
  })

})
