import { cloud, type PresetEntry } from "@/lib/cloud"

export const dynamic = "force-dynamic"

const CATEGORY_ORDER: PresetEntry["category"][] = [
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
]

const CATEGORY_DESC: Record<PresetEntry["category"], string> = {
  ANSWER:   "Response quality at final-answer time.",
  FACT:     "Factual grounding and source backing.",
  CODING:   "Coding-turn evidence and discipline.",
  TASK:     "Task lifecycle, goals, and completion.",
  OUTPUT:   "Output shape, delivery, and language.",
  RESEARCH: "Research coverage and source authority.",
  MEMORY:   "Cross-session memory consistency.",
  SECURITY: "Always-on safety guards.",
}

function EnforcementBadge({ kind }: { kind: PresetEntry["enforcement"] }) {
  // v1.1 only emits "enforcing" (5 wired) or "preview" (vendor). The 4-tier
  // type stays open for future expansion, but until a verifier returns
  // always-on/capability we don't pre-style them — distinct colors would
  // suggest semantic states the runtime doesn't actually produce.
  const cls = kind === "enforcing" ? "tag ok" : "tag"
  return <span className={cls} aria-label={`enforcement: ${kind}`}>{kind}</span>
}

function PresetCard({ p }: { p: PresetEntry }) {
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <code style={{ fontSize: 13 }}>{p.id}</code>
          <div className="muted" style={{ marginTop: 4, fontSize: 12, color: "#aab" }}>
            {p.description}
          </div>
          {p.step && (
            <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
              policy IR step: <code>{p.step}</code>
            </div>
          )}
        </div>
        <EnforcementBadge kind={p.enforcement} />
      </div>
    </div>
  )
}

function CategorySection({
  category, items,
}: { category: PresetEntry["category"]; items: PresetEntry[] }) {
  if (items.length === 0) return null
  const enforcing = items.filter(i => i.enforcement === "enforcing").length
  return (
    <section aria-labelledby={`cat-${category}`}>
      <h2 id={`cat-${category}`}>
        {category}
        <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
          {items.length} preset{items.length === 1 ? "" : "s"}
          {enforcing > 0 ? `, ${enforcing} wired` : ""}
        </span>
      </h2>
      <p className="muted" style={{ marginTop: -4, marginBottom: 10 }}>
        {CATEGORY_DESC[category]}
      </p>
      {items.map(p => <PresetCard key={p.id} p={p} />)}
    </section>
  )
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default async function PresetsPage() {
  let items: PresetEntry[] = []
  let err: string | null = null
  try { items = await cloud.listPresets() }
  catch (e: unknown) { err = errMsg(e) }

  const byCategory: Record<string, PresetEntry[]> = {}
  for (const it of items) {
    (byCategory[it.category] ||= []).push(it)
  }
  const wiredCount = items.filter(i => i.enforcement === "enforcing").length

  return (
    <>
      <h1>
        Presets {err ? "(unavailable)" : `(${items.length} total, ${wiredCount} wired)`}
      </h1>
      <p className="muted" style={{ marginTop: -4, marginBottom: 16 }}>
        Catalog of verifier presets. <strong>Enforcing</strong> presets are wired to a
        live verifier in this control plane — policies may bind to their step.{" "}
        <strong>Preview</strong> presets are surfaced for label parity with magi-agent
        but have no runtime gate here yet.
      </p>
      {err && (
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">see server logs</p>
        </div>
      )}
      {!err && items.length === 0 && (
        <div className="card muted">No presets surfaced.</div>
      )}
      {!err && CATEGORY_ORDER.map(cat =>
        <CategorySection key={cat} category={cat} items={byCategory[cat] || []} />
      )}
    </>
  )
}
