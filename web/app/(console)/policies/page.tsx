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
import { PolicyToggle } from "./_components/PolicyToggle"

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
  } catch (e: unknown) {
    redirect(`/policies?err=${codeForError(e)}`)
  }
  // NOTE: redirect() MUST live outside the try block — it throws
  // NEXT_REDIRECT, which would otherwise be caught by the catch and
  // mis-coded as cloud_unreachable (the catch-all default in
  // codeForError) even though the underlying setEnabled succeeded.
  revalidatePath("/policies")
  redirect(`/policies?msg=toggled`)
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
        <div className="flex flex-col items-end gap-2">
          <EnforcementBadge kind={item.enforcement} />
          <div className="flex items-center gap-2">
            <span className={`text-[11px] font-medium uppercase tracking-wider ${item.enabled ? "text-emerald-700" : "text-[var(--color-text-tertiary)]"}`}>
              {item.enabled ? "on" : "off"}
            </span>
            <PolicyToggle
              policyId={item.id}
              enabled={item.enabled}
              action={toggleEnabled}
              labelOn={`${t("policies.disable")} — ${item.id}`}
              labelOff={`${t("policies.enable")} — ${item.id}`}
            />
          </div>
        </div>
      </div>

      <div className="text-xs text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
        <span>{t("policies.trigger")}: <Code>{item.trigger.event}</Code> · <Code>{item.trigger.matcher}</Code></span>
        <span>{t("policies.source")}: <Code>{item.source}</Code></span>
      </div>

      {showConfirm && (
        <form action={toggleEnabled} className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--color-review-fg)]/30 bg-[var(--color-review-bg)]/40 px-3 py-2">
          <input type="hidden" name="id" value={item.id} />
          <input type="hidden" name="enabled" value="false" />
          <input type="hidden" name="confirmed" value="1" />
          <span className="text-xs text-[var(--color-review-fg)] flex-1">
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
      )}
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
        <ErrorState title={flash.text} severity="error" />
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
