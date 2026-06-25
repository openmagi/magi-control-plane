import Link from "next/link"
import { cloud, type PolicyPackDetail } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, Code, ErrorState, PageHeader,
} from "@/components/ui"
import { PolicyTestPanel } from "../../policies/_components/PolicyTestPanel"

export const dynamic = "force-dynamic"

/**
 * D77: pack detail page with the multi-policy test simulator. Lists
 * the pack's members + their enabled state, then mounts the
 * PolicyTestPanel in `kind="pack"` mode so the operator can throw a
 * single synthetic payload at every member and compare results.
 *
 * The page reuses the cloud GET /policy-packs/{id} envelope (built-in
 * + user packs supported) so users authoring a pack can preview its
 * coverage before flipping it on.
 */
export default async function PolicyPackDetailPage({
  params,
}: { params: { id: string } }) {
  const { t, locale } = await getT()
  // The cloud accepts the legacy `pack/<slug>` and `user-pack/<slug>`
  // forms verbatim. Next.js dynamic route splits the id at the first
  // slash so we re-join it for the cloud lookup.
  const rawId = decodeURIComponent(params.id)
  // If the operator hits /policy-packs/foo without the `pack/` or
  // `user-pack/` prefix, we accept the user-pack/ shape by default
  // (the cloud will 404 cleanly on a bad guess).
  const packId = rawId.includes("/") ? rawId : `user-pack/${rawId}`

  let detail: PolicyPackDetail | null = null
  let errCode: string | null = null
  try {
    detail = await cloud.getPack(packId, locale)
  } catch (e: unknown) {
    errCode = codeForError(e)
  }

  if (errCode === "not_found" || detail == null) {
    return (
      <>
        <p className="mb-3">
          <Link href="/rules" className="text-sm">{t("newPolicy.back")}</Link>
        </p>
        <ErrorState
          status={t("policies.notFound")}
          title={t("policies.notFound")}
          body={<Code>{packId}</Code>}
        />
      </>
    )
  }
  if (errCode) {
    return (
      <>
        <p className="mb-3">
          <Link href="/rules" className="text-sm">{t("newPolicy.back")}</Link>
        </p>
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      </>
    )
  }

  return (
    <>
      <p className="mb-3">
        <Link href="/rules" className="text-sm">{t("newPolicy.back")}</Link>
      </p>
      <PageHeader title={<Code className="text-md">{detail.name}</Code>} />

      <Card className="mb-6">
        <div className="flex flex-wrap gap-x-6 gap-y-3 items-center">
          <span className="text-sm">
            id: <Code>{detail.id}</Code>
          </span>
          <span className="text-sm">
            source: <Code>{detail.source}</Code>
          </span>
          <span className="text-sm flex items-center gap-2">
            status:
            {detail.status === "all" && (
              <Badge variant="ok">all enabled</Badge>
            )}
            {detail.status === "partial" && (
              <Badge variant="review">partial</Badge>
            )}
            {detail.status === "none" && (
              <Badge variant="deny">none enabled</Badge>
            )}
          </span>
          <span className="text-sm">
            members: <Code>{detail.member_count}</Code>{" "}
            (enabled: <Code>{detail.enabled_count}</Code>)
          </span>
        </div>
        {detail.description && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
            {detail.description}
          </p>
        )}
      </Card>

      <section>
        <h2 className="text-md font-semibold mt-0 mb-2">Members</h2>
        <ul role="list" className="space-y-1.5">
          {detail.members.map((m) => (
            <li
              key={m.id}
              data-testid="pack-member-row"
              className="flex items-center justify-between gap-3 rounded-md border border-[var(--color-border)] bg-white p-2"
            >
              <Code className="text-[12px]">{m.id}</Code>
              {m.enabled
                ? <Badge variant="ok">{t("policies.enabled")}</Badge>
                : <Badge variant="deny">{t("policies.disabled")}</Badge>}
            </li>
          ))}
        </ul>
      </section>

      {/* D77: multi-policy simulator. Same panel as the single-policy
          page; `kind="pack"` flips it to the per-member result list. */}
      <PolicyTestPanel locale={locale} id={detail.id} kind="pack" />
    </>
  )
}
