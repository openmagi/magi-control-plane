import { readFileSync } from "node:fs"
import path from "node:path"
import { NextRequest } from "next/server"

/**
 * Serves the quickstart installer at /install.sh.
 *
 * The file is read at request time (not bundled) so a hotfix to scripts/
 * doesn't require a web rebuild. The script body itself fetches the
 * cloud's managed-settings + gate shim from this same hostname, so the
 * one-liner is hostname-locked to wherever you reach it from.
 */
export async function GET(_req: NextRequest) {
  let body: string
  try {
    body = readFileSync(
      path.join(process.cwd(), "..", "scripts", "quickstart.sh"),
      "utf-8",
    )
  } catch (e) {
    return new Response(`#!/usr/bin/env bash\necho "install.sh missing (${(e as Error).message})" >&2; exit 1\n`, {
      status: 500,
      headers: { "content-type": "text/x-shellscript; charset=utf-8" },
    })
  }
  return new Response(body, {
    headers: {
      "content-type": "text/x-shellscript; charset=utf-8",
      "cache-control": "no-store",
    },
  })
}
