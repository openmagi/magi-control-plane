import Link from "next/link"
import {
  cloud,
  type ConditionEntry,
  type EvidenceTypeEntry,
  type PolicyListItem,
} from "@/lib/cloud"
import { resolveFlash, codeForError } from "@/lib/flash"
import { getIntl, getT } from "@/lib/i18n/server"
import {
  Badge,
  Button,
  Card,
  Code,
  EmptyState,
  ErrorState,
  PageHeader,
} from "@/components/ui"
import { PolicyToggle } from "./_components/PolicyToggle"
import { togglePolicyAction } from "./actions"

export const dynamic = "force-dynamic"

type Tab = "policies" | "evidence" | "conditions"
const TABS: readonly Tab[] = ["policies", "evidence", "conditions"] as const

function parseTab(raw: string | undefined): Tab {
  if (raw === "evidence" || raw === "conditions") return raw
  return "policies"
}

function EnforcementBadge({ kind }: { kind: string }) {
  if (kind === "deterministic-gate") return <Badge variant="ok">{kind}</Badge>
  if (kind === "observe-only")        return <Badge variant="review">{kind}</Badge>
  if (kind === "missing")             return <Badge variant="deny">{kind}</Badge>
  return <Badge>{kind}</Badge>
}

export default async function RulesPage({
  searchParams,
}: {
  searchParams: { tab?: string; msg?: string; err?: string }
}) {
  const { t } = await getT()
  const { nf } = await getIntl()
  const tab = parseTab(searchParams.tab)
  const flash = resolveFlash(searchParams.msg, searchParams.err)

  let policies: PolicyListItem[] = []
  let policiesErr: string | null = null
  let evidence: EvidenceTypeEntry[] = []
  let evidenceErr: string | null = null
  let conditions: ConditionEntry[] = []
  let conditionsErr: string | null = null

  if (tab === "policies") {
    try { policies = await cloud.listPolicies() }
    catch (e: unknown) { policiesErr = codeForError(e) }
  } else if (tab === "evidence") {
    try { evidence = await cloud.listEvidenceTypes() }
    catch (e: unknown) { evidenceErr = codeForError(e) }
  } else {
    try { conditions = await cloud.listConditions() }
    catch (e: unknown) { conditionsErr = codeForError(e) }
  }

  return (
    <>
      <PageHeader
        title={t("rules.title")}
        description={t("rules.description")}
        actions={
          <Link href="/policies/new">
            <Button variant="primary" size="md">
              {t("rules.newButton")}
            </Button>
          </Link>
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
  items, err, nfFormat, t,
}: {
  items: PolicyListItem[]
  err: string | null
  nfFormat: (n: number) => string
  t: TFunc
}) {
  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.policies.hint")}
      </p>
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
                      {item.description || "—"}
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
                        labelOn={`${t("policies.disable")} — ${item.id}`}
                        labelOff={`${t("policies.enable")} — ${item.id}`}
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

function EvidenceTab({
  items, err, nfFormat, t,
}: {
  items: EvidenceTypeEntry[]
  err: string | null
  nfFormat: (n: number) => string
  t: TFunc
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
                className={`px-4 py-3.5 flex flex-wrap items-start justify-between gap-3 ${
                  idx > 0 ? "border-t border-black/[0.05]" : ""
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <Code className="text-sm">{row.step}</Code>
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-gray-100 text-gray-700">
                      {row.source === "builtin"
                        ? t("rules.evidence.source.builtin")
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
                <div className="pt-0.5">
                  <EnforcementBadge kind={row.enforcement} />
                </div>
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
