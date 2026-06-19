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
