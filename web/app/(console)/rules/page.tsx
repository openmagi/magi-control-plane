import Link from "next/link"
import {
  cloud,
  type ConditionEntry,
  type EvidenceTypeEntry,
  type PolicyListItem,
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
import { PolicyToggle } from "./_components/PolicyToggle"
import { VerifierExpander } from "./_components/VerifierExpander"
import { togglePolicyAction } from "./actions"

export const dynamic = "force-dynamic"

type Tab = "policies" | "evidence" | "conditions"
const TABS: readonly Tab[] = ["policies", "evidence", "conditions"] as const

function parseTab(raw: string | undefined): Tab {
  if (raw === "evidence" || raw === "conditions") return raw
  return "policies"
}

export default async function RulesPage({
  searchParams,
}: {
  searchParams: { tab?: string; msg?: string; err?: string }
}) {
  const { t, locale } = await getT()
  const { nf } = await getIntl()
  const tab = parseTab(searchParams.tab)
  const flash = resolveFlash(searchParams.msg, searchParams.err)

  let policies: PolicyListItem[] = []
  let policiesErr: string | null = null
  let prebuilt: PrebuiltPolicyEntry[] = []
  // D54: prebuilt fetch failures are silent. The section is a nice-to-
  // have catalog, not the operator's own data. We hide the section
  // when the call fails rather than blocking the Policies tab on it.
  let evidence: EvidenceTypeEntry[] = []
  let evidenceErr: string | null = null
  let conditions: ConditionEntry[] = []
  let conditionsErr: string | null = null

  // D52c: per-row "Recent emissions (last 24h)" counts on the Verifiers
  // tab. Map step → 24h count. Filled in parallel below; missing keys
  // render a dash instead of a fake 0 (a 0 means the cloud answered; a
  // dash means we couldn't reach it).
  let emissionCounts: Record<string, number> = {}

  if (tab === "policies") {
    try { policies = await cloud.listPolicies() }
    catch (e: unknown) { policiesErr = codeForError(e) }
    // D54: prebuilt catalog. Fetched in parallel-ish with listPolicies
    // (the await above already settled by the time we get here; this
    // is the simplest reliable shape and keeps the failure paths
    // separated). A prebuilt-fetch error doesn't surface a banner;
    // the section just hides, since it carries no operator data.
    // D54 follow-up: log the failure code distinctly so an admin-key
    // misconfiguration (the endpoint is admin-key gated, the policies
    // endpoint is API-key gated, so the two can disagree) shows up
    // in the dashboard's server log instead of being lost in /dev/null.
    try { prebuilt = await cloud.listPrebuiltPolicies() }
    catch (e: unknown) {
      console.error(`rules: listPrebuiltPolicies failed code=${codeForError(e)}`)
    }
  } else if (tab === "evidence") {
    try {
      evidence = await cloud.listEvidenceTypes()
      // D52c follow-up: single batched `/ledger/counts` call replaces
      // the per-row fan-out (was: `Promise.all(evidence.map(...))`
      // → K HTTP round-trips + K full-tenant SQL scans, scaling as
      // O(V * N_tenant_rows) per render). The cloud now does one
      // GROUP BY query and returns `{step: count}` for every step.
      // Per-row errors here only mute the widget, not the tab.
      const SINCE_24H = 24 * 60 * 60
      try {
        const steps = evidence.map((e) => e.step).filter(Boolean)
        if (steps.length > 0) {
          const r = await cloud.ledgerCounts(steps, SINCE_24H)
          emissionCounts = r.counts
        }
      } catch {
        // Leave emissionCounts as `{}` so each row renders the
        // unavailable-dash. Same shape as before, one swallowed
        // call instead of K.
      }
    }
    catch (e: unknown) { evidenceErr = codeForError(e) }
  } else {
    try { conditions = await cloud.listConditions() }
    catch (e: unknown) { conditionsErr = codeForError(e) }
  }

  return (
    <>
      <PageHeader
        title={t("rules.title")}
        description={<RulesDescription t={t} />}
        actions={
          tab === "evidence" ? (
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
          nfFormat={nf.format.bind(nf)}
          t={t}
        />
      )}
      {tab === "evidence" && (
        <EvidenceTab
          items={evidence}
          err={evidenceErr}
          nfFormat={nf.format.bind(nf)}
          t={t}
          locale={locale}
          emissionCounts={emissionCounts}
        />
      )}
      {tab === "conditions" && (
        <ConditionsTab
          items={conditions}
          err={conditionsErr}
          nfFormat={nf.format.bind(nf)}
          t={t}
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
            id === "policies"  ? "rules.tab.policies"   :
            id === "evidence"  ? "rules.tab.evidence"   :
                                 "rules.tab.conditions"
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
  items, err, prebuilt, nfFormat, t,
}: {
  items: PolicyListItem[]
  err: string | null
  /** D54: prebuilt catalog. Empty list = the cloud call failed (silent
   * hide) OR the cloud returned no entries. Either way the section is
   * omitted; we never render an empty prebuilt header. */
  prebuilt: PrebuiltPolicyEntry[]
  nfFormat: (n: number) => string
  t: TFunc
}) {
  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.policies.hint")}
      </p>
      {prebuilt.length > 0 && (
        <PrebuiltSection items={prebuilt} t={t} />
      )}
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState
          title={t("rules.empty.policies")}
          action={
            <Link href="/policies/new">
              <Button variant="primary">{t("rules.empty.policies.cta")}</Button>
            </Link>
          }
        />
      )}
      {!err && items.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.policies", { n: nfFormat(items.length) })}
          </Badge>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {items.map((item) => (
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

/** D54: prebuilt policy templates. Rendered above the operator's own
 * policies so the "this is what the verifier does in practice" mental
 * model lives next to where the operator authors policies (not on the
 * Verifiers tab, which sticks to verifier=algorithm post-D54).
 *
 * Each card has a "Use this" link to /policies/new?mode=advanced&draft=
 * <encoded JSON of the prebuilt IR>. PolicyBuilder picks the draft up
 * via the existing `_parseDraftQuery` path; nothing here calls a
 * dedicated install endpoint. The operator reviews the prefilled
 * advanced editor (id, description, trigger, requires, action) and
 * saves through the normal PUT /policies path. */
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
          <Card key={p.id} className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
                {t("rules.prebuilt.badge")}
              </span>
              <span className="text-sm font-semibold text-[var(--color-text-primary)]">
                {p.title}
              </span>
            </div>
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
              {p.summary}
            </p>
            <div className="text-[11px] text-[var(--color-text-tertiary)] flex flex-wrap gap-x-3 gap-y-1">
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
            <div className="mt-1">
              <Link
                href={prebuiltDraftHref(p)}
                aria-label={t("rules.prebuilt.useThis.aria", { title: p.title })}
              >
                <Button variant="secondary" size="sm">
                  {t("rules.prebuilt.useThis")}
                </Button>
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
 * The PolicyBuilder's `_parseDraftQuery` (web/app/(console)/policies/new/
 * page.tsx) does a `decodeURIComponent` on the `draft` query value before
 * `JSON.parse`. Next.js `searchParams` already decodes URL-encoded query
 * values once, so the value the builder receives must STILL carry one
 * level of encoding when it lands in the URL. We therefore encode twice:
 *
 *   raw JSON  -> encodeURIComponent (sits in the URL as `%7B...%7D`)
 *             -> encodeURIComponent (sits in the URL as `%257B...%257D`)
 *
 * Next.js decodes once (`%25` -> `%`), the builder decodes again
 * (`%7B` -> `{`), then `JSON.parse` succeeds. The existing
 * saveWizard fallback path (see same file, ~line 434) mirrors this
 * double-encode by going through URLSearchParams.set on a value that
 * was already encodeURIComponent'd; we hand-build the query so the
 * shape is auditable in one place. */
function prebuiltDraftHref(p: PrebuiltPolicyEntry): string {
  // D56a: route prebuilt "Use this" to the Guided wizard's Step 6
  // (Review) rather than the raw IR editor (mode=advanced). Step 6
  // round-trips the prefill through its WizardState parser and offers
  // per-field Edit jumps to the relevant earlier step so the operator
  // can fill in placeholder fields (allowlists, prompts, ttl, …) in a
  // form-based UI instead of editing JSON.
  const draftJson = JSON.stringify(p.ir)
  const doubleEncoded = encodeURIComponent(encodeURIComponent(draftJson))
  return `/policies/new?mode=guided&step=6&draft=${doubleEncoded}`
}

function EvidenceTab({
  items, err, nfFormat, t, locale, emissionCounts,
}: {
  items: EvidenceTypeEntry[]
  err: string | null
  nfFormat: (n: number) => string
  t: TFunc
  /** Threaded down to VerifierExpander -> VerifierSamplesList so the
   * client-component leaf can rebuild `t` locally without crossing
   * the RSC boundary with a function. */
  locale: import("@/lib/i18n/dict").Locale
  // D52c: undefined value = the cloud call failed (render dash). 0 =
  // cloud answered, no emissions in window.
  emissionCounts: Record<string, number>
}) {
  const builtin = items.filter((i) => i.source === "builtin").length
  const derived = items.length - builtin
  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.evidence.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState title={t("rules.empty.evidence")} />
      )}
      {!err && items.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.evidence", {
              total: nfFormat(items.length),
              builtin: nfFormat(builtin),
              derived: nfFormat(derived),
            })}
          </Badge>
          <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
            {items.map((row, idx) => (
              <div
                key={row.step}
                className={`px-4 py-3.5 ${
                  idx > 0 ? "border-t border-black/[0.05]" : ""
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <Code className="text-sm">{row.step}</Code>
                      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
                        {row.source === "builtin"
                          ? t("rules.evidence.source.builtin")
                          : row.source === "custom"
                            ? t("rules.evidence.source.custom")
                            : t("rules.evidence.source.derived")}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-[var(--color-text-secondary)] leading-relaxed">
                      {row.description}
                    </p>
                    {row.used_by_policies.length > 0 && (
                      <div className="mt-1.5 text-[11px] text-[var(--color-text-tertiary)]">
                        {t("rules.evidence.usedBy")}: {row.used_by_policies.map((pid, i) => (
                          <span key={pid}>
                            {i > 0 && ", "}
                            <Link
                              href={`/policies/${encodeURI(pid)}`}
                              className="font-mono text-[var(--color-accent-light)] hover:underline"
                            >
                              {pid}
                            </Link>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  {/* D54: drop the "enforcing" pill on verifier cards.
                      Enforcement is a POLICY-level attribute (the same
                      verifier can back one policy with action=block and
                      another with action=audit); rendering it here
                      conflated verifier with policy. The BUILT-IN /
                      CUSTOM source badge above already carries the only
                      catalog-level signal an operator needs. The
                      EnforcementBadge component itself stays for use on
                      the Policies tab / policy detail / ledger. */}
                </div>
                <VerifierExpander
                  step={row.step}
                  t={t}
                  locale={locale}
                  recentEmissions24h={
                    Object.prototype.hasOwnProperty.call(emissionCounts, row.step)
                      ? emissionCounts[row.step]
                      : null
                  }
                  nfFormat={nfFormat}
                  // D52c follow-up: pass `source` so the expander can
                  // mark custom-source rows as "preview, not bound to
                  // runtime" so a count of 0 there is structural (no
                  // runtime path), not "no usage". `policy-derived`
                  // with enforcement=missing gets the same treatment
                  // (the policy references a step nothing implements).
                  source={row.source}
                  enforcement={row.enforcement}
                  // D52d follow-up: forward the operator's authored
                  // field_checks for custom-source rows so the
                  // expander renders the tree instead of falling
                  // through to the "no descriptor" placeholder.
                  // Built-in rows ignore this prop and use the
                  // descriptor mirror.
                  fieldChecksOverride={
                    row.source === "custom" ? row.field_checks : undefined
                  }
                />
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function ConditionsTab({
  items, err, nfFormat, t,
}: {
  items: ConditionEntry[]
  err: string | null
  nfFormat: (n: number) => string
  t: TFunc
}) {
  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.conditions.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      {!err && items.length === 0 && (
        <EmptyState title={t("rules.empty.conditions")} />
      )}
      {!err && items.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.conditions", { n: nfFormat(items.length) })}
          </Badge>
          <div className="rounded-2xl border border-black/[0.06] bg-white overflow-hidden">
            {items.map((row, idx) => (
              <div
                key={`${row.kind}:${row.value}:${row.policy_id}:${idx}`}
                className={`px-4 py-3.5 ${
                  idx > 0 ? "border-t border-black/[0.05]" : ""
                }`}
              >
                <div className="flex flex-wrap items-baseline gap-2 mb-1">
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                    row.kind === "regex" || row.kind === "llm_critic" || row.kind === "shacl"
                      ? "bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]"
                      : "bg-gray-100 text-gray-700"
                  }`}>
                    {row.kind === "sentinel_re"
                      ? t("rules.condition.kind.sentinel")
                      : row.kind === "tool_match"
                        ? t("rules.condition.kind.tool")
                        : row.kind === "regex"
                          ? t("rules.condition.kind.regex")
                          : row.kind === "llm_critic"
                            ? t("rules.condition.kind.llm")
                            : t("rules.condition.kind.shacl")}
                  </span>
                  <Code className="text-[12.5px] truncate max-w-full">
                    {row.value}
                  </Code>
                </div>
                <div className="text-[11px] text-[var(--color-text-tertiary)]">
                  {t("rules.condition.fromPolicy")}:{" "}
                  <Link
                    href={`/policies/${encodeURI(row.policy_id)}`}
                    className="font-mono text-[var(--color-accent-light)] hover:underline"
                  >
                    {row.policy_id}
                  </Link>
                  {" · "}
                  <Code>{row.trigger_event}</Code>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  )
}
