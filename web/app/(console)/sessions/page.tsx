import { revalidatePath } from "next/cache"

import { cloud, type AdminSessionEntry } from "@/lib/cloud"
import { fmtUtc } from "@/lib/format"
import { getT } from "@/lib/i18n/server"
import { isPackCentricEnabled } from "@/lib/pack-centric"
import { runtimeNameKey } from "@/lib/runtime-name"
import {
  Badge, Button, Card, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

/**
 * P4 (pack-centric runtime): the /sessions tab.
 *
 * A table of the tenant's recent Claude Code sessions and the packs each
 * one currently has activated. This answers the compliance question the
 * plan doc calls out: "did anyone leave the strict block pack turned off
 * yesterday?" — the inverse view of the per-session activation the gate
 * reads at every hook resolution.
 *
 * Row action "force deactivate all" loops the session's `active_packs`
 * and calls `cloud.deactivateSessionPack` once per pack (the cloud
 * `POST /session/{id}/packs/deactivate` route). The floor pack is never
 * in `active_packs` — it is always-on and server-locked — so it is left
 * untouched and rendered as an "ALWAYS-ON" chip instead.
 *
 * Data source: GET /admin/sessions (new alongside P4).
 */

/** Server action: deactivate every pack a session has active. */
async function forceDeactivateAll(formData: FormData) {
  "use server"
  const sessionId = formData.get("sessionId")
  const packsRaw = formData.get("activePacks")
  if (typeof sessionId !== "string" || !sessionId) return
  const packIds =
    typeof packsRaw === "string" && packsRaw
      ? packsRaw.split(",").filter(Boolean)
      : []
  // One deactivate per active pack. The floor pack is not in this list
  // (always-on), so we never hit the cloud's 400 floor-lock branch.
  for (const packId of packIds) {
    try {
      await cloud.deactivateSessionPack(sessionId, packId)
    } catch {
      // Surfaced on refresh (the row keeps its packs); avoid leaking
      // cloud detail into the browser.
    }
  }
  revalidatePath("/sessions")
}

/** Truncate a CC session uuid for the table's first column. */
function truncSession(id: string): string {
  if (id.length <= 16) return id
  return `${id.slice(0, 8)}…${id.slice(-4)}`
}

type TFunc = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

export default async function SessionsPage() {
  const { t } = await getT()

  // P4 legacy-guard: pack-centric gate resolution is itself flag-gated
  // (the runtime only reads session_active_packs when
  // MAGI_CP_PACK_CENTRIC_RUNTIME is on). With the flag OFF this whole
  // governance model is not in effect, so render an explicit "not yet
  // enabled" state instead of a table that promises a session-scoped
  // runtime doing nothing. Keeps the legacy per-policy path honest.
  if (!isPackCentricEnabled()) {
    return (
      <div className="space-y-4">
        <PageHeader
          title={t("sessions.title")}
          description={t("sessions.description")}
        />
        <EmptyState
          title={t("sessions.disabled.title")}
          body={t("sessions.disabled.body")}
        />
      </div>
    )
  }

  let data: {
    items: AdminSessionEntry[]
    floor_pack_id: string | null
  }
  try {
    data = await cloud.listAdminSessions()
  } catch {
    return (
      <ErrorState
        title={t("sessions.title")}
        body={t("common.cloudUnreachable")}
      />
    )
  }

  const { items, floor_pack_id: floorPackId } = data

  return (
    <div className="space-y-4">
      <PageHeader
        title={t("sessions.title")}
        description={t("sessions.description")}
      />

      {floorPackId && (
        <Card className="text-sm">
          {t("sessions.floorNote")}{" "}
          <Code>{floorPackId}</Code>{" "}
          <Badge variant="ok">{t("packs.alwaysOn")}</Badge>
        </Card>
      )}

      {items.length === 0 ? (
        <EmptyState
          title={t("sessions.empty.title")}
          body={t("sessions.empty.body")}
        />
      ) : (
        <Card noPadding>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="text-left p-3">{t("sessions.col.session")}</th>
                  <th className="text-left p-3">{t("sessions.col.runtime")}</th>
                  <th className="text-left p-3">{t("sessions.col.activePacks")}</th>
                  <th className="text-left p-3">{t("sessions.col.lastActivity")}</th>
                  <th className="text-left p-3">{t("sessions.col.floorPack")}</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <SessionRow
                    key={item.session_id}
                    item={item}
                    t={t}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

function SessionRow({
  item, t,
}: {
  item: AdminSessionEntry
  t: TFunc
}) {
  const hasActive = item.active_packs.length > 0
  return (
    <tr className="border-t border-[var(--color-border)] align-top">
      <td className="p-3">
        <Code>{truncSession(item.session_id)}</Code>
      </td>
      <td className="p-3">
        <Badge variant="muted">
          {t(runtimeNameKey(item.runtime_id))}
        </Badge>
      </td>
      <td className="p-3">
        {hasActive ? (
          <div className="flex flex-wrap gap-1.5">
            {item.active_packs.map((packId) => (
              <Badge key={packId} variant="info">{packId}</Badge>
            ))}
          </div>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">
            {t("sessions.noActivePacks")}
          </span>
        )}
      </td>
      <td className="p-3 text-[var(--color-text-tertiary)]">
        {fmtUtc(item.last_seen_at)}
      </td>
      <td className="p-3">
        {item.floor_pack_id ? (
          <div className="flex items-center gap-1.5">
            <Code>{item.floor_pack_id}</Code>
            <Badge variant="ok">{t("packs.alwaysOn")}</Badge>
          </div>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">
            {t("sessions.noFloorPack")}
          </span>
        )}
      </td>
      <td className="p-3 text-right">
        {hasActive ? (
          <form action={forceDeactivateAll}>
            <input type="hidden" name="sessionId" value={item.session_id} />
            <input
              type="hidden"
              name="activePacks"
              value={item.active_packs.join(",")}
            />
            <Button
              type="submit"
              variant="ghost"
              size="sm"
              aria-label={t("sessions.forceDeactivateFor", {
                id: truncSession(item.session_id),
              })}
            >
              {t("sessions.forceDeactivate")}
            </Button>
          </form>
        ) : null}
      </td>
    </tr>
  )
}
