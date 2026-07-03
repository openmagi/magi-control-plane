/**
 * Self-host console auth backstop (WEB-1).
 *
 * The dashboard holds ambient server-side credentials (MAGI_CP_API_KEY,
 * MAGI_CP_ADMIN_API_KEY, MAGI_CP_ADMIN_HMAC_SECRET) that the BFF injects into
 * backend calls. The intended model is "console = operator localhost only".
 * If an operator exposes the dashboard to a network, there was no app-layer
 * backstop, so anyone reachable became a full unauthenticated admin.
 *
 * This module adds a signed session so a NON-loopback console request must
 * carry a valid cookie. Loopback stays frictionless (single-operator default).
 * Fails closed: with no signing secret configured, verifySession() is always
 * false, so an exposed dashboard denies rather than admits.
 *
 * HMAC uses the Web Crypto API (globalThis.crypto.subtle) so the SAME code
 * verifies in the Edge middleware runtime and signs in the Node server-action
 * runtime.
 */
const COOKIE = "magi-cp-console-session"
const MAX_AGE_S = 60 * 60 * 12

function secret(): string | null {
  return (
    process.env.MAGI_CP_DASHBOARD_SESSION_SECRET ||
    process.env.MAGI_CP_ADMIN_HMAC_SECRET ||
    null
  )
}

/**
 * Whether to trust a loopback `host` header as proof of a local request.
 *
 * Default TRUE: magi-cp is a self-host, single-operator tool, so a
 * `localhost` request is the operator's own machine and forcing a login
 * there is pure friction. The real security boundary is the NETWORK BIND,
 * not this header: the docker-compose template binds the dashboard to
 * 127.0.0.1 only, so it is unreachable off-host and the Host header cannot be
 * spoofed from another machine. An operator who deliberately exposes the
 * console (binds 0.0.0.0, or fronts it with a reverse proxy) sets
 * MAGI_CP_TRUST_LOOPBACK_HEADER=0 to require a signed session for every
 * request. (We intentionally do NOT try to detect a proxy from x-forwarded-*
 * headers: the Next.js standalone server injects those on every request even
 * with no proxy, so their presence is not a proxy signal.)
 */
export function trustLoopbackHeader(): boolean {
  return process.env.MAGI_CP_TRUST_LOOPBACK_HEADER !== "0"
}

export function isLoopbackHost(host: string | null): boolean {
  if (!host) return false
  // Bracketed IPv6 (e.g. "[::1]:3000") vs host:port.
  const h = (
    host.startsWith("[") ? host.slice(1, host.indexOf("]")) : host.split(":")[0]
  ).toLowerCase()
  return h === "localhost" || h === "127.0.0.1" || h === "::1"
}

async function hmacHex(key: string, msg: string): Promise<string> {
  const enc = new TextEncoder()
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  )
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(msg))
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return diff === 0
}

/** Sign `subject.exp.sig`. Returns null when no signing secret is configured. */
export async function signSession(subject: string): Promise<string | null> {
  const s = secret()
  if (!s) return null
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE_S
  const body = `${subject}.${exp}`
  const sig = await hmacHex(s, body)
  return `${body}.${sig}`
}

/** Verify a session token. False on missing secret, malformed, expired, or bad sig. */
export async function verifySession(token: string | undefined): Promise<boolean> {
  const s = secret()
  if (!s || !token) return false
  const parts = token.split(".")
  if (parts.length !== 3) return false
  const [subject, expStr, sig] = parts
  const exp = Number(expStr)
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return false
  const expected = await hmacHex(s, `${subject}.${expStr}`)
  return timingSafeEqualHex(sig, expected)
}

export const CONSOLE_COOKIE = COOKIE
export const CONSOLE_MAX_AGE_S = MAX_AGE_S
