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
  ErrorState,
  PageHeader,
} from "@/components/ui"
import { ChecksTab } from "./_components/ChecksTab"
import { EvidenceTab } from "./_components/EvidenceTab"
import { PoliciesTab } from "./_components/PoliciesTab"
import { PacksTab } from "./_components/PacksTab"

export const dynamic = "force-dynamic"

/**
 * D82a: Rules page reorganized into FOUR semantically distinct tabs:
 *
 *   Policies        — operator-authored compositions + prebuilt rows
 *                     (rows, not cards, post D82a).
 *   Packs           — NEW (D82a). Top-level home for the policy-pack
 *                     bundle concept (cascade-enable). Moved out of
 *                     the Policies tab to give the bundle layer its
 *                     own surface.
 *   Checks          — pure functions: built-in verifiers + custom
 *                     verifiers + inline regex / llm_critic / shacl
 *                     bodies pulled from policies.
 *   Evidence records — catalog of evidence record types the system
 *                      can emit, with payload schema + recent-24h
 *                      count + jump to /ledger.
 *
 * URL params:
 *   ?tab=policies | packs | checks | evidence-types (default = policies)
 *   ?tab=conditions  → redirects to ?tab=checks (legacy bookmark grace).
 *   ?tab=verifiers   → redirects to ?tab=checks (D52a old name).
 *   ?tab=evidence    → redirects to ?tab=checks when paired with
 *                      msg=verifier_created (legacy /verifiers/new
 *                      success URL); otherwise to ?tab=evidence-types
 *                      (legacy "Verifiers tab" bookmarks land closer
 *                      to where the evidence shapes now live).
 *
 * Note on the rename from `?tab=evidence` to `?tab=evidence-types`:
 * pre-D56e the `evidence` slug was the Verifiers tab. Reusing the same
 * slug for the new evidence-records surface would silently change the
 * page a bookmark resolves to. Distinct slug + dedicated redirect.
 */

type Tab = "policies" | "packs" | "checks" | "evidence-types"
const TABS: readonly Tab[] = ["policies", "packs", "checks", "evidence-types"] as const

function parseTab(raw: string | undefined): Tab {
  if (raw === "packs" || raw === "checks" || raw === "evidence-types") return raw
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
  let packsErr: string | null = null
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
  } else if (tab === "packs") {
    // D82a: Packs got its own tab; the fetch moves with it so the
    // Policies tab does not pay for unused pack data.
    try { packs = await cloud.listPacks(locale) }
    catch (e: unknown) { packsErr = codeForError(e) }
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
          nfFormat={nf.format.bind(nf)}
          t={t}
          locale={locale}
        />
      )}
      {tab === "packs" && (
        <PacksTab items={packs} err={packsErr} t={t} />
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
            id === "packs"          ? "rules.tab.packs"    :
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

// togglePolicyAction + togglePrebuiltAction are imported directly by
// PoliciesTab / PrebuiltRow respectively (via "../actions") so this
// page file is a Next-friendly module that only exports `default` +
// the `dynamic` config knob.
