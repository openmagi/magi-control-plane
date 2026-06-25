import Link from "next/link"
import { cloud } from "@/lib/cloud"
import { codeForError, resolveFlash } from "@/lib/flash"
import { getT } from "@/lib/i18n/server"
import {
  Badge,
  Button,
  Card,
  Code,
  ErrorState,
  PageHeader,
} from "@/components/ui"
import { createPackAction } from "../../rules/actions"

export const dynamic = "force-dynamic"

/**
 * D75: new policy-pack page. Server component renders a form that
 * submits to `createPackAction`. The picker lists every available
 * member-policy id (user policies + prebuilts, materialized rows
 * only); the operator checks a few and submits.
 *
 * On failure (transport / 422 / 409) we redirect back here with an
 * `err` flash code; on success the dashboard lands on /rules with
 * `pack_created`.
 */
export default async function NewPolicyPackPage({
  searchParams,
}: {
  searchParams: { err?: string }
}) {
  const { t } = await getT()
  const flash = resolveFlash(undefined, searchParams.err)

  // Build the picker's option list: every materialized policy id +
  // every prebuilt id. We list prebuilts even when they haven't been
  // enabled yet so an operator can build a pack that references them;
  // the cloud's enable cascade will materialize the prebuilt on first
  // enable.
  let userPolicies: Awaited<ReturnType<typeof cloud.listPolicies>> = []
  let prebuilts: Awaited<ReturnType<typeof cloud.listPrebuiltPolicies>> = []
  let loadErr: string | null = null
  try {
    userPolicies = await cloud.listPolicies()
  } catch (e: unknown) {
    loadErr = codeForError(e)
  }
  try {
    prebuilts = await cloud.listPrebuiltPolicies()
  } catch (e: unknown) {
    loadErr = loadErr ?? codeForError(e)
  }

  // De-dupe: prebuilt rows can already appear in /policies once the
  // operator enables them. The picker should still surface them once.
  const optionIds = new Map<string, { id: string; label: string }>()
  for (const p of userPolicies) {
    if (!optionIds.has(p.id)) {
      optionIds.set(p.id, { id: p.id, label: p.description || p.id })
    }
  }
  for (const p of prebuilts) {
    if (!optionIds.has(p.id)) {
      optionIds.set(p.id, { id: p.id, label: p.title })
    }
  }
  const options = Array.from(optionIds.values()).sort((a, b) =>
    a.id.localeCompare(b.id),
  )

  return (
    <>
      <PageHeader
        title={t("packs.new.title")}
        description={t("packs.new.hint")}
        actions={
          <Link href="/rules">
            <Button variant="secondary" size="md">
              {t("packs.new.cancel")}
            </Button>
          </Link>
        }
      />
      {flash?.kind === "error" && (
        <ErrorState title={flash.text} severity="error" />
      )}
      <form action={createPackAction} className="space-y-5">
        <Card className="flex flex-col gap-3">
          <label
            htmlFor="pack-name"
            className="text-sm font-semibold text-[var(--color-text-primary)]"
          >
            {t("packs.new.fields.name")}
          </label>
          <input
            id="pack-name"
            name="name"
            required
            maxLength={200}
            className="rounded-md border border-black/10 bg-white px-3 py-2 text-sm focus:border-[var(--color-accent)] focus:outline-none"
          />
        </Card>
        <Card className="flex flex-col gap-3">
          <label
            htmlFor="pack-description"
            className="text-sm font-semibold text-[var(--color-text-primary)]"
          >
            {t("packs.new.fields.description")}
          </label>
          <textarea
            id="pack-description"
            name="description"
            maxLength={1000}
            rows={3}
            className="rounded-md border border-black/10 bg-white px-3 py-2 text-sm focus:border-[var(--color-accent)] focus:outline-none"
          />
        </Card>
        <Card className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
            {t("packs.new.fields.policies")}
          </h2>
          {options.length === 0 ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {t("packs.new.fields.policies.empty")}
            </p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-96 overflow-y-auto">
              {options.map((opt) => (
                <label
                  key={opt.id}
                  className="flex items-start gap-2 rounded-md border border-black/[0.06] bg-white px-3 py-2 text-xs hover:border-[var(--color-accent)]/40"
                >
                  <input
                    type="checkbox"
                    name="policy_ids"
                    value={opt.id}
                    className="mt-0.5"
                  />
                  <span className="flex flex-col gap-0.5">
                    <Code className="text-[10px]">{opt.id}</Code>
                    <span className="text-[var(--color-text-secondary)]">
                      {opt.label}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          )}
          {loadErr && (
            <Badge variant="review">{loadErr}</Badge>
          )}
        </Card>
        <div className="flex flex-wrap gap-3">
          <Button variant="primary" type="submit">
            {t("packs.new.save")}
          </Button>
          <Link href="/rules">
            <Button variant="secondary" type="button">
              {t("packs.new.cancel")}
            </Button>
          </Link>
        </div>
      </form>
    </>
  )
}
