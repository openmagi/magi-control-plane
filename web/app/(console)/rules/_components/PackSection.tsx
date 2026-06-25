import Link from "next/link"
import type { PolicyPackEntry } from "@/lib/cloud"
import { Card, Code } from "@/components/ui"
import { PackToggle } from "./PackToggle"
import { togglePackAction } from "../actions"

type TFunc = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/**
 * D75: policy-pack section. Renders ABOVE the Prebuilt section on the
 * Policies tab so the operator's eye lands on the "intent-level"
 * controls first (single toggle for the bundle), with single-policy
 * prebuilts as the next level of granularity.
 *
 * Each card shows: name, description, member count, status badge,
 * and a single toggle. A "View members" link expands to the member
 * list (handled by a `<details>` element — server-friendly + no extra
 * state). The "New pack" CTA opens /policy-packs/new.
 *
 * Visual contract:
 *   status=all     → green border + "All on" badge
 *   status=partial → amber border + "Partial 3/5" badge
 *   status=none    → neutral border + "Off" badge
 */
export function PackSection({
  items, t,
}: {
  items: PolicyPackEntry[]
  t: TFunc
}) {
  const hasItems = items.length > 0
  // D75 follow-up: list_policy_packs always returns 5 built-ins + any
  // user packs, so items.length === 0 only triggers when the cloud
  // call failed (and page.tsx already drops the section out via its
  // try/catch). In the rare case it does fire, the old copy ("No
  // policy packs yet") was actively misleading because 5 built-ins
  // ship. Repurpose the empty branch for the more useful "user has
  // not built any of their own yet" case: when every visible pack is
  // a built-in we render a subtle nudge under the grid pointing at
  // the New Pack CTA rather than implying no packs exist.
  const userPackCount = items.filter((p) => p.source === "user").length
  return (
    <div className="mb-6 rounded-2xl border border-black/[0.06] bg-[var(--color-surface-1,#f9fafb)]/40 p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
            {t("rules.pack.section.title")}
          </h2>
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {t("rules.pack.section.hint")}
          </p>
        </div>
        <Link
          href="/policy-packs/new"
          className="rounded-md border border-[var(--color-accent)]/40 bg-white px-3 py-1.5 text-xs font-semibold text-[var(--color-accent)] hover:bg-[var(--color-accent)]/5"
        >
          {t("packs.new.cta")}
        </Link>
      </div>
      {hasItems ? (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
            {items.map((pack) => (
              <PackCard key={pack.id} pack={pack} t={t} />
            ))}
          </div>
          {userPackCount === 0 && (
            <p className="mt-3 text-[11px] text-[var(--color-text-tertiary)]">
              {t("rules.pack.builtinsOnlyHint")}
            </p>
          )}
        </>
      ) : (
        // True empty branch — only fires on a cloud failure (the
        // page-level try/catch usually swallows that before we
        // render). The cloud-failure path already has an error banner
        // at the top of the page; rather than misreport "No packs"
        // when 5 built-ins exist, point the operator at the CTA so a
        // retry click is right there.
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {t("rules.pack.section.cloudErrorHint")}
        </p>
      )}
    </div>
  )
}

function PackCard({
  pack, t,
}: {
  pack: PolicyPackEntry
  t: TFunc
}) {
  const borderTone =
    pack.status === "all"
      ? "border-emerald-500/60 ring-1 ring-emerald-500/30"
      : pack.status === "partial"
      ? "border-amber-400/70 ring-1 ring-amber-400/30"
      : ""
  // D75 follow-up: stale + setup_required surfacing.
  const staleMembers = pack.stale_members ?? []
  const setupRequiredMembers = pack.setup_required_members ?? []
  return (
    <Card key={pack.id} className={`flex flex-col gap-2 ${borderTone}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
              {pack.source === "builtin"
                ? t("rules.pack.badge.builtin")
                : t("rules.pack.badge.user")}
            </span>
            <span className="text-sm font-semibold text-[var(--color-text-primary)]">
              {pack.name}
            </span>
            <PackStatusBadge pack={pack} t={t} />
            {setupRequiredMembers.length > 0 && (
              <span
                className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-800"
                title={t("rules.pack.setupRequired.chip.title")}
              >
                {t("rules.pack.setupRequired.chip", {
                  n: setupRequiredMembers.length,
                })}
              </span>
            )}
            {staleMembers.length > 0 && (
              <span
                className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-red-100 text-red-800"
                title={t("rules.pack.stale.chip.title")}
              >
                {t("rules.pack.stale.chip", { n: staleMembers.length })}
              </span>
            )}
          </div>
          <p className="mt-2 text-xs text-[var(--color-text-secondary)] leading-relaxed">
            {pack.description}
          </p>
          <p className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
            {t("rules.pack.memberCount", { n: pack.member_count })}
          </p>
        </div>
        <PackToggle
          packId={pack.id}
          status={pack.status}
          action={togglePackAction}
          labelOn={t("rules.pack.toggle.disable", { name: pack.name })}
          labelOff={t("rules.pack.toggle.enable", { name: pack.name })}
          setupRequiredMembers={setupRequiredMembers}
          confirmCopy={{
            setupRequiredTitle: t("rules.pack.setupRequired.title"),
            setupRequiredBody: t("rules.pack.setupRequired.body", {
              name: pack.name,
              n: setupRequiredMembers.length,
            }),
            setupRequiredMembersHeader: t("rules.pack.setupRequired.membersHeader"),
            partialReachTitle: t("rules.pack.partialReach.title"),
            partialReachBody: t("rules.pack.partialReach.body", {
              name: pack.name,
            }),
            enableAnyway: t("rules.pack.enableAnyway"),
            cancel: t("rules.pack.cancel"),
          }}
        />
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] font-medium text-[var(--color-accent-light)] hover:underline">
          {t("rules.pack.expand.toggle")}
        </summary>
        <ul className="mt-2 space-y-1 pl-3 text-[11px] text-[var(--color-text-secondary)]">
          {pack.policy_ids.map((mid) => {
            const isStale = staleMembers.includes(mid)
            const needsSetup = setupRequiredMembers.includes(mid)
            return (
              <li key={mid} className="flex items-center gap-2">
                <Code className="text-[10px]">{mid}</Code>
                {isStale && (
                  <span className="rounded-full bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold text-red-800">
                    {t("rules.pack.stale.inlineBadge")}
                  </span>
                )}
                {needsSetup && (
                  <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
                    {t("rules.pack.setupRequired.inlineBadge")}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </details>
    </Card>
  )
}

function PackStatusBadge({
  pack, t,
}: {
  pack: PolicyPackEntry
  t: TFunc
}) {
  if (pack.status === "all") {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-emerald-100 text-emerald-800">
        {t("rules.pack.status.all")}
      </span>
    )
  }
  if (pack.status === "partial") {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-800">
        {t("rules.pack.status.partial", {
          enabled: pack.enabled_count,
          total: pack.member_count,
        })}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-gray-100 text-gray-700">
      {t("rules.pack.status.none")}
    </span>
  )
}
