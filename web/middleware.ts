import { NextResponse, type NextRequest } from "next/server"
import {
  isLoopbackHost,
  verifySession,
  trustLoopbackHeader,
  CONSOLE_COOKIE,
} from "@/lib/dashboard-auth"

/**
 * Two responsibilities, both keyed off the request path:
 *
 * 1. Marketing-only deploy gate (MAGI_CP_MARKETING_ONLY=1, e.g. the public
 *    cp.openmagi.ai Vercel deploy): there is no hosted control plane, so
 *    redirect every non-marketing route to /install.
 *
 * 2. Self-host console backstop (WEB-1): when NOT marketing-only, the console
 *    holds ambient admin credentials. Loopback requests are trusted (single
 *    operator localhost default); any non-loopback console request must carry
 *    a signed session cookie (see lib/dashboard-auth). Fails closed when no
 *    signing secret is set. Behind a reverse proxy set
 *    MAGI_CP_TRUST_LOOPBACK_HEADER=0 to require a session for every request.
 */
const MARKETING_ONLY = process.env.MAGI_CP_MARKETING_ONLY === "1"

// Prefixes that stay public in BOTH modes.
const MARKETING_PUBLIC: readonly string[] = [
  "/welcome",
  "/install",
  "/docs",
  "/r",
  "/legal",
  "/self-host",
  "/api/install-config",
  "/api/downloads",
  "/robots.txt",
  "/sitemap.xml",
]

// Console-guard public set = marketing-public + the login page itself.
const CONSOLE_PUBLIC: readonly string[] = [...MARKETING_PUBLIC, "/login"]

function matchesPrefix(path: string, prefixes: readonly string[]): boolean {
  return prefixes.some(
    (p) => path === p || path.startsWith(p + "/") || path === p + ".sh",
  )
}

export async function middleware(req: NextRequest): Promise<NextResponse> {
  const path = req.nextUrl.pathname

  // (1) Marketing-only deploy.
  if (MARKETING_ONLY) {
    if (path === "/") {
      const url = req.nextUrl.clone()
      url.pathname = "/welcome"
      return NextResponse.redirect(url)
    }
    if (matchesPrefix(path, MARKETING_PUBLIC)) return NextResponse.next()
    const url = req.nextUrl.clone()
    url.pathname = "/install"
    url.searchParams.set("from", path)
    return NextResponse.redirect(url)
  }

  // (2) Self-host console backstop.
  if (path === "/") return NextResponse.next()
  if (matchesPrefix(path, CONSOLE_PUBLIC)) return NextResponse.next()

  // A loopback Host request skips the login (self-host single-operator
  // default). The security boundary is the network bind, NOT this header:
  // the docker-compose template binds the dashboard to 127.0.0.1 only, so it
  // is unreachable off-host and the Host header cannot be spoofed from
  // outside. An operator who deliberately exposes the console (binds 0.0.0.0
  // or fronts it with a proxy) sets MAGI_CP_TRUST_LOOPBACK_HEADER=0 to force a
  // session. (We do NOT key off x-forwarded-* here: the Next.js standalone
  // server injects those on every request even with no proxy, so their mere
  // presence is not a proxy signal.)
  if (trustLoopbackHeader() && isLoopbackHost(req.headers.get("host"))) {
    return NextResponse.next()
  }
  if (await verifySession(req.cookies.get(CONSOLE_COOKIE)?.value)) {
    return NextResponse.next()
  }
  const url = req.nextUrl.clone()
  url.pathname = "/login"
  url.searchParams.set("from", path)
  return NextResponse.redirect(url)
}

export const config = {
  // Skip framework internals + static assets; everything else routes through.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.png$|.*\\.svg$).*)"],
}
