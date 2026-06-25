import Link from "next/link"
import { redirect } from "next/navigation"
import {
  cloud,
  type CheckEntry,
  type EvidenceRecordType,
  type PolicyListItem,
  type PolicyPackEntry,
  type PrebuiltPolicyEntry,
} from "@/lib/cloud"
import { resolveFlash, codeForError } from "@/lib/flash"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge,
  Button,
  Card,
  Code,
  EmptyState,
  EnforcementBadge,
  ErrorState,
  PageHeader,
} from "@/components/ui"
import { PackSection } from "./_components/PackSection"
import { PolicyToggle } from "./_components/PolicyToggle"
import { PrebuiltToggle } from "./_components/PrebuiltToggle"
import { ChecksTab } from "./_components/ChecksTab"
import { EvidenceTab } from "./_components/EvidenceTab"
import { WelcomeBanner } from "./_components/WelcomeBanner"
import { togglePolicyAction, togglePrebuiltAction } from "./actions"

export const dynamic = "force-dynamic"

/**
 * D56e: Rules page reorganized into three semantically distinct tabs:
 *
 *   Policies        — compositions the operator edits (today's tab, unchanged).
 *   Checks          — pure functions: built-in verifiers + custom
 *                     verifiers + inline regex / llm_critic / shacl
 *                     bodies pulled from policies. Replaces the old
 *                     "Verifiers" tab.
 *   Evidence records — catalog of evidence record types the system can
 *                      emit, with payload schema + recent-24h count
 *                      + jump to /ledger.
 *
 * URL params:
 *   ?tab=policies | checks | evidence-types  (default = policies)
 *   ?tab=conditions  → redirects to ?tab=checks (legacy bookmark grace).
 *   ?tab=verifiers   → redirects to ?tab=checks (D52a old name).
 *   ?tab=evidence    → redirects to ?tab=checks when paired with
 *                      msg=verifier_created (legacy /verifiers/new
 *                      success URL); otherwise to ?tab=evidence-types
 *                      (legacy "Verifiers tab" bookmarks land closer
 *                      to where the evidence shapes now live).
 *
 * Note on the rename from `?tab=evidence` to `?tab=evidence-types`:
 * pre-D56e the `evidence` slug was the Verifiers tab (a `Tab` literal
 * union of `policies | evidence | conditions`). Reusing the same slug
 * for the new evidence-records surface would silently change the page
 * a bookmark resolves to. Distinct slug + a dedicated redirect keeps
 * every legacy URL pointed at a sensible successor.
 */

type Tab = "policies" | "checks" | "evidence-types"
const TABS: readonly Tab[] = ["policies", "checks", "evidence-types"] as const

function parseTab(raw: string | undefined): Tab {
  if (raw === "checks" || raw === "evidence-types") return raw
  return "policies"
}

