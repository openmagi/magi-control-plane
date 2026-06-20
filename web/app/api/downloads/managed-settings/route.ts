import { NextRequest } from "next/server"

/** managed-settings.json — uses the dashboard's cloud URL so the user's
 * locally-installed Claude Code knows where to call. The token isn't
 * embedded; the gate reads MAGI_CP_API_KEY at runtime, per /setup step 1. */
export async function GET(req: NextRequest) {
  const cloudUrl =
    process.env.MAGI_CP_PUBLIC_CLOUD_URL ||
    process.env.MAGI_CP_CLOUD_URL ||
    "https://cloud.openmagi.ai"

  const settings = {
    "_magi_policies": [],   // populated by user via `/policies/...`
    "allowManagedHooksOnly": true,
    "permissions": { "defaultMode": "default" },
    "hooks": {
      "PreToolUse": [
        {
          "matcher": "Bash",
          "hooks": [
            {
              "type": "command",
              "command": "/usr/local/bin/magi-gate.sh",
              "env": { "MAGI_CP_CLOUD_URL": cloudUrl },
            },
          ],
        },
      ],
    },
  }

  return new Response(JSON.stringify(settings, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": 'attachment; filename="managed-settings.json"',
      "cache-control": "no-store",
    },
  })
}
