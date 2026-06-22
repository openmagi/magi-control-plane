import { cloud, type PresetEntry } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"
import { CategorySection } from "./_components/CategorySection"
import { PresetToggle } from "./_components/PresetToggle"
import { readDisabledPresetIds, togglePresetAction } from "./actions"

export const dynamic = "force-dynamic"

const CATEGORY_ORDER: PresetEntry["category"][] = [
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
]

function EnforcementBadge({ kind }: { kind: PresetEntry["enforcement"] }) {
  const variant =
    kind === "enforcing" ? "ok" :
    kind === "always-on" ? "info" :
    kind === "preview"   ? "review" : "muted"
  return <Badge variant={variant}>{kind}</Badge>
}

interface PresetRowProps {
  p: PresetEntry
  stepLabel: string
  enabled: boolean
  labelOn: string
  labelOff: string
}

function PresetRow({ p, stepLabel, enabled, labelOn, labelOff }: PresetRowProps) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-black/[0.04] bg-white px-4 py-3 hover:border-black/[0.08] transition-colors duration-150">
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-1.5">
          <Code className="text-[13px] font-semibold">{p.id}</Code>
          <EnforcementBadge kind={p.enforcement} />
        </div>
        <p className="text-sm text-[var(--color-text-secondary)] leading-5 line-clamp-3">
          {p.description}
        </p>
        {p.step && (
          <div className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">
            {stepLabel}: <Code>{p.step}</Code>
          </div>
        )}
      </div>
      <PresetToggle
        presetId={p.id}
        enabled={enabled}
        action={togglePresetAction}
        labelOn={labelOn}
        labelOff={labelOff}
      />
    </div>
  )
}

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

  const labelOn = t("presets.toggle.on")
  const labelOff = t("presets.toggle.off")

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
          status={t("common.cloudUnreachable")}
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
                    stepLabel={t("presets.stepLabel")}
                    enabled={!disabled.has(p.id)}
                    labelOn={labelOn}
                    labelOff={labelOff}
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
