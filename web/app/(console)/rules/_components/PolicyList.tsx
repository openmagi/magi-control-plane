import Link from "next/link"
import type { PolicyGroupItem, PolicyListItem } from "@/lib/cloud"
import {
  Badge, Button, Card, EmptyState, EnforcementBadge, ErrorState,
} from "@/components/ui"
import type { Locale } from "@/lib/i18n/dict"
import { deletePolicyGroupAction, togglePolicyGroupAction } from "../actions"
import { WelcomeBanner } from "./WelcomeBanner"

/**
 * C1 (audit Q1 / decision 1: rule -> policy -> pack): the POLICY-first list.
 *
 * The old Policies tab stacked a thin multi-rule-only PolicyGroupSection over
 * PoliciesTab, which rendered the raw RULE store (listPolicies) as cards and
 * mislabeled the count "N policies". A free-standing one-rule policy therefore
 * showed with rule framing, and a compound showed as N separate rule cards.
 *
 * This renders the COMPLETE policy view from listPolicyGroups (authored
 * compounds + free-standing rules synthesized as one-rule policies) as the
 * single primary list. The USER unit is the policy; each policy's member RULES
 * (the implementation detail) live in an expandable drill-down, looked up from
 * the rule store by id. Policy-level enable/disable + delete cascade to all
 * members; pack membership shows as chips.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

export interface PolicyListProps {
  groups: PolicyGroupItem[]
  /** Rule store keyed by id, for member-rule detail (trigger + enforcement). */
  rulesById: Map<string, PolicyListItem>
  err: string | null
  nfFormat: (n: number) => string
  t: T
  locale: Locale
  /** policyId -> pack labels (pack-centric runtime chips). */
  policyPacks?: Record<string, string[]>
  /** Pack-centric runtime: activation lives in Claude Code, so per-policy
   *  toggles are hidden and "which pack" chips are shown instead. */
  packCentric?: boolean
}

export function PolicyList({
  groups, rulesById, err, nfFormat, t, locale,
  policyPacks = {}, packCentric = false,
}: PolicyListProps) {
  const showWelcome = !err && groups.length === 0

  return (
    <section data-testid="policy-list">
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
      {!err && groups.length === 0 && !showWelcome && (
        <EmptyState
          title={t("rules.empty.policies.title")}
          body={t("rules.empty.policies.body")}
          action={
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link href="/policies/new">
                <Button variant="primary">{t("rules.empty.policies.cta.primary")}</Button>
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
      {!err && groups.length > 0 && (
        <>
          <Badge variant="info" className="mb-3">
            {t("rules.summary.policies", { n: nfFormat(groups.length) })}
          </Badge>
          <div className="flex flex-col gap-3">
            {groups.map((g) => (
              <PolicyCard
                key={g.id}
                group={g}
                rulesById={rulesById}
                t={t}
                packCentric={packCentric}
                packs={policyPacks[g.id] ?? []}
              />
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function PolicyCard({
  group: g, rulesById, t, packCentric, packs,
}: {
  group: PolicyGroupItem
  rulesById: Map<string, PolicyListItem>
  t: T
  packCentric: boolean
  packs: string[]
}) {
  const n = g.rule_ids.length
  return (
    <Card className="flex flex-col gap-2" data-testid={`policy-card-${g.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-medium">{g.id}</span>
            {g.kind === "compound" ? <Badge variant="default">{t("rules.policy.compound")}</Badge> : null}
            <Badge variant={g.enabled ? "ok" : "review"}>
              {g.enabled ? t("rules.policy.enabled") : t("rules.policy.disabled")}
            </Badge>
            {g.mixed ? <Badge variant="deny">{t("rules.policy.mixed")}</Badge> : null}
            {g.missing_rules && g.missing_rules.length > 0
              ? <Badge variant="deny">{t("rules.policy.missing", { n: g.missing_rules.length })}</Badge>
              : null}
          </div>
          {g.description ? (
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{g.description}</p>
          ) : null}
          {packCentric && <PackChips packs={packs} t={t} />}
        </div>
        {/* Policy-level actions. Under pack-centric runtime activation lives
            in Claude Code, so the enable toggle is hidden (delete stays). */}
        <div className="flex items-center gap-1 shrink-0">
          {!packCentric && (
            <form action={togglePolicyGroupAction}>
              <input type="hidden" name="id" value={g.id} />
              <input type="hidden" name="enabled" value={g.enabled ? "false" : "true"} />
              <Button type="submit" variant="ghost" size="sm">
                {g.enabled ? t("rules.policy.disable") : t("rules.policy.enable")}
              </Button>
            </form>
          )}
          <form action={deletePolicyGroupAction}>
            <input type="hidden" name="id" value={g.id} />
            <Button type="submit" variant="ghost" size="sm">{t("rules.policy.delete")}</Button>
          </form>
        </div>
      </div>

      {/* Member rules: the implementation detail of the policy. */}
      <details className="group rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40"
               data-testid={`policy-rules-${g.id}`}>
        <summary className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:bg-black/[0.02]">
          <span>{t("rules.policy.rulesCount", { n })}</span>
          <span aria-hidden className="inline-block transition-transform duration-150 group-open:rotate-180">▾</span>
        </summary>
        <div className="px-3 pb-2 pt-1">
          <ul className="flex flex-col gap-1.5">
            {g.rule_ids.map((rid) => {
              const rule = rulesById.get(rid)
              const missing = g.missing_rules?.includes(rid)
              return (
                <li key={rid} className="flex flex-wrap items-center gap-2 text-[12px]">
                  <Link
                    href={`/policies/${encodeURI(rid)}`}
                    className="font-mono text-[var(--color-accent-light)] hover:underline"
                  >
                    {rid}
                  </Link>
                  {missing ? (
                    <Badge variant="deny">{t("rules.policy.ruleMissing")}</Badge>
                  ) : rule ? (
                    <>
                      {rule.trigger ? (
                        <span className="text-[var(--color-text-tertiary)]">
                          {rule.trigger.event}
                          {rule.trigger.matcher ? ` · ${rule.trigger.matcher}` : ""}
                        </span>
                      ) : null}
                      <EnforcementBadge kind={rule.enforcement} />
                    </>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      </details>
    </Card>
  )
}

function PackChips({ packs, t }: { packs: string[]; t: T }) {
  if (packs.length === 0) {
    return (
      <div className="mt-1.5">
        <Badge variant="review">{t("packs.orphan")}</Badge>
      </div>
    )
  }
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {packs.map((name) => (
        <Badge key={name} variant="info">{name}</Badge>
      ))}
    </div>
  )
}