export default async function RulesPage({
  searchParams,
}: {
  searchParams: { tab?: string; msg?: string; err?: string }
}) {
  // D56e: legacy `conditions` and `verifiers` tab names redirect to
  // the new merged `checks` tab. Plain 307 redirect via Next's
  // server-side helper; preserves any flash params on the way through.
  if (searchParams.tab === "conditions" || searchParams.tab === "verifiers") {
    const passthrough = new URLSearchParams()
    passthrough.set("tab", "checks")
    if (searchParams.msg) passthrough.set("msg", searchParams.msg)
    if (searchParams.err) passthrough.set("err", searchParams.err)
    redirect(`/rules?${passthrough.toString()}`)
  }
  // D56e follow-up: pre-D56e the `evidence` slug rendered the Verifiers
  // tab (and /verifiers/new succeeded into `?tab=evidence&msg=verifier_created`).
  // Bookmark + browser-history grace: the verifier-success URL lands on
  // the new Checks tab (verifier authoring moved there); every other
  // `?tab=evidence` URL lands on the new evidence-records tab.
  if (searchParams.tab === "evidence") {
    const passthrough = new URLSearchParams()
    const dest =
      searchParams.msg === "verifier_created" ? "checks" : "evidence-types"
    passthrough.set("tab", dest)
    if (searchParams.msg) passthrough.set("msg", searchParams.msg)
    if (searchParams.err) passthrough.set("err", searchParams.err)
    redirect(`/rules?${passthrough.toString()}`)
  }

  const { t, locale } = await getT()
  const { nf } = await getIntl()
  const tab = parseTab(searchParams.tab)
  const flash = resolveFlash(searchParams.msg, searchParams.err)

  let policies: PolicyListItem[] = []
  let policiesErr: string | null = null
  let prebuilt: PrebuiltPolicyEntry[] = []
  let packs: PolicyPackEntry[] = []
  let checks: CheckEntry[] = []
  let checksErr: string | null = null
  let evidence: EvidenceRecordType[] = []
  let evidenceErr: string | null = null

  // Recent-24h emission counts, keyed by step name. Used by both the
  // Checks expander and Evidence catalog cards. Single batched call
  // per tab so the cloud sees one GROUP BY query.
  let emissionCounts: Record<string, number> = {}
  const SINCE_24H = 24 * 60 * 60

  if (tab === "policies") {
    try { policies = await cloud.listPolicies() }
    catch (e: unknown) { policiesErr = codeForError(e) }
    try { prebuilt = await cloud.listPrebuiltPolicies() }
    catch (e: unknown) {
      console.error(`rules: listPrebuiltPolicies failed code=${codeForError(e)}`)
    }
    try { packs = await cloud.listPacks(locale) }
    catch (e: unknown) {
      console.error(`rules: listPacks failed code=${codeForError(e)}`)
    }
  } else if (tab === "checks") {
    try {
      checks = await cloud.listChecks()
      try {
        // Only built-in / custom check ids map to ledger step names;
        // inline-* rows emit under generic `inline_<kind>` steps which
        // surface on the Evidence tab instead.
        const steps = checks
          .filter((c) => c.kind === "builtin" || c.kind === "custom")
          .map((c) => c.id)
          .filter(Boolean)
        if (steps.length > 0) {
          const r = await cloud.ledgerCounts(steps, SINCE_24H)
          emissionCounts = r.counts
        }
      } catch {
        // Leave as `{}` — each row falls through to the unavailable dash.
      }
    }
    catch (e: unknown) { checksErr = codeForError(e) }
  } else {
    // tab === "evidence-types"
    try {
      evidence = await cloud.listEvidenceRecordTypes()
      try {
        const steps = evidence.map((e) => e.id).filter(Boolean)
        if (steps.length > 0) {
          const r = await cloud.ledgerCounts(steps, SINCE_24H)
          emissionCounts = r.counts
        }
      } catch {
        // see above
      }
    }
    catch (e: unknown) { evidenceErr = codeForError(e) }
  }

  return (
    <>
      <PageHeader
        title={t("rules.title")}
        description={<RulesDescription t={t} />}
        actions={
          tab === "checks" ? (
            <div className="flex flex-wrap items-center gap-2">
              <Link href="/verifiers/new">
                <Button variant="secondary" size="md">
                  {t("rules.newVerifierButton")}
                </Button>
              </Link>
              <Link href="/policies/new">
                <Button variant="primary" size="md">
                  {t("rules.newButton")}
                </Button>
              </Link>
            </div>
          ) : (
            <Link href="/policies/new">
              <Button variant="primary" size="md">
                {t("rules.newButton")}
              </Button>
            </Link>
          )
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

      <SubTabNav tab={tab} t={t} />

      {tab === "policies" && (
        <PoliciesTab
          items={policies}
          err={policiesErr}
          prebuilt={prebuilt}
          packs={packs}
          nfFormat={nf.format.bind(nf)}
          t={t}
          locale={locale}
        />
      )}
      {tab === "checks" && (
        <ChecksTab
          items={checks}
          err={checksErr}
          nfFormat={nf.format.bind(nf)}
          t={t}
          locale={locale}
          emissionCounts={emissionCounts}
        />
      )}
      {tab === "evidence-types" && (
        <EvidenceTab
          items={evidence}
          err={evidenceErr}
          nfFormat={nf.format.bind(nf)}
          t={t}
          emissionCounts={emissionCounts}
        />
      )}
    </>
  )
}

type TFunc = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

// Sentinel used to splice the inline /ledger link into the translated
// description. We pass the marker as the {ledger} var, then split on it.
const LEDGER_MARKER = "__LEDGER__"

function RulesDescription({ t }: { t: TFunc }) {
  const parts = t("rules.description", { ledger: LEDGER_MARKER }).split(LEDGER_MARKER)
  const before = parts[0] ?? ""
  const after = parts[1] ?? ""
  return (
    <>
      {before}
      <Link
        href="/ledger"
        className="font-medium text-[var(--color-accent-light)] hover:underline"
      >
        {t("rules.description.ledgerLink")}
      </Link>
      {after}
    </>
  )
}

function SubTabNav({ tab, t }: { tab: Tab; t: TFunc }) {
  return (
    <div className="mb-5 border-b border-black/[0.08]">
      <nav className="-mb-px flex flex-wrap gap-x-6" aria-label="rules sub-navigation">
        {TABS.map((id) => {
          const active = id === tab
          const labelKey =
            id === "policies"       ? "rules.tab.policies" :
            id === "checks"         ? "rules.tab.checks"   :
                                      "rules.tab.evidenceRecords"
          return (
            <Link
              key={id}
              href={`/rules?tab=${id}`}
              aria-current={active ? "page" : undefined}
              className={`inline-flex items-center gap-2 border-b-2 px-1 py-2.5 text-sm font-semibold transition-colors duration-150 hover:no-underline ${
                active
                  ? "border-[var(--color-accent)] text-[var(--color-text-primary)]"
                  : "border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {t(labelKey as never)}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}

function PoliciesTab({
  items, err, prebuilt, packs, nfFormat, t, locale,
}: {
  items: PolicyListItem[]
  err: string | null
  prebuilt: PrebuiltPolicyEntry[]
  packs: PolicyPackEntry[]
  nfFormat: (n: number) => string
  t: TFunc
  locale: import("@/lib/i18n/dict").Locale
}) {
  // D60 follow-up: GET /policies returns every row including the
  // materialized prebuilt rows (POST /policies/prebuilt/{id}/enable
  // saves into the same store under `prebuilt/...` ids). Without
  // this filter an enabled prebuilt would render TWICE — once in
  // <PrebuiltSection> with the new toggle, and once below in the
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
      {/* D75: pack section renders ABOVE prebuilts so the
       * intent-level controls land first. */}
      <PackSection items={packs} t={t} />
      {prebuilt.length > 0 && (
        <PrebuiltSection items={prebuilt} t={t} />
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
                  <span>{t("policies.trigger")}: <Code>{item.trigger.event}</Code> · <Code>{item.trigger.matcher}</Code></span>
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

/** D54 / D60: prebuilt policy templates. D60 reframes each row as a
 * toggle — clicking the toggle calls /policies/prebuilt/{id}/enable
 * (or DELETE for disable) directly, with no wizard ride. The "Edit
 * before enabling" Link kept as a secondary path for operators who
 * want to tweak the IR before saving.
 *
 * The card border + "Active" pill mirror the enabled state so the
 * section reads at a glance — green border = on, neutral border =
 * off. Setup-required prebuilts (citation_verify, source_allowlist)
 * surface an inline callout BEFORE flipping the toggle, AND render a
 * persistent "Needs setup" chip on the card (D60 follow-up) so an
 * operator scanning the section can see the prerequisite from the
 * grid view rather than having to click the toggle first. */
function PrebuiltSection({
  items, t,
}: {
  items: PrebuiltPolicyEntry[]
  t: TFunc
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
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
        {items.map((p) => (
          <Card
            key={p.id}
            className={`flex flex-col gap-2 ${
              p.enabled
                ? "border-emerald-500/60 ring-1 ring-emerald-500/30"
                : ""
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
                    {t("rules.prebuilt.badge")}
                  </span>
                  <span className="text-sm font-semibold text-[var(--color-text-primary)]">
                    {p.title}
                  </span>
                  {p.enabled && (
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-emerald-100 text-emerald-800">
                      {t("rules.prebuilt.active")}
                    </span>
                  )}
                  {/* D60 follow-up: persistent "Needs setup" chip on
                   * OFF setup-required prebuilts so the prerequisite
                   * is visible from the grid view. We also render
                   * the chip on ENABLED setup-required rows (cloud
                   * leaves `setup_required` true even when enabled)
                   * because the operator may have used Enable
                   * Anyway and the policy is still inert. */}
                  {p.setup_required && (
                    <span
                      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-800"
                      title={p.setup_hint}
                    >
                      {t("rules.prebuilt.needsSetup")}
                    </span>
                  )}
                </div>
                <p className="mt-2 text-xs text-[var(--color-text-secondary)] leading-relaxed">
                  {p.summary}
                </p>
                <div className="mt-2 text-[11px] text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
                  <span>
                    {t("rules.prebuilt.verifier")}: <Code>{p.verifier_step}</Code>
                  </span>
                  {p.ir.trigger ? (
                    <span>
                      {t("policies.trigger")}:{" "}
                      <Code>{p.ir.trigger.event}</Code>{" · "}
                      <Code>{p.ir.trigger.matcher}</Code>
                    </span>
                  ) : null}
                  {p.ir.action ? (
                    <span>
                      {t("rules.prebuilt.action")}: <Code>{p.ir.action}</Code>
                    </span>
                  ) : null}
                </div>
              </div>
              <PrebuiltToggle
                prebuiltId={p.id}
                enabled={p.enabled}
                setupRequired={p.setup_required}
                setupHint={p.setup_hint}
                action={togglePrebuiltAction}
                labelOn={t("rules.prebuilt.disable", { title: p.title })}
                labelOff={t("rules.prebuilt.enable", { title: p.title })}
                copy={{
                  setupRequired: t("rules.prebuilt.setupRequired"),
                  setupUnconfigurableHere: t(
                    "rules.prebuilt.setupHint.unconfigurableHere",
                  ),
                  enableAnyway: t("rules.prebuilt.enableAnyway"),
                  cancel: t("rules.prebuilt.cancel"),
                  transportError: t("rules.prebuilt.transportError"),
                }}
              />
            </div>
            <div className="mt-1">
              <Link
                href={prebuiltDraftHref(p)}
                aria-label={t("rules.prebuilt.editBeforeAria", { title: p.title })}
                className="text-[11px] font-medium text-[var(--color-accent-light)] hover:underline"
              >
                {t("rules.prebuilt.editBefore")}
              </Link>
            </div>
          </Card>
        ))}
      </div>
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
function prebuiltDraftHref(p: PrebuiltPolicyEntry): string {
  // D56a: route prebuilt "Use this" to the Guided wizard's Step 6
  // (Review) rather than the raw IR editor.
  const draftJson = JSON.stringify(p.ir)
  const doubleEncoded = encodeURIComponent(encodeURIComponent(draftJson))
  return `/policies/new?mode=guided&step=6&draft=${doubleEncoded}`
}
