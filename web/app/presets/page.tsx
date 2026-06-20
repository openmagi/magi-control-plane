import { cloud, type PresetEntry } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge, Card, Code, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

const CATEGORY_ORDER: PresetEntry["category"][] = [
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
]

function EnforcementBadge({ kind }: { kind: PresetEntry["enforcement"] }) {
  return (
    <Badge variant={kind === "enforcing" ? "ok" : "muted"}>
      {kind}
    </Badge>
  )
}

function PresetCard({ p, stepLabel }: { p: PresetEntry; stepLabel: string }) {
  return (
    <Card className="flex flex-col gap-2 h-full">
      <div className="flex items-start justify-between gap-3">
        <Code className="text-sm">{p.id}</Code>
        <EnforcementBadge kind={p.enforcement} />
      </div>
      <p className="text-sm text-[var(--color-text-secondary)] line-clamp-3">
        {p.description}
      </p>
      {p.step && (
        <div className="text-xs text-[var(--color-text-tertiary)]">
          {stepLabel}: <Code>{p.step}</Code>
        </div>
      )}
    </Card>
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

  const byCategory: Record<string, PresetEntry[]> = {}
  for (const it of items) (byCategory[it.category] ||= []).push(it)
  const wiredCount = items.filter(i => i.enforcement === "enforcing").length

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
        <div className="flex items-center gap-2 mb-4 text-sm text-[var(--color-text-tertiary)]">
          <Badge variant="info">
            {t("presets.summary", {
              total: nf.format(items.length),
              wired: nf.format(wiredCount),
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
        <div className="space-y-8">
          {CATEGORY_ORDER.map(cat => {
            const list = byCategory[cat] || []
            if (list.length === 0) return null
            const wired = list.filter(i => i.enforcement === "enforcing").length
            return (
              <section
                key={cat}
                aria-labelledby={`cat-${cat}`}
                className="space-y-3"
              >
                <header className="flex flex-wrap items-baseline gap-3">
                  <h2 id={`cat-${cat}`} className="text-md font-semibold m-0">
                    {t(`presets.category.${cat}` as never)}
                  </h2>
                  <span className="text-xs text-[var(--color-text-tertiary)]">
                    {t("presets.count", { n: nf.format(list.length) })}
                    {wired > 0 && t("presets.wired", { n: nf.format(wired) })}
                  </span>
                </header>
                <p className="text-xs text-[var(--color-text-tertiary)] -mt-1">
                  {t(`presets.categoryHint.${cat}` as never)}
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  {list.map(p => (
                    <PresetCard
                      key={p.id}
                      p={p}
                      stepLabel={t("presets.stepLabel")}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      )}
    </>
  )
}
