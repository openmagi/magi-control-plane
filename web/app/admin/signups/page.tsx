import { cloud, CloudConfigError, type Signup } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import { revalidatePath } from "next/cache"
import {
  Badge, Card, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

async function decideAction(formData: FormData) {
  "use server"
  const signupId = Number(formData.get("signupId"))
  const status = String(formData.get("status")) as "approved" | "rejected"
  const notes = String(formData.get("notes") ?? "")
  if (!signupId || !["approved", "rejected"].includes(status)) return
  await cloud.decideSignup(signupId, status, notes)
  revalidatePath("/admin/signups")
}

type FilterStatus = "pending" | "approved" | "rejected" | "all"

export default async function AdminSignupsPage({
  searchParams,
}: { searchParams?: Promise<{ status?: string }> }) {
  const { t, locale } = await getT()
  const intl = await getIntl()
  const params = (await searchParams) ?? {}
  const filter: FilterStatus =
    params.status === "approved" || params.status === "rejected" || params.status === "all"
      ? params.status
      : "pending"

  let items: Signup[] = []
  let err: string | undefined
  try {
    items = await cloud.listSignups(filter === "all" ? undefined : filter)
  } catch (e) {
    err = e instanceof CloudConfigError
      ? t("admin.signups.errNoKey")
      : errMsg(e)
  }

  const labels = {
    title: locale === "ko" ? "알파 신청 큐" : "Alpha signup queue",
    desc:
      locale === "ko"
        ? "신청자 검토 후 승인/거부. 승인 후 별도로 테넌트 + API 키를 발급하세요."
        : "Triage applications. Provision tenant + API key separately after approval.",
    filter: locale === "ko" ? "상태" : "Status",
    pending: locale === "ko" ? "검토 대기" : "Pending",
    approved: locale === "ko" ? "승인됨" : "Approved",
    rejected: locale === "ko" ? "거부됨" : "Rejected",
    all: locale === "ko" ? "전체" : "All",
    empty: locale === "ko" ? "해당 상태의 신청이 없습니다." : "No signups in this state.",
    approve: locale === "ko" ? "승인" : "Approve",
    reject: locale === "ko" ? "거부" : "Reject",
    notesPh: locale === "ko" ? "메모 (선택)" : "Note (optional)",
    firm: locale === "ko" ? "소속" : "Firm",
    role: locale === "ko" ? "역할" : "Role",
    useCase: locale === "ko" ? "사용 목적" : "Use case",
    referrer: locale === "ko" ? "유입" : "Referrer",
    ip: "IP",
    submitted: locale === "ko" ? "신청 일시" : "Submitted",
  }

  if (err) {
    return (
      <>
        <PageHeader title={labels.title} description={labels.desc} />
        <ErrorState title={err} />
      </>
    )
  }

  return (
    <>
      <PageHeader title={labels.title} description={labels.desc} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-sm text-[var(--color-text-tertiary)]">{labels.filter}:</span>
        {(["pending", "approved", "rejected", "all"] as const).map(f => (
          <a key={f} href={`/admin/signups?status=${f}`}>
            <Badge variant={filter === f ? "info" : "muted"}>
              {labels[f]}
            </Badge>
          </a>
        ))}
      </div>

      {items.length === 0 ? (
        <EmptyState title={labels.empty} />
      ) : (
        <div className="space-y-3">
          {items.map(s => (
            <Card key={s.id}>
              <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
                <div>
                  <div className="text-sm font-medium text-[var(--color-text-primary)]">
                    {s.email}
                  </div>
                  <div className="text-xs text-[var(--color-text-tertiary)] mt-1">
                    {labels.submitted}: {intl.dtf.format(new Date(s.ts_created * 1000))}
                  </div>
                </div>
                <Badge variant={s.status === "approved" ? "ok"
                       : s.status === "rejected" ? "deny" : "review"}>
                  {labels[s.status as "pending" | "approved" | "rejected"]}
                </Badge>
              </div>

              <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-sm">
                {s.firm && (<>
                  <dt className="text-[var(--color-text-tertiary)]">{labels.firm}</dt>
                  <dd className="text-[var(--color-text-secondary)]">{s.firm}</dd>
                </>)}
                {s.role && (<>
                  <dt className="text-[var(--color-text-tertiary)]">{labels.role}</dt>
                  <dd className="text-[var(--color-text-secondary)]">{s.role}</dd>
                </>)}
                {s.use_case && (<>
                  <dt className="text-[var(--color-text-tertiary)]">{labels.useCase}</dt>
                  <dd className="text-[var(--color-text-secondary)] whitespace-pre-line">{s.use_case}</dd>
                </>)}
                {s.referrer && (<>
                  <dt className="text-[var(--color-text-tertiary)]">{labels.referrer}</dt>
                  <dd className="text-[var(--color-text-secondary)]">{s.referrer}</dd>
                </>)}
                {s.source_ip && (<>
                  <dt className="text-[var(--color-text-tertiary)]">{labels.ip}</dt>
                  <dd className="text-[var(--color-text-secondary)] font-mono text-xs">{s.source_ip}</dd>
                </>)}
                {s.notes && (<>
                  <dt className="text-[var(--color-text-tertiary)]">Note</dt>
                  <dd className="text-[var(--color-text-secondary)] italic">{s.notes}</dd>
                </>)}
              </dl>

              {s.status === "pending" && (
                <form action={decideAction} className="mt-4 flex flex-wrap items-end gap-2">
                  <input type="hidden" name="signupId" value={s.id} />
                  <input
                    name="notes"
                    placeholder={labels.notesPh}
                    className="flex-1 min-w-[180px] h-9 px-3 text-sm rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-surface-overlay)] text-[var(--color-text-secondary)]"
                  />
                  <button
                    type="submit"
                    name="status"
                    value="approved"
                    className="h-9 px-3 text-sm rounded-md border border-[var(--color-pass-fg)] text-[var(--color-pass-fg)] hover:bg-[var(--color-pass-bg)] cursor-pointer"
                  >
                    {labels.approve}
                  </button>
                  <button
                    type="submit"
                    name="status"
                    value="rejected"
                    className="h-9 px-3 text-sm rounded-md border border-[var(--color-deny-fg)] text-[var(--color-deny-fg)] hover:bg-[var(--color-deny-bg)] cursor-pointer"
                  >
                    {labels.reject}
                  </button>
                </form>
              )}
            </Card>
          ))}
        </div>
      )}
    </>
  )
}
