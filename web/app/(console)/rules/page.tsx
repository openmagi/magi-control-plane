import Link from "next/link"
import {
  cloud,
  type PresetEntry,
  type PolicyListItem,
} from "@/lib/cloud"
import { resolveFlash, codeForError } from "@/lib/flash"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge,
  Button,
  Card,
  Code,
  EmptyState,
  ErrorState,
  PageHeader,
} from "@/components/ui"
import { CategorySection } from "../presets/_components/CategorySection"
import { VerifierRow } from "./_components/VerifierRow"
import { PolicyToggle } from "./_components/PolicyToggle"
import {
  readDisabledVerifierIds,
  togglePolicyAction,
} from "./actions"

export const dynamic = "force-dynamic"

const CATEGORY_ORDER: PresetEntry["category"][] = [
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
]

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function isCustomDisabled(p: PresetEntry, disabled: Set<string>): boolean {
  if (p.is_custom) return p.enabled === false
  return disabled.has(p.id)
}

function EnforcementBadge({ kind }: { kind: string }) {
  if (kind === "deterministic-gate") return <Badge variant="ok">{kind}</Badge>
  if (kind === "observe-only")        return <Badge variant="review">{kind}</Badge>
  return <Badge>{kind}</Badge>
}

export default async function RulesPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string } }) {
  const { t } = await getT()
  const { nf } = await getIntl()

  let verifiers: PresetEntry[] = []
  let verifiersErr: string | null = null
  try { verifiers = await cloud.listVerifiers() }
  catch (e: unknown) { verifiersErr = errMsg(e) }

  let policies: PolicyListItem[] = []
  let policiesErr: string | null = null
  try { policies = await cloud.listPolicies() }
  catch (e: unknown) { policiesErr = codeForError(e) }

  const disabledIds = await readDisabledVerifierIds()

  const byCategory: Record<string, PresetEntry[]> = {}
  for (const it of verifiers) (byCategory[it.category] ||= []).push(it)
  const enabledCount = verifiers.filter(
    (v) => !isCustomDisabled(v, disabledIds),
  ).length

  const flash = resolveFlash(searchParams.msg, searchParams.err)

  const labelOn        = t("presets.toggle.on")
  const labelOff       = t("presets.toggle.off")
  const stepLabel      = t("presets.stepLabel")
  const whenLabel      = t("presets.spec.when")
  const matchersLabel  = t("presets.spec.matchers")
  const verdictLabel   = t("presets.spec.verdict")
  const howLabel       = t("presets.spec.howItWorks")
  const schemaLabel    = t("presets.spec.inputSchema")
  const notWiredLabel  = t("presets.spec.notWired")
  const customBadge    = t("rules.badge.custom")
  const editLabel      = t("rules.custom.edit")
  const deleteLabel    = t("rules.custom.delete")
  const confirmDelete  = t("rules.custom.confirmDelete")

  return (
    <>
      <PageHeader
        title={t("rules.title")}
        description={t("rules.description")}
        actions={
          <Link href="/rules/new">
            <Button variant="primary" size="md">{t("rules.newButton")}</Button>
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

      <section className="mb-8">
        <div className="flex flex-wrap items-baseline justify-between gap-3 mb-3">
          <div>
            <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
              {t("rules.section.verifiers")}
            </h2>
            <p className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
              {t("rules.section.verifiers.hint")}
            </p>
          </div>
          {!verifiersErr && verifiers.length > 0 && (
            <Badge variant="info">
              {t("rules.summary.verifiers", {
                total: nf.format(verifiers.length),
                on: nf.format(enabledCount),
              })}
            </Badge>
          )}
        </div>

        {verifiersErr && (
          <ErrorState
            title={t("common.cloudUnreachable")}
            body={t("common.seeServerLogs")}
          />
        )}
        {!verifiersErr && verifiers.length === 0 && (
          <EmptyState title={t("rules.empty.verifiers")} />
        )}
        {!verifiersErr && verifiers.length > 0 && (
          <div className="space-y-3">
            {CATEGORY_ORDER.map((cat) => {
              const list = byCategory[cat] || []
              if (list.length === 0) return null
              const enabledInCat = list.filter(
                (v) => !isCustomDisabled(v, disabledIds),
              ).length
              const countLabel = `${nf.format(enabledInCat)} / ${nf.format(list.length)}`
              return (
                <CategorySection
                  key={cat}
                  id={`cat-${cat}`}
                  title={t(`presets.category.${cat}` as never)}
                  hint={t(`presets.categoryHint.${cat}` as never)}
                  countLabel={countLabel}
                >
                  {list.map((p) => (
                    <VerifierRow
                      key={p.id}
                      p={p}
                      enabled={!isCustomDisabled(p, disabledIds)}
                      labelOn={labelOn}
                      labelOff={labelOff}
                      stepLabel={stepLabel}
                      whenLabel={whenLabel}
                      matchersLabel={matchersLabel}
                      verdictLabel={verdictLabel}
                      howLabel={howLabel}
                      schemaLabel={schemaLabel}
                      notWiredLabel={notWiredLabel}
                      customBadgeLabel={customBadge}
                      editLabel={editLabel}
                      deleteLabel={deleteLabel}
                      confirmDeleteLabel={confirmDelete}
                    />
                  ))}
                </CategorySection>
              )
            })}
          </div>
        )}
      </section>

      <section>
        <div className="flex flex-wrap items-baseline justify-between gap-3 mb-3">
          <div>
            <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
              {t("rules.section.policies")}
            </h2>
            <p className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
              {t("rules.section.policies.hint")}
            </p>
          </div>
          {!policiesErr && policies.length > 0 && (
            <Badge variant="info">
              {t("rules.summary.policies", { n: nf.format(policies.length) })}
            </Badge>
          )}
        </div>

        {policiesErr && (
          <ErrorState
            title={t("common.cloudUnreachable")}
            body={t("common.seeServerLogs")}
          />
        )}
        {!policiesErr && policies.length === 0 && (
          <EmptyState
            title={t("rules.empty.policies")}
            action={
              <Link href="/rules/new">
                <Button variant="primary">{t("rules.empty.policies.cta")}</Button>
              </Link>
            }
          />
        )}
        {!policiesErr && policies.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {policies.map((item) => (
              <Card key={item.id} className="flex flex-col gap-3">
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
                        action={togglePolicyAction}
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
              </Card>
            ))}
          </div>
        )}
      </section>
    </>
  )
}
