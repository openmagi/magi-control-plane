import { revalidatePath } from "next/cache"

import { cloud, type SharedRunItem } from "@/lib/cloud"
import { fmtUtc } from "@/lib/format"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Button, Card, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

async function revoke(formData: FormData) {
  "use server"
  const tokenHash = formData.get("tokenHash")
  if (typeof tokenHash === "string" && tokenHash) {
    try {
      await cloud.revokeSharedRun(tokenHash)
    } catch {
      // Surfaced on refresh (the row stays active); avoid leaking detail.
    }
  }
  revalidatePath("/shared")
}

function stateBadge(item: SharedRunItem) {
  if (item.revokedAt) return <Badge variant="deny">revoked</Badge>
  if (!item.active) return <Badge variant="review">expired</Badge>
  return <Badge variant="default">active</Badge>
}

export default async function SharedRunsPage() {
  const { t } = await getT()

  let items: SharedRunItem[]
  try {
    items = await cloud.listSharedRuns()
  } catch {
    return (
      <ErrorState
        title={t("nav.shared")}
        body="Could not reach the cloud. Check MAGI_CP_CLOUD_URL / MAGI_CP_API_KEY."
      />
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader title={t("nav.shared")} description={t("shared.description")} />

      <Card className="text-sm">
        {t("shared.howto")} <Code>magi-cp share &lt;sessionId&gt;</Code>
      </Card>

      {items.length === 0 ? (
        <EmptyState title={t("shared.empty")} />
      ) : (
        <Card noPadding>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="text-left p-3">run</th>
                  <th className="text-left p-3">created</th>
                  <th className="text-left p-3">state</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.tokenHash} className="border-t border-[var(--color-border)]">
                    <td className="p-3">{item.title || "(untitled run)"}</td>
                    <td className="p-3 text-[var(--color-text-tertiary)]">
                      {fmtUtc(item.createdAt)}
                    </td>
                    <td className="p-3">{stateBadge(item)}</td>
                    <td className="p-3 text-right">
                      {item.active ? (
                        <form action={revoke}>
                          <input type="hidden" name="tokenHash" value={item.tokenHash} />
                          <Button type="submit" variant="ghost" size="sm">
                            {t("shared.revoke")}
                          </Button>
                        </form>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
