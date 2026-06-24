import Link from "next/link"
import { redirect } from "next/navigation"
import {
  cloud,
  type CheckEntry,
  type EvidenceRecordType,
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
import { ChecksTab } from "./_components/ChecksTab"
import { EvidenceTab } from "./_components/EvidenceTab"
import { togglePolicyAction } from "./actions"

export const dynamic = "force-dynamic"

/**
 * D56e: Rules page reorganized into three semantically distinct tabs:
 *
 *   Policies — compositions the operator edits (today's tab, unchanged).
 *   Checks   — pure functions: built-in verifiers + custom verifiers +
 *              inline regex / llm_critic / shacl bodies pulled from
 *              policies. Replaces the old "Verifiers" + "Conditions"
 *              tabs which split this single concept across two pages.
 *   Evidence — catalog of evidence record types the system can emit,
 *              with payload schema + recent-24h count + jump to /ledger.
 *
 * URL params:
 *   ?tab=policies | checks | evidence  (default = policies)
 *   ?tab=conditions  → redirects to ?tab=checks (legacy bookmark grace).
 *   ?tab=verifiers   → redirects to ?tab=checks (D52a old name).
 */

type Tab = "policies" | "checks" | "evidence"
const TABS: readonly Tab[] = ["policies", "checks", "evidence"] as const

function parseTab(raw: string | undefined): Tab {
  if (raw === "checks" || raw === "evidence") return raw
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

  const { t, locale } = await getT()
  const { nf } = await getIntl()
  const tab = parseTab(searchParams.tab)
  const flash = resolveFlash(searchParams.msg, searchParams.err)

  let policies: PolicyListItem[] = []
  let policiesErr: string | null = null
  let prebuilt: PrebuiltPolicyEntry[] = []
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
    // tab === "evidence"
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
          nfFormat={nf.format.bind(nf)}
          t={t}
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
      {tab === "evidence" && (
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
            id === "policies" ? "rules.tab.policies" :
            id === "checks"   ? "rules.tab.checks"   :
                                "rules.tab.evidence"
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
 * model lives next to where the operator authors policies. */
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
