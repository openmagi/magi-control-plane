import { unstable_cache } from "next/cache"
import { cloud, CloudConfigError } from "@/lib/cloud"

export interface WorkspaceData {
  tenant: {
    id: string
    plan: string
    status: string
    synthetic: boolean
    expires_at: number | null
  } | null
  healthOk: boolean
  hitlPending: number
}

/**
 * Cache tag fired when mutating server actions need to invalidate the
 * sidebar (HITL approve/reject, policy enable/disable, tenant changes).
 *
 * Use via `revalidateTag(WORKSPACE_TAG)` from any server action.
 */
export const WORKSPACE_TAG = "workspace"

async function _loadWorkspaceUncached(): Promise<WorkspaceData> {
  const apiKey = process.env.MAGI_CP_API_KEY
  const cloudUrl =
    process.env.MAGI_CP_PUBLIC_CLOUD_URL ??
    process.env.MAGI_CP_CLOUD_URL ??
    "http://127.0.0.1:8787"

  const [tenant, healthOk, hitlPending] = await Promise.all([
    apiKey
      ? cloud.getMyTenant(apiKey).catch(() => null)
      : Promise.resolve(null),
    fetch(`${cloudUrl}/healthz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2000),
    })
      .then(r => r.ok)
      .catch(() => false),
    cloud.listHitl().then(l => l.length).catch((e: unknown) => {
      if (e instanceof CloudConfigError) return 0
      return 0
    }),
  ])

  return { tenant, healthOk, hitlPending }
}

/**
 * Cached workspace data fetch for the sidebar.
 *
 * Cache window: 30 seconds. Mutating server actions (HITL approve/
 * reject, policy enable/disable) call `revalidateTag(WORKSPACE_TAG)`
 * to invalidate sooner.
 *
 * The cache key is intentionally a single static array. every page
 * server-rendered under the (console) shell shares the same workspace
 * snapshot. There is no per-page variation.
 */
export const getWorkspaceData = unstable_cache(
  _loadWorkspaceUncached,
  ["workspace-sidebar-v1"],
  { revalidate: 30, tags: [WORKSPACE_TAG] },
)
