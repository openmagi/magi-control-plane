import Link from "next/link"
import type {
  CoverageCell, PolicyListItem, PrebuiltPolicyEntry,
} from "@/lib/cloud"
import type { Locale } from "@/lib/i18n/dict"
import { CoverageStrip } from "../../_components/CoverageStrip"
import {
  Badge,
  Button,
  Card,
  Code,
  EmptyState,
  EnforcementBadge,
  ErrorState,
} from "@/components/ui"
import { PolicyToggle } from "./PolicyToggle"
import { PrebuiltToggle } from "./PrebuiltToggle"
import { WelcomeBanner } from "./WelcomeBanner"
import {
  togglePolicyAction,
  togglePrebuiltAction,
} from "../actions"
import { translate } from "@/lib/i18n/dict"

type TFunc = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/**
 * D82f: unified policy list.
 *
 * Screenshot review: the D82d/e layout still treated Prebuilt as its
 * own section (larger header, its own card shell, its own row style)
 * while User policies below used a completely different card style
 * with a different set of meta fields. Kevin's feedback: "prebuilt를
 * 저렇게 따로 특별대우할 필요가 있나?" — no.
 *
 * The tab now renders ONE unified card grid. Prebuilts and user
 * policies use the same card shell, the same meta row, the same
 * toggle affordance. Prebuilts are sorted first and carry a small
 * BUILT-IN badge alongside the enforcement chip. Every prebuilt-
 * specific action (Setup, Edit before enabling) still exists but is
 * a Link inside the card body rather than a fifth inline chip.
 */
export function PoliciesTab({
  items, err, prebuilt, nfFormat, t, locale,
  packCentric = false, policyPacks = {},
  codexEnabled = false, codexCoverage = {},
}: {
  items: PolicyListItem[]
  err: string | null
  prebuilt: PrebuiltPolicyEntry[]
  nfFormat: (n: number) => string
  t: TFunc
  locale: Locale
  /** P4: pack-centric runtime. When true the tab is a READ-ONLY
   *  preview: per-policy toggles are dropped (activation lives in
   *  Claude Code) and each card renders "which pack" chips. */
  packCentric?: boolean
  /** P4: policyId -> pack labels the policy belongs to. Only populated
   *  under `packCentric`. */
  policyPacks?: Record<string, string[]>
  /** P4 (Codex runtime adapter): the tenant has Codex enabled, so each
   *  card's coverage strip renders both runtimes. */
  codexEnabled?: boolean
  /** P4: policyId -> Codex coverage cell. Only populated when
   *  `codexEnabled`. */
  codexCoverage?: Record<string, CoverageCell>
}) {
  // D60 follow-up: GET /policies returns every row including the
  // materialized prebuilt rows (POST /policies/prebuilt/{id}/enable
  // saves into the same store under `prebuilt/...` ids). Filter at
  // the render boundary so each enabled prebuilt renders exactly
  // once (via the prebuilt card, which owns the prebuilt-specific
  // actions).
  const userPolicies = items.filter((p) => !p.id.startsWith("prebuilt/"))
  const showWelcome =
    !err && userPolicies.length === 0 && prebuilt.every((p) => !p.enabled)

  const totalCards = userPolicies.length + prebuilt.length

  return (
    <section>
      {packCentric && <PackCentricBanner t={t} />}
      {showWelcome && <WelcomeBanner locale={locale} />}
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.policies.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && totalCards === 0 && !showWelcome && (
        <EmptyState
          title={t("rules.empty.policies.title")}
          body={t("rules.empty.policies.body")}
          action={
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link href="/policies/new">
                <Button variant="primary">
                  {t("rules.empty.policies.cta.primary")}
                </Button>
              </Link>
              <Link
                href="/policies/new?mode=conversational"
                className="text-sm font-medium text-[var(--color-accent-light)] hover:underline"
              >
                {t("rules.empty.policies.cta.secondary")}
              </Link>
            </div>
          }
        />
      )}
      {!err && totalCards > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.policies", { n: nfFormat(totalCards) })}
          </Badge>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {/* Prebuilts render first so a first-time visitor sees the
             *  ready-to-toggle bundle before the empty custom
             *  policies grid. */}
            {prebuilt.map((entry) => (
              <PrebuiltCard
                key={entry.id}
                entry={entry}
                draftHref={prebuiltDraftHref(entry)}
                locale={locale}
                t={t}
                packCentric={packCentric}
                packs={policyPacks[entry.id] ?? []}
                codexEnabled={codexEnabled}
                codexCell={codexCoverage[entry.id]}
              />
            ))}
            {userPolicies.map((item) => (
              <UserPolicyCard
                key={item.id}
                item={item}
                t={t}
                packCentric={packCentric}
                packs={policyPacks[item.id] ?? []}
                codexEnabled={codexEnabled}
                codexCell={codexCoverage[item.id]}
              />
            ))}
          </div>
        </>
      )}
    </section>
  )
}

