import type { NextRequest } from "next/server"

/**
 * CSRF guard for state-changing BFF routes (WEB-2).
 *
 * A `multipart/form-data` POST is a CORS "simple request", so a cross-origin
 * page can submit it with no preflight. Combined with the console's ambient
 * server-side credentials, a drive-by page the operator visits could POST to
 * e.g. /api/scripts and persist a script. We assert the request is same-origin
 * before honoring a mutation.
 *
 * Preference order:
 *   1. Sec-Fetch-Site (sent by all modern browsers): allow same-origin /
 *      same-site, and `none` (a top-level navigation the user typed / bookmarked).
 *      Reject `cross-site`.
 *   2. Fallback to comparing the Origin header host to the Host header.
 *   3. No Origin at all (server-to-server, curl, tests): allow.
 */
export function isSameOrigin(req: NextRequest): boolean {
  const site = req.headers.get("sec-fetch-site")
  if (site) {
    return site === "same-origin" || site === "same-site" || site === "none"
  }
  const origin = req.headers.get("origin")
  if (!origin) return true // non-CORS caller (server / curl)
  try {
    return new URL(origin).host === req.headers.get("host")
  } catch {
    return false
  }
}
