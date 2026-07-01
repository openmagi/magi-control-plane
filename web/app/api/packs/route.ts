import { NextRequest } from "next/server"
import { cloud, CloudConfigError } from "@/lib/cloud"

/**
 * P4 (pack-centric runtime): same-origin proxy for the PackMultiSelect
 * combobox used on every policy authoring surface.
 *
 * The cloud's `GET /policy-packs` is admin-key gated, which we never
 * ship to the browser. This route forwards the locale, calls the cloud
 * server-side (cloud.ts reads the admin key from env), and returns the
 * pack list so the client picker can render its options (floor pack
 * first, ALWAYS-ON, plus every user + built-in pack).
 *
 * Read-only. Admin cloud error text is never echoed back (logged to
 * server stderr by cloud.ts); the client degrades to an empty option
 * list on failure so a picker outage never blocks authoring.
 */
export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const localeParam = req.nextUrl.searchParams.get("locale")
  const locale = localeParam === "en" ? "en" : localeParam === "ko" ? "ko" : undefined
  try {
    const items = await cloud.listPacks(locale)
    return Response.json({ items }, { headers: { "cache-control": "no-store" } })
  } catch (e) {
    if (e instanceof CloudConfigError) {
      return Response.json(
        { error: "server config", items: [] },
        { status: 503, headers: { "cache-control": "no-store" } },
      )
    }
    return Response.json(
      { error: "upstream", items: [] },
      { status: 502, headers: { "cache-control": "no-store" } },
    )
  }
}
