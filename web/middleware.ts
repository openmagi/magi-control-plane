import { NextResponse, type NextRequest } from "next/server"

/**
 * Marketing-only deploy gate.
 *
 * The same Next.js codebase ships both the marketing surface (welcome,
 * install guide, install.sh, compose.yml) AND the dashboard console
 * (rules, policies, hitl, ledger, presets, setup). In the official
 * docker image they run together at the user's localhost.
 *
 * The Vercel deploy at cp.openmagi.ai is marketing-ONLY though — there
 * is no hosted control plane, so it would be misleading to let visitors
 * walk into /policies/new on cp.openmagi.ai and start authoring against
 * "nothing." Set MAGI_CP_MARKETING_ONLY=1 in that environment and the
 * middleware below redirects every non-marketing route to /install.
 *
 * Locally and in the user's self-hosted dashboard the env is unset, so
 * this middleware is a no-op and the console renders normally.
 */
const MARKETING_ONLY = process.env.MAGI_CP_MARKETING_ONLY === "1"

// Path prefixes that always stay public on marketing-only deploys.
const MARKETING_PUBLIC: readonly string[] = [
  "/welcome",
  "/install",          // /install + /install.sh
  "/docs",             // /docs + /docs/<slug>
  "/r",                // /r/<token> public run-share links
  "/legal",
  "/self-host",        // /self-host/docker-compose.yml
  "/api/install-config",
  "/api/downloads",
  "/robots.txt",
  "/sitemap.xml",
]

export function middleware(req: NextRequest): NextResponse {
  if (!MARKETING_ONLY) return NextResponse.next()
  const path = req.nextUrl.pathname
  if (path === "/") {
    const url = req.nextUrl.clone()
    url.pathname = "/welcome"
    return NextResponse.redirect(url)
  }
  if (MARKETING_PUBLIC.some((p) => path === p || path.startsWith(p + "/") || path === p + ".sh")) {
    return NextResponse.next()
  }
  // Console routes on a marketing-only deploy: redirect to install.
  // Visitors who land here typed the URL directly or followed an old
  // link; the install page is the right next step.
  const url = req.nextUrl.clone()
  url.pathname = "/install"
  url.searchParams.set("from", path)
  return NextResponse.redirect(url)
}

export const config = {
  // Skip framework internals + the static assets dir; everything else
  // routes through the middleware.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.png$|.*\\.svg$).*)"],
}