/** D82f: prebuilt policy in the SAME card shell as user policies.
 *  Adds a BUILT-IN badge alongside the enforcement chip, and a
 *  quiet secondary link ("Setup" for setup-required, "Edit before
 *  enabling" otherwise). */
export function PrebuiltCard({
  entry, draftHref, locale, t, packCentric = false, packs = [],
  codexEnabled = false, codexCell,
}: {
  entry: PrebuiltPolicyEntry
  draftHref: string
  locale: Locale
  t: TFunc
  packCentric?: boolean
  packs?: string[]
  codexEnabled?: boolean
  codexCell?: CoverageCell
}) {
  const inferredEnforcement = entry.ir.action === "block"
    ? "enforcing"
    : entry.ir.action === "ask"
      ? "advisory"
      : "log-only"
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Code className="text-sm">{entry.id}</Code>
          <p className="mt-2 text-sm text-[var(--color-text-secondary)] line-clamp-3 break-words">
            {entry.title}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
              {t("rules.prebuilt.badge")}
            </span>
            <EnforcementBadge kind={inferredEnforcement} />
          </div>
          {/* P4: per-policy activation moves to Claude Code under
           *  pack-centric mode, so the toggle is dropped from the
           *  read-only preview. */}
          {!packCentric && (
            <div className="flex items-center gap-2">
              <span className={`text-[11px] font-medium uppercase tracking-wider ${entry.enabled ? "text-emerald-700" : "text-[var(--color-text-tertiary)]"}`}>
                {entry.enabled ? "on" : "off"}
              </span>
              <PrebuiltToggle
                prebuiltId={entry.id}
                enabled={entry.enabled}
                action={togglePrebuiltAction}
                labelOn={t("rules.prebuilt.disable", { title: entry.title })}
                labelOff={t("rules.prebuilt.enable", { title: entry.title })}
                copy={{ transportError: t("rules.prebuilt.transportError") }}
              />
            </div>
          )}
        </div>
      </div>
      {packCentric && <PackChips packs={packs} t={t} />}
      {codexEnabled && (
        <CoverageStrip t={t} codexEnabled={codexEnabled} codexCell={codexCell} />
      )}
      <div className="text-xs text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
        {entry.ir.trigger ? (
          <span>{t("policies.trigger")}: <Code>{entry.ir.trigger.event}</Code> · <Code>{entry.ir.trigger.matcher}</Code></span>
        ) : null}
        <span>{t("rules.prebuilt.verifier")}: <Code>{entry.verifier_step}</Code></span>
      </div>
      <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border-subtle)] pt-2">
        {entry.setup_required ? (
          <Link
            href={setupDocsHref(entry.id)}
            className="text-[11px] font-medium text-amber-900 hover:underline"
            title={entry.setup_hint || undefined}
          >
            {t("rules.prebuilt.setup")} →
          </Link>
        ) : (
          <Link
            href={draftHref}
            className="text-[11px] font-medium text-[var(--color-accent-light)] hover:underline"
          >
            {t("rules.prebuilt.editBefore")}
          </Link>
        )}
        {entry.setup_required && (
          <span
            className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-800"
            title={entry.setup_hint}
          >
            {t("rules.prebuilt.row.statusNeedsSetup")}
          </span>
        )}
      </div>
    </Card>
  )
}

