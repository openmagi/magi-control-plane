/**
 * Policy id validation — single source of truth for the browser↔cloud edge.
 *
 * Mirrors the cloud-side accepted shape (alphanumeric, dot/underscore/dash,
 * forward slashes for namespacing like "legal-filing/v1"). Rejects path
 * traversal ("..") and reserved suffixes that collide with sibling routes
 * (/compiled, /enabled).
 */
const POLICY_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$/
const RESERVED_SUFFIXES = ["/compiled", "/enabled"]

export function validatePolicyId(s: unknown): string {
  if (typeof s !== "string" || !s) throw new Error("invalid_id")
  if (!POLICY_ID_RE.test(s)) throw new Error("invalid_id")
  if (s.includes("..")) throw new Error("invalid_id")
  if (RESERVED_SUFFIXES.some(suf => s.endsWith(suf))) throw new Error("invalid_id")
  return s
}

/** Encode each path segment defensively before URL composition. */
export function encodePolicyIdForUrl(s: string): string {
  return s.split("/").map(encodeURIComponent).join("/")
}
