import Link from "next/link"
import { redirect } from "next/navigation"
import {
  cloud,
  type CheckEntry,
  type CoverageCell,
  type EvidenceRecordType,
  type PackCoverage,
  type PolicyGroupItem,
  type PolicyListItem,
  type PolicyPackEntry,
  type PrebuiltPolicyEntry,
} from "@/lib/cloud"
import { resolveFlash, codeForError } from "@/lib/flash"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Button,
  Card,
  PageHeader,
} from "@/components/ui"
import { EvidenceTab } from "./_components/EvidenceTab"
import { PolicyList } from "./_components/PolicyList"
import { PrebuiltCard, prebuiltDraftHref } from "./_components/PoliciesTab"
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

// H1 (decision 2): the "checks" + "evidence-types" tabs merged into one
// "evidence" tab (the check is the top-level entity, its emitted records are
// the drill-down). Legacy slugs redirect below.
type Tab = "policies" | "packs" | "evidence"
const TABS: readonly Tab[] = ["policies", "packs", "evidence"] as const

function parseTab(raw: string | undefined): Tab {
  if (raw === "packs" || raw === "evidence") return raw
  return "policies"
}

export default async function RulesPage({
  searchParams,
}: {
  searchParams: { tab?: string; msg?: string; err?: string; templates?: string }
}) {
  // H1 (decision 2): every legacy checks/evidence slug now lands on the
  // single merged `evidence` tab. `conditions` (D56e) + `verifiers` (D52a)
  // + `checks` + `evidence-types` all redirect there. Plain 307 redirect
  // via Next's server-side helper; preserves flash params.
  if (
    searchParams.tab === "conditions"
    || searchParams.tab === "verifiers"
    || searchParams.tab === "checks"
    || searchParams.tab === "evidence-types"
  ) {
    const passthrough = new URLSearchParams()
    passthrough.set("tab", "evidence")
    if (searchParams.msg) passthrough.set("msg", searchParams.msg)
    if (searchParams.err) passthrough.set("err", searchParams.err)
    redirect(`/rules?${passthrough.toString()}`)
  }

  const { t, locale } = await getT()
  const { nf } = await getIntl()
  const tab = parseTab(searchParams.tab)
  const flash = resolveFlash(searchParams.msg, searchParams.err)

  // Fresh-install clarity: the prebuilt template catalog + the built-in
  // packs are hidden by default so a new operator sees only what they
  // authored (empty to start), not nine ready-made cards to scroll past.
  // `?templates=1` reveals the catalog on demand ("Browse templates").
  // The floor pack (source=user, always-on) is never a template and
  // always shows; the backend catalog is untouched.
  const showTemplates = searchParams.templates === "1"

  let policies: PolicyListItem[] = []
  let policyGroups: PolicyGroupItem[] = []
  let policiesErr: string | null = null
  let prebuilt: PrebuiltPolicyEntry[] = []
  let packs: PolicyPackEntry[] = []
  let packsErr: string | null = null
  let checks: CheckEntry[] = []
  let evidence: EvidenceRecordType[] = []
  let evidenceErr: string | null = null

  // Recent-24h emission counts, keyed by step name. Used by both the
  // Checks expander and Evidence catalog cards. Single batched call
  // per tab so the cloud sees one GROUP BY query.
  let emissionCounts: Record<string, number> = {}
  const SINCE_24H = 24 * 60 * 60

  // P4 (pack-centric runtime): when MAGI_CP_PACK_CENTRIC_RUNTIME is on
  // the Policies tab becomes a READ-ONLY preview — per-policy toggles
  // are gone (activation lives in Claude Code via /magi:pack:*), and
  // each policy card gains a "which pack" chip list. A banner at the top
  // explains the shift and links to the packs tab. The legacy toggle
  // path is preserved verbatim when the flag is off so a zero-downtime
  // rollout keeps the existing enabled-policy behaviour intact.
  const packCentric = _packCentricEnabled()

  // policyId -> pack labels the policy belongs to. Built once from the
  // pack list so each card can render its "which pack" chips without an
  // extra per-card round-trip. Only populated on the Policies tab under
  // pack-centric mode (the chips are the whole point of the read-only
  // preview).
  let policyPacks: Record<string, string[]> = {}

  // P4 (Codex runtime adapter): whether THIS tenant has the Codex
  // runtime enabled, plus per-policy / per-pack coverage cells for the
  // dashboard strips. Only fetched when the tab needs it AND the build
  // has codex on (MAGI_CP_CODEX_RUNTIME_ENABLED) - a CC-only tenant
  // never pays the per-card coverage round-trips, and the strips render
  // CC-only. Tenant id is the single-tenant-beta "default".
  let codexEnabled = false
  let codexCoverage: Record<string, CoverageCell> = {}
  let packCoverage: Record<string, PackCoverage> = {}

  async function _resolveCodexEnabled(): Promise<boolean> {
    try {
      const rt = await cloud.getTenantRuntime("default")
      return rt.codex_enabled === true
    } catch (e: unknown) {
      // Coverage strips degrade to CC-only; the tab still renders.
      console.error(`rules: getTenantRuntime failed code=${codeForError(e)}`)
      return false
    }
  }

  if (tab === "policies") {
    try { policies = await cloud.listPolicies() }
    catch (e: unknown) { policiesErr = codeForError(e) }
    // pack -> policy -> rule: authored policies (the multi-rule ones are shown
    // grouped above the per-rule grid). Best-effort; a failure just hides the
    // grouped section, the rule grid still renders.
    try { policyGroups = await cloud.listPolicyGroups() }
    catch { /* grouped section optional */ }
    // Prebuilt templates only when the operator opts in via ?templates=1.
    if (showTemplates) {
      try { prebuilt = await cloud.listPrebuiltPolicies() }
      catch (e: unknown) {
        console.error(`rules: listPrebuiltPolicies failed code=${codeForError(e)}`)
      }
    }
    if (packCentric) {
      try {
        const packList = await cloud.listPacks(locale)
        policyPacks = _buildPolicyPackIndex(packList)
      } catch (e: unknown) {
        // Chips degrade to absent; the read-only list still renders.
        console.error(`rules: listPacks for chips failed code=${codeForError(e)}`)
      }
    }
    codexEnabled = await _resolveCodexEnabled()
    if (codexEnabled) {
      const ids = [
        ...prebuilt.map((p) => p.id),
        ...policies.map((p) => p.id),
      ]
      const cells = await Promise.all(ids.map(async (id) => {
        try {
          const c = await cloud.getPolicyCoverage(id, "codex")
          return [id, c.coverage] as const
        } catch { return null }
      }))
      codexCoverage = Object.fromEntries(
        cells.filter((c): c is readonly [string, CoverageCell] => c !== null),
      )
    }
  } else if (tab === "packs") {
    // D82a: Packs got its own tab; the fetch moves with it so the
    // Policies tab does not pay for unused pack data.
    try { packs = await cloud.listPacks(locale) }
    catch (e: unknown) { packsErr = codeForError(e) }
    // Built-in packs are templates: hidden unless ?templates=1. User packs
    // (including the always-on floor pack, source=user) always show.
    if (!showTemplates) packs = packs.filter((p) => p.source !== "builtin")
    codexEnabled = await _resolveCodexEnabled()
    if (codexEnabled) {
      const rollups = await Promise.all(packs.map(async (pk) => {
        try {
          return [pk.id, await cloud.getPackCoverage(pk.id, "codex")] as const
        } catch { return null }
      }))
      packCoverage = Object.fromEntries(
        rollups.filter((r): r is readonly [string, PackCoverage] => r !== null),
      )
    }
  } else {
    // H1: tab === "evidence" - fetch BOTH the checks (top-level entities)
    // and the evidence record types (their emitted-record drill-down),
    // joined by id in EvidenceTab. Emission counts cover the union of both
    // id sets so both the record drill-down and the generic inline record
    // rows can render their recent-24h count.
    try {
      const [c, e] = await Promise.all([
        cloud.listChecks(),
        cloud.listEvidenceRecordTypes(),
      ])
      checks = c
      evidence = e
      try {
        const ids = new Set<string>()
        for (const row of checks) {
          if ((row.kind === "builtin" || row.kind === "custom") && row.id) ids.add(row.id)
        }
        for (const row of evidence) if (row.id) ids.add(row.id)
        const steps = Array.from(ids)
        if (steps.length > 0) {
          const r = await cloud.ledgerCounts(steps, SINCE_24H)
          emissionCounts = r.counts
        }
      } catch {
        // Leave as `{}` - each row falls through to the unavailable dash.
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
            <div className="flex flex-wrap items-center gap-2">
              {(tab === "policies" || tab === "packs") && (
                <Link href={`/rules?tab=${tab}${showTemplates ? "" : "&templates=1"}`}>
                  <Button variant="ghost" size="md">
                    {showTemplates
                      ? t("rules.templates.hide")
                      : t("rules.templates.browse")}
                  </Button>
                </Link>
              )}
              <Link href="/policies/new/evidence-gate">
                <Button variant="secondary" size="md">
                  {t("rules.newEvidenceGateButton")}
                </Button>
              </Link>
              <Link href="/policies/new">
                <Button variant="primary" size="md">
                  {t("rules.newButton")}
                </Button>
              </Link>
            </div>
          )
        }
      />

      {/* One flash language for both outcomes: a toned banner (green for
          ok, red for error) with a status dot + message, instead of the
          old split (status Card for ok vs ErrorState for error). */}
      {flash && (
        <Card
          role={flash.kind === "error" ? "alert" : "status"}
          aria-live="polite"
          tone={flash.kind === "error" ? "alert" : "status"}
          className="mb-3 flex items-center gap-2.5"
        >
          <span
            aria-hidden="true"
            className={
              "h-1.5 w-1.5 shrink-0 rounded-full "
              + (flash.kind === "error"
                ? "bg-[var(--color-deny-fg)]"
                : "bg-[var(--color-pass-fg)]")
            }
          />
          <span className="text-sm font-medium text-[var(--color-text-primary)]">
            {flash.text}
          </span>
        </Card>
      )}

      <SubTabNav tab={tab} t={t} />

      {tab === "policies" && (
        <div className="space-y-6">
          {/* C1 (Q1 / decision 1): policy-first. The primary list is the
              complete policy view (compounds + free-standing one-rule
              policies), each with its member RULES as a drill-down. */}
          <PolicyList
            groups={policyGroups}
            rulesById={new Map(policies.map((p) => [p.id, p]))}
            err={policiesErr}
            nfFormat={nf.format.bind(nf)}
            t={t}
            locale={locale}
            packCentric={packCentric}
            policyPacks={policyPacks}
          />
          {/* Prebuilt template catalog (opt-in via ?templates=1). */}
          {showTemplates && prebuilt.length > 0 && (
            <div className="space-y-3">
              <div className="text-sm font-semibold">{t("rules.templates.heading")}</div>
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
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
              </div>
            </div>
          )}
        </div>
      )}
      {tab === "packs" && (
        <PacksTab
          items={packs}
          err={packsErr}
          t={t}
          locale={locale}
          packCentric={packCentric}
          codexEnabled={codexEnabled}
          packCoverage={packCoverage}
        />
      )}
      {tab === "evidence" && (
        <EvidenceTab
          checks={checks}
          records={evidence}
          err={evidenceErr}
          nfFormat={nf.format.bind(nf)}
          t={t}
          locale={locale}
          emissionCounts={emissionCounts}
        />
      )}
    </>
  )
}