function UserPolicyCard({
  item, t, packCentric = false, packs = [],
  codexEnabled = false, codexCell,
}: {
  item: PolicyListItem
  t: TFunc
  packCentric?: boolean
  packs?: string[]
  codexEnabled?: boolean
  codexCell?: CoverageCell
}) {
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
            {item.description || " "}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <EnforcementBadge kind={item.enforcement} />
          {/* P4: read-only preview under pack-centric mode — no toggle. */}
          {!packCentric && (
            <div className="flex items-center gap-2">
              <span className={`text-[11px] font-medium uppercase tracking-wider ${item.enabled ? "text-emerald-700" : "text-[var(--color-text-tertiary)]"}`}>
                {item.enabled ? "on" : "off"}
              </span>
              <PolicyToggle
                policyId={item.id}
                enabled={item.enabled}
                action={togglePolicyAction}
                labelOn={`${t("policies.disable")}. ${item.id}`}
                labelOff={`${t("policies.enable")}. ${item.id}`}
              />
            </div>
          )}
        </div>
      </div>
      <div className="text-xs text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
        {item.trigger ? (
          <span>{t("policies.trigger")}: <Code>{item.trigger.event}</Code> · <Code>{item.trigger.matcher}</Code></span>
        ) : null}
        <span>{t("policies.source")}: <Code>{item.source}</Code></span>
      </div>
      {codexEnabled && (
        <CoverageStrip t={t} codexEnabled={codexEnabled} codexCell={codexCell} />
      )}
      {packCentric && <PackChips packs={packs} t={t} />}
    </Card>
  )
}

/** P4: "which pack" chip list for a policy card. Renders one chip per
 * pack the policy belongs to; an amber "orphan" chip when the policy is
 * in no pack (it fires nowhere until an operator adds it to one). */
function PackChips({ packs, t }: { packs: string[]; t: TFunc }) {
  if (packs.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--color-border-subtle)] pt-2">
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {t("packs.whichPack")}
        </span>
        <Badge variant="review">{t("packs.orphan")}</Badge>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--color-border-subtle)] pt-2">
      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {t("packs.whichPack")}
      </span>
      {packs.map((name) => (
        <Badge key={name} variant="info">{name}</Badge>
      ))}
    </div>
  )
}

/** P4: banner explaining the pack-centric shift, linking to the packs
 * tab. Shown at the top of the read-only Policies preview. */
function PackCentricBanner({ t }: { t: TFunc }) {
  return (
    <Card tone="status" className="mb-4">
      <p className="text-sm font-semibold text-[var(--color-text-primary)]">
        {t("rules.packCentric.banner.title")}
      </p>
      <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
        {t("rules.packCentric.banner.body")}
      </p>
      <Link
        href="/rules?tab=packs"
        className="mt-2 inline-block text-xs font-medium text-[var(--color-accent-light)] hover:underline"
      >
        {t("rules.packCentric.banner.link")} →
      </Link>
    </Card>
  )
}

/** D82d: per-prebuilt docs anchor for the Setup button. */
function setupDocsHref(prebuiltId: string): string {
  const slug = prebuiltId.replace(/^prebuilt\//, "")
  return `/docs/operator#${slug}`
}

/** D54: build the /policies/new prefill URL for one prebuilt entry.
 *
 * The PolicyBuilder's `_parseDraftQuery` does a `decodeURIComponent` on
 * the `draft` query value before `JSON.parse`. Next.js `searchParams`
 * already decodes URL-encoded query values once, so the value the
 * builder receives must STILL carry one level of encoding when it
 * lands in the URL. Encode twice. */
export function prebuiltDraftHref(p: PrebuiltPolicyEntry): string {
  const draftJson = JSON.stringify(p.ir)
  const doubleEncoded = encodeURIComponent(encodeURIComponent(draftJson))
  return `/policies/new?mode=guided&step=6&draft=${doubleEncoded}`
}
