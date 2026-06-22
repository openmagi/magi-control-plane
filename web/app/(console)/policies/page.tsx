import Link from "next/link"
import { cloud, type PolicyListItem } from "@/lib/cloud"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { resolveFlash, codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Button, Card, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

async function toggleEnabled(formData: FormData) {
  "use server"
  let id: string
  try {
    id = validatePolicyId(formData.get("id"))
  } catch {
    redirect("/policies?err=invalid_id")
  }
  const enabled = formData.get("enabled") === "true"
  const requireConfirm = formData.get("require_confirm") === "1"
  const confirmed = formData.get("confirmed") === "1"
  if (requireConfirm && !enabled && !confirmed) {
    redirect(`/policies?confirm_disable=${encodeURIComponent(id)}`)
  }
  try {
    await cloud.setEnabled(id, enabled)
    revalidatePath("/policies")
    redirect(`/policies?msg=toggled`)
  } catch (e: unknown) {
    redirect(`/policies?err=${codeForError(e)}`)
  }
}

function EnforcementBadge({ kind }: { kind: string }) {
  if (kind === "deterministic-gate") return <Badge variant="ok">{kind}</Badge>
  if (kind === "observe-only")        return <Badge variant="review">{kind}</Badge>
  return <Badge>{kind}</Badge>
}

function PolicyCard(
  { item, t, confirmDisableFor }: {
    item: PolicyListItem
    confirmDisableFor: string | null
    t: (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string
  },
) {
  const isHighStakes = item.enforcement === "deterministic-gate"
  const showConfirm = confirmDisableFor === item.id

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Link
            href={`/policies/${encodeURI(item.id)}`}
            className="hover:no-underline"
          >
            <Code className="text-sm">{item.id}</Code>
          </Link>
          <p className="mt-2 text-sm text-[var(--color-text-secondary)] line-clamp-3 break-words">
            {item.description || "—"}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <EnforcementBadge kind={item.enforcement} />
          {item.enabled
            ? <Badge variant="ok">{t("policies.enabled")}</Badge>
            : <Badge variant="deny">{t("policies.disabled")}</Badge>}
        </div>
      </div>

      <div className="text-xs text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
        <span>{t("policies.trigger")}: <Code>{item.trigger.event}</Code> · <Code>{item.trigger.matcher}</Code></span>
        <span>{t("policies.source")}: <Code>{item.source}</Code></span>
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-2 justify-end">
        {showConfirm ? (
          <form action={toggleEnabled} className="flex flex-wrap items-center gap-2">
            <input type="hidden" name="id" value={item.id} />
            <input type="hidden" name="enabled" value="false" />
            <input type="hidden" name="confirmed" value="1" />
            <span className="text-xs text-[var(--color-review-fg)]">
              {t("policies.confirmDisable.body")}
            </span>
            <Button
              type="submit"
              variant="danger"
              size="sm"
              aria-label={`${t("policies.confirmDisable.confirm")} — ${item.id}`}
            >
              {t("policies.confirmDisable.confirm")}
            </Button>
            <Link
              href="/policies"
              className="text-xs text-[var(--color-text-tertiary)] hover:no-underline px-2 py-1"
            >
              {t("common.cancel")}
            </Link>
          </form>
        ) : (
          <form action={toggleEnabled}>
            <input type="hidden" name="id" value={item.id} />
            <input
              type="hidden"
              name="enabled"
              value={item.enabled ? "false" : "true"}
            />
            {isHighStakes && item.enabled && (
              <input type="hidden" name="require_confirm" value="1" />
            )}
            <Button
              type="submit"
              variant={item.enabled ? "secondary" : "primary"}
              size="sm"
              aria-pressed={item.enabled}
              aria-label={
                item.enabled
                  ? `${t("policies.disable")} — ${item.id}`
                  : `${t("policies.enable")} — ${item.id}`
              }
            >
              {item.enabled ? t("policies.disable") : t("policies.enable")}
            </Button>
          </form>
        )}
      </div>
    </Card>
  )
}

export default async function PoliciesPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string; confirm_disable?: string } }) {
  const { t } = await getT()
  const { nf } = await getIntl()
  let items: PolicyListItem[]
  let err: string | null = null
  try { items = await cloud.listPolicies() }
  catch (e: unknown) { items = []; err = codeForError(e) }

  const flash = resolveFlash(searchParams.msg, searchParams.err)

  return (
    <>
      <PageHeader
        title={err ? t("policies.titleUnavailable") : t("policies.title")}
        description={
          !err && items.length > 0
            ? t("policies.count", { n: nf.format(items.length) })
            : undefined
        }
        actions={
          <Link href="/policies/new">
            <Button variant="primary" size="md">
              {t("policies.newPolicy")}
            </Button>
          </Link>
        }
      />

      {flash?.kind === "ok" && (
        <Card role="status" aria-live="polite" tone="status" className="mb-3">
          <Badge variant="ok">{flash.text}</Badge>
        </Card>
      )}
      {flash?.kind === "error" && (
        <ErrorState status={flash.text} title={flash.text} severity="error" />
      )}
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState
          title={t("policies.empty.title")}
          body={t("policies.empty.body")}
          action={
            <Link href="/policies/compile">
              <Button variant="primary">{t("policies.empty.cta")}</Button>
            </Link>
          }
        />
      )}

      {items.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {items.map(item =>
            <PolicyCard
              key={item.id}
              item={item}
              confirmDisableFor={searchParams.confirm_disable ?? null}
              t={t}
            />
          )}
        </div>
      )}
    </>
  )
}
