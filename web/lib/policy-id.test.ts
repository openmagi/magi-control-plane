import { describe, it, expect } from "vitest"
import { validatePolicyId, encodePolicyIdForUrl } from "./policy-id"

describe("validatePolicyId", () => {
  it("accepts canonical ids", () => {
    expect(validatePolicyId("legal-filing/v1")).toBe("legal-filing/v1")
    expect(validatePolicyId("a")).toBe("a")
    expect(validatePolicyId("a.b_c-d/2.0")).toBe("a.b_c-d/2.0")
  })

  it("rejects empty / wrong type", () => {
    expect(() => validatePolicyId("")).toThrow("invalid_id")
    expect(() => validatePolicyId(undefined)).toThrow("invalid_id")
    expect(() => validatePolicyId(123)).toThrow("invalid_id")
  })

  it("rejects path traversal", () => {
    expect(() => validatePolicyId("../hitl")).toThrow("invalid_id")
    expect(() => validatePolicyId("a/../b")).toThrow("invalid_id")
  })

  it("rejects reserved suffix collisions", () => {
    expect(() => validatePolicyId("foo/compiled")).toThrow("invalid_id")
    expect(() => validatePolicyId("foo/enabled")).toThrow("invalid_id")
  })

  it("rejects unsafe characters", () => {
    expect(() => validatePolicyId("a b")).toThrow("invalid_id")
    expect(() => validatePolicyId("a?b")).toThrow("invalid_id")
    expect(() => validatePolicyId("a#b")).toThrow("invalid_id")
  })

  it("rejects oversized id", () => {
    expect(() => validatePolicyId("x".repeat(129))).toThrow("invalid_id")
  })
})

describe("encodePolicyIdForUrl", () => {
  it("preserves segment slashes", () => {
    expect(encodePolicyIdForUrl("legal-filing/v1")).toBe("legal-filing/v1")
  })

  it("encodes per-segment safely", () => {
    expect(encodePolicyIdForUrl("a b/c+d")).toBe("a%20b/c%2Bd")
  })
})
