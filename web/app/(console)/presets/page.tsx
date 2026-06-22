import { cloud, type PresetEntry } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import { Badge, EmptyState, ErrorState, PageHeader } from "@/components/ui"
import { CategorySection } from "./_components/CategorySection"
import { PresetRow } from "./_components/PresetRow"
import { readDisabledPresetIds } from "./actions"

export const dynamic = "force-dynamic"

const CATEGORY_ORDER: PresetEntry["category"][] = [
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
]

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default async function PresetsPage() {
  const { t } = await getT()
  const { nf } = await getIntl()

  let items: PresetEntry[] = []
  let err: string | null = null
  try { items = await cloud.listPresets() }
  catch (e: unknown) { err = errMsg(e) }

  const disabled = await readDisabledPresetIds()

  const byCategory: Record<string, PresetEntry[]> = {}
  for (const it of items) (byCategory[it.category] ||= []).push(it)
  const enabledCount = items.filter(i => !disabled.has(i.id)).length

  const labelOn        = t("presets.toggle.on")
  const labelOff       = t("presets.toggle.off")
  const stepLabel      = t("presets.stepLabel")
  const whenLabel      = t("presets.spec.when")
  const matchersLabel  = t("presets.spec.matchers")
  const verdictLabel   = t("presets.spec.verdict")
  const howLabel       = t("presets.spec.howItWorks")
  const schemaLabel    = t("presets.spec.inputSchema")
  const notWiredLabel  = t("presets.spec.notWired")

  return (
    <>
      <PageHeader
        title={t("presets.title")}
        description={
          !err
            ? t("presets.description.lead", {
                enforcing: t("presets.description.enforcing"),
                preview: t("presets.description.preview"),
              })
            : undefined
        }
      />

      {!err && items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-5">
          <Badge variant="info">
            {t("presets.summary", {
              total: nf.format(items.length),
              wired: nf.format(enabledCount),
            })}
          </Badge>
        </div>
      )}

      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState title={t("hitl.empty")} />
      )}

      {!err && items.length > 0 && (
        <div className="space-y-3">
          {CATEGORY_ORDER.map(cat => {
            const list = byCategory[cat] || []
            if (list.length === 0) return null
            const enabledInCat = list.filter(i => !disabled.has(i.id)).length
            const countLabel = `${nf.format(enabledInCat)} / ${nf.format(list.length)}`
            return (
              <CategorySection
                key={cat}
                id={`cat-${cat}`}
                title={t(`presets.category.${cat}` as never)}
                hint={t(`presets.categoryHint.${cat}` as never)}
                countLabel={countLabel}
              >
                {list.map(p => (
                  <PresetRow
                    key={p.id}
                    p={p}
                    enabled={!disabled.has(p.id)}
                    labelOn={labelOn}
                    labelOff={labelOff}
                    stepLabel={stepLabel}
                    whenLabel={whenLabel}
                    matchersLabel={matchersLabel}
                    verdictLabel={verdictLabel}
                    howLabel={howLabel}
                    schemaLabel={schemaLabel}
                    notWiredLabel={notWiredLabel}
                  />
                ))}
              </CategorySection>
            )
          })}
        </div>
      )}
    </>
  )
}