type TFunc = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

/** Read the pack-centric runtime flag. P5 flipped the default to ON:
 * unset renders the Policies tab as a read-only preview (activation
 * lives in Claude Code). Only an explicit falsy value ("0", "false",
 * "no", "off", empty) rolls back to the legacy per-policy toggle path.
 * Mirrors `magi_cp.config.pack_centric_runtime_enabled()` and
 * `@/lib/pack-centric#isPackCentricEnabled`. */
function _packCentricEnabled(): boolean {
  const raw = process.env.MAGI_CP_PACK_CENTRIC_RUNTIME
  if (raw === undefined) return true
  const norm = raw.trim().toLowerCase()
  return !(norm === "0" || norm === "false" || norm === "no" || norm === "off" || norm === "")
}

/** P4: fold the pack list into a `policyId -> [packName, ...]` index so
 * each policy card can render its "which pack" chips. A policy can
 * belong to multiple packs; every membership becomes a chip. */
function _buildPolicyPackIndex(
  packs: import("@/lib/cloud").PolicyPackEntry[],
): Record<string, string[]> {
  const index: Record<string, string[]> = {}
  for (const pack of packs) {
    for (const policyId of pack.policy_ids) {
      if (!index[policyId]) index[policyId] = []
      if (!index[policyId].includes(pack.name)) index[policyId].push(pack.name)
    }
  }
  return index
}

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

// togglePolicyAction + togglePrebuiltAction are imported directly by
// PoliciesTab / PrebuiltRow respectively (via "../actions") so this
// page file is a Next-friendly module that only exports `default` +
// the `dynamic` config knob.
