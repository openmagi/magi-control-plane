import { readFileSync } from "node:fs"
import path from "node:path"

/** Serves scripts/smoke-test.sh at /install/smoke-test.sh so users
 * can re-verify after the initial install with one curl. */
export async function GET() {
  let body: string
  try {
    body = readFileSync(
      path.join(process.cwd(), "..", "scripts", "smoke-test.sh"),
      "utf-8",
    )
  } catch (e) {
    return new Response(
      `#!/usr/bin/env bash\necho "smoke-test.sh missing (${(e as Error).message})" >&2; exit 1\n`,
      { status: 500, headers: { "content-type": "text/x-shellscript; charset=utf-8" } },
    )
  }
  return new Response(body, {
    headers: {
      "content-type": "text/x-shellscript; charset=utf-8",
      "cache-control": "no-store",
    },
  })
}
