import Link from "next/link"
import type { PolicyListItem, PrebuiltPolicyEntry } from "@/lib/cloud"
import type { Locale } from "@/lib/i18n/dict"
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
import { PrebuiltRow } from "./PrebuiltRow"
import { WelcomeBanner } from "./WelcomeBanner"
import { togglePolicyAction } from "../actions"

type TFunc = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/**
 * D82a: Policies tab content extracted from rules/page.tsx so the file
 * is shorter and PackSection (now on its own tab) no longer mounts here.
 *
 * Renders, in order:
 *   1. WelcomeBanner — first-time visitor, when nothing yet to act on.
 *   2. PrebuiltRow list — the prebuilt policies as space-efficient rows
 *      (D82a). Replaces the prior grid-of-cards.
 *   3. User-policies grid — operator-authored policies. Materialized
 *      prebuilts (id starts with `prebuilt/`) are filtered out so each
 *      enabled prebuilt renders exactly ONCE (in the prebuilt list).
 *
 * D82a removes the PackSection mount from this tab. Packs now have a
 * dedicated tab (see PacksTab). A source-grep test pins that PackSection
 * is no longer imported here.
 */
export function PoliciesTab({
  items, err, prebuilt, nfFormat, t, locale,
}: {
  items: PolicyListItem[]
  err: string | null
  prebuilt: PrebuiltPolicyEntry[]
  nfFormat: (n: number) => string
  t: TFunc
  locale: Locale
}) {
  // D60 follow-up: GET /policies returns every row including the
  // materialized prebuilt rows (POST /policies/prebuilt/{id}/enable
  // saves into the same store under `prebuilt/...` ids). Without
  // this filter an enabled prebuilt would render TWICE — once in the
  // prebuilt list (with the PrebuiltToggle) and once below in the
  // user-policies grid with the regular PolicyToggle. The
  // user-policies grid is for OPERATOR-AUTHORED policies; the
  // prebuilt section is the canonical surface for `prebuilt/...`
  // rows. Filter at the render boundary (not at the cloud) so a
  // future surface that wants the unfiltered list still sees it.
  const userPolicies = items.filter((p) => !p.id.startsWith("prebuilt/"))
  // D72: first-time visitor banner. Show only when there is nothing on
  // this screen to act on: no user policies AND no enabled prebuilt.
  const showWelcome =
    !err && userPolicies.length === 0 && prebuilt.every((p) => !p.enabled)
  return (
    <section>
      {showWelcome && <WelcomeBanner locale={locale} />}
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.policies.hint")}
      </p>
      {/* D82a: PackSection MOVED to its own Packs tab (PacksTab). The
       * Policies tab now shows prebuilt rows + user-authored policies
       * only, so the operator's eye lands on a single ranking of
       * concrete enable surfaces rather than two overlapping bundles
       * (packs vs prebuilts) competing for attention. */}
      {prebuilt.length > 0 && (
        <PrebuiltSection items={prebuilt} t={t} locale={locale} />
      )}
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {/* D72 follow-up: when the welcome banner renders the EmptyState
          would stack a third "Build with conversation" nudge on the
          same screen. The banner already covers the no-policies path
          with stronger framing. Hide the EmptyState when the banner is
          visible so the operator sees one focused next step. */}
      {!err && userPolicies.length === 0 && !showWelcome && (
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
      {!err && userPolicies.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.policies", { n: nfFormat(userPolicies.length) })}
          </Badge>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {userPolicies.map((item) => (
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
                      {item.description || ", "}
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
                        labelOn={`${t("policies.disable")}. ${item.id}`}
                        labelOff={`${t("policies.enable")}. ${item.id}`}
                      />
                    </div>
                  </div>
                </div>
                <div className="text-xs text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
                  {/* D74a: cloud /policies omits `trigger` from
                      ContextInjectionPolicy + RunCommandPolicy listings
                      (they encode the hook surface in archetype-specific
                      fields, not the EvidencePolicy trigger triple).
                      Read defensively so the page does not crash with
                      "Cannot read properties of undefined (reading
                      'event')" when those archetypes are present. */}
                  {item.trigger ? (
                    <span>{t("policies.trigger")}: <Code>{item.trigger.event}</Code> · <Code>{item.trigger.matcher}</Code></span>
                  ) : null}
                  <span>{t("policies.source")}: <Code>{item.source}</Code></span>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

/** D54 / D60: prebuilt policy templates. D82a: rendered as ROWS, not a
 * grid of cards, so the section is space-efficient. Each PrebuiltRow
 * keeps the existing PrebuiltToggle (toggle wired to
 * /policies/prebuilt/{id}/enable) and the "Edit before enabling" Link
 * as the secondary path. */
function PrebuiltSection({
  items, t, locale,
}: {
  items: PrebuiltPolicyEntry[]
  t: TFunc
  locale: Locale
}) {
  return (
    <div className="mb-6 rounded-2xl border border-black/[0.06] bg-[var(--color-surface-1,#f9fafb)]/40 p-4">
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
          {t("rules.prebuilt.title")}
        </h2>
        <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
          {t("rules.prebuilt.hint")}
        </p>
      </div>
      <ul role="list" className="flex flex-col divide-y divide-black/[0.06] overflow-hidden rounded-xl border border-black/[0.06] bg-white">
        {items.map((p) => (
          <li key={p.id}>
            <PrebuiltRow entry={p} draftHref={prebuiltDraftHref(p)} locale={locale} />
          </li>
        ))}
      </ul>
    </div>
  )
}

/** D54: build the /policies/new prefill URL for one prebuilt entry.
 *
 * The PolicyBuilder's `_parseDraftQuery` does a `decodeURIComponent` on
 * the `draft` query value before `JSON.parse`. Next.js `searchParams`
 * already decodes URL-encoded query values once, so the value the
 * builder receives must STILL carry one level of encoding when it
 * lands in the URL. Encode twice:
 *
 *   raw JSON  -> encodeURIComponent (sits in the URL as `%7B...%7D`)
 *             -> encodeURIComponent (sits in the URL as `%257B...%257D`)
 *
 * Next.js decodes once (`%25` -> `%`), the builder decodes again
 * (`%7B` -> `{`), then `JSON.parse` succeeds. */
export function prebuiltDraftHref(p: PrebuiltPolicyEntry): string {
  // D56a: route prebuilt "Use this" to the Guided wizard's Step 6
  // (Review) rather than the raw IR editor.
  const draftJson = JSON.stringify(p.ir)
  const doubleEncoded = encodeURIComponent(encodeURIComponent(draftJson))
  return `/policies/new?mode=guided&step=6&draft=${doubleEncoded}`
}
