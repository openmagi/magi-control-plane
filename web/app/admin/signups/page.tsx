import { cloud, CloudConfigError, type Signup } from "@/lib/cloud"
import { getIntl, getT } from "@/lib/i18n/server"
import { revalidatePath } from "next/cache"
import { cookies } from "next/headers"
import { redirect } from "next/navigation"
import {
  Badge, Card, CodeBlock, CopyButton, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const PROVISIONED_COOKIE = "magi-cp-provisioned"

function _tenantIdFromEmail(email: string): string {
  // Stable, URL-safe, alphanumeric-and-hyphen per backend regex.
  // Local-part + first 6 chars of domain + 4 random — collisions handled
  // by the backend's idempotent /admin/tenants endpoint.
  const local = (email.split("@")[0] || "tenant").toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 18)
  const domain = (email.split("@")[1] || "x").toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 8)
  const rand = Math.floor(Math.random() * 0xffff).toString(36).padStart(4, "0")
  return `${local}-${domain}-${rand}`
}

async function decideAction(formData: FormData) {
  "use server"
  const signupId = Number(formData.get("signupId"))
  const status = String(formData.get("status")) as "approved" | "rejected"
  const notes = String(formData.get("notes") ?? "")
  const email = String(formData.get("email") ?? "")
  const provision = formData.get("provision") === "1"
  if (!signupId || !["approved", "rejected"].includes(status)) return

  await cloud.decideSignup(signupId, status, notes)

  if (status === "approved" && provision && email) {
    const tenantId = _tenantIdFromEmail(email)
    try {
      const out = await cloud.provisionTenant(tenantId, "alpha")
      // Stash the cleartext key in a short-lived cookie so the next render
      // shows it ONCE. Backend never re-emits it.
      const c = await cookies()
      c.set(PROVISIONED_COOKIE, JSON.stringify({
        signupId, email, tenantId: out.tenantId,
        apiKey: out.apiKey, keyId: out.keyId, prefix: out.prefix,
      }), { httpOnly: true, sameSite: "lax", maxAge: 600, path: "/admin/signups" })
    } catch (e) {
      const c = await cookies()
      c.set(PROVISIONED_COOKIE, JSON.stringify({
        signupId, email, error: errMsg(e),
      }), { httpOnly: true, sameSite: "lax", maxAge: 60, path: "/admin/signups" })
    }
  }
  revalidatePath("/admin/signups")
  redirect("/admin/signups")
}

async function dismissProvisioned() {
  "use server"
  const c = await cookies()
  c.delete(PROVISIONED_COOKIE)
  redirect("/admin/signups")
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

  const ck = await cookies()
  const provisioned = ck.get(PROVISIONED_COOKIE)?.value
    ? (JSON.parse(ck.get(PROVISIONED_COOKIE)!.value) as {
        signupId: number; email: string; tenantId?: string;
        apiKey?: string; keyId?: number; prefix?: string; error?: string
      })
    : null

  return (
    <>
      <PageHeader title={labels.title} description={labels.desc} />

      {provisioned && (
        <Card tone={provisioned.error ? "alert" : "status"} className="mb-4">
          {provisioned.error ? (
            <>
              <div className="text-sm font-medium text-[var(--color-text-primary)] mb-2">
                {locale === "ko"
                  ? `프로비저닝 실패 — ${provisioned.email}`
                  : `Provisioning failed — ${provisioned.email}`}
              </div>
              <p className="text-sm text-[var(--color-text-secondary)]">{provisioned.error}</p>
            </>
          ) : (
            <>
              <div className="text-sm font-medium text-[var(--color-text-primary)] mb-2">
                {locale === "ko"
                  ? `프로비저닝 완료 — ${provisioned.email}`
                  : `Provisioned — ${provisioned.email}`}
              </div>
              <dl className="text-xs space-y-1 mb-3">
                <div><span className="text-[var(--color-text-tertiary)]">tenant_id:</span> <code className="font-mono">{provisioned.tenantId}</code></div>
                <div><span className="text-[var(--color-text-tertiary)]">key_id:</span> <code className="font-mono">{provisioned.keyId}</code></div>
              </dl>
              <div className="text-xs text-[var(--color-text-tertiary)] mb-2">
                {locale === "ko"
                  ? "이 키는 다시 표시되지 않습니다. 지금 신청자에게 이메일로 전달하세요."
                  : "This key is not shown again. Email it to the applicant now."}
              </div>
              <div className="flex items-center gap-2">
                <CodeBlock maxHeight="auto" className="flex-1">{provisioned.apiKey}</CodeBlock>
                <CopyButton value={provisioned.apiKey ?? ""} />
              </div>
            </>
          )}
          <form action={dismissProvisioned} className="mt-3">
            <button type="submit" className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] cursor-pointer">
              {locale === "ko" ? "닫기" : "Dismiss"}
            </button>
          </form>
        </Card>
      )}

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
                <form action={decideAction} className="mt-4 space-y-2">
                  <input type="hidden" name="signupId" value={s.id} />
                  <input type="hidden" name="email" value={s.email} />
                  <div className="flex flex-wrap items-end gap-2">
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
                  </div>
                  <label className="flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                    <input type="checkbox" name="provision" value="1" defaultChecked />
                    {locale === "ko"
                      ? "승인 시 테넌트 + API 키 동시 프로비저닝 (HMAC 시크릿 필요)"
                      : "On approve: provision tenant + issue API key (needs HMAC secret)"}
                  </label>
                </form>
              )}
            </Card>
          ))}
        </div>
      )}
    </>
  )
}
