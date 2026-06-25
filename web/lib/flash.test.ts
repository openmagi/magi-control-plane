import { describe, it, expect } from "vitest"
import { resolveFlash, codeForError } from "./flash"
import { CloudConfigError } from "./cloud"

describe("resolveFlash", () => {
  it("returns null for missing input", () => {
    expect(resolveFlash(undefined, undefined)).toBeNull()
  })

  it("returns null for unknown codes (no echo)", () => {
    expect(resolveFlash("evil_phish_string", undefined)).toBeNull()
    expect(resolveFlash(undefined, "evil_phish_string")).toBeNull()
  })

  it("renders known ok code", () => {
    expect(resolveFlash("toggled", undefined)).toEqual({
      kind: "ok", text: "Policy updated.",
    })
  })

  it("renders known error code without leaking env name", () => {
    const r = resolveFlash(undefined, "config_error")!
    expect(r.kind).toBe("error")
    expect(r.text).not.toContain("MAGI_CP")
  })

  it("renders pack_created ok flash", () => {
    // D75 follow-up: createPackAction redirects with msg=pack_created.
    // Before this code was added resolveFlash returned null and the
    // visitor landed on /rules with zero confirmation.
    const r = resolveFlash("pack_created", undefined)!
    expect(r.kind).toBe("ok")
    expect(r.text).toMatch(/pack/i)
  })

  it("renders name_required error flash", () => {
    // D75 follow-up: createPackAction redirects with err=name_required
    // on empty form submits; the new-pack page only renders a banner
    // when resolveFlash returns non-null.
    const r = resolveFlash(undefined, "name_required")!
    expect(r.kind).toBe("error")
    expect(r.text).toMatch(/name/i)
  })

  it("renders the pack cascade partial-success + partial-failure codes", () => {
    const ok = resolveFlash("pack_partial_success", undefined)!
    expect(ok.kind).toBe("ok")
    const err = resolveFlash(undefined, "pack_partial_failure")!
    expect(err.kind).toBe("error")
  })
})

describe("codeForError", () => {
  it("maps CloudConfigError to config_error code", () => {
    expect(codeForError(new CloudConfigError())).toBe("config_error")
  })

  it("maps 404 to not_found", () => {
    expect(codeForError(new Error("cloud 404"))).toBe("not_found")
  })

  it("maps 401/403 to forbidden", () => {
    expect(codeForError(new Error("cloud 401"))).toBe("forbidden")
    expect(codeForError(new Error("cloud 403"))).toBe("forbidden")
  })

  it("maps 409 to conflict", () => {
    expect(codeForError(new Error("cloud 409"))).toBe("conflict")
  })

  it("maps other 4xx to invalid_input", () => {
    expect(codeForError(new Error("cloud 422"))).toBe("invalid_input")
  })

  it("defaults to cloud_unreachable", () => {
    expect(codeForError(new Error("network error"))).toBe("cloud_unreachable")
    expect(codeForError("anything")).toBe("cloud_unreachable")
  })
})
