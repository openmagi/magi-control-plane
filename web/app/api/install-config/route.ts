import { NextRequest } from "next/server"

/**
 * Tells the install.sh quickstart where the dashboard vs the runtime
 * cloud backend live. In the production split:
 *
 *   - dashUrl: cloud.openmagi.ai  (Next.js on Vercel. this server)
 *   - apiUrl:  api.openmagi.ai    (Python FastAPI on K8s. gate calls here)
 *
 * In dev (same host serves both), `apiUrl` falls back to the request
 * origin so a single-binary deploy still works.
 */
export async function GET(req: NextRequest) {
  const origin = new URL(req.url).origin
  const dashUrl =
    process.env.MAGI_CP_PUBLIC_SITE_URL || origin
  const apiUrl =
    process.env.MAGI_CP_PUBLIC_CLOUD_URL ||
    process.env.MAGI_CP_CLOUD_URL ||
    origin
  return Response.json({
    dashUrl,
    apiUrl,
    // Stamp so install.sh can detect a version skew between the script
    // it has cached and the dashboard it's downloading from.
    schema: 1,
  }, { headers: { "cache-control": "no-store" } })
}
