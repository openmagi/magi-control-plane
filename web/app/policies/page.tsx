import Link from "next/link"
import { cloud, type PolicyListItem } from "@/lib/cloud"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { resolveFlash, codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"

export const dynamic = "force-dynamic"

async function toggleEnabled(formData: FormData) {
  "use server"
  let id: string
  try {
    id = validatePolicyId(formData.get("id"))
  } catch {
    redirect("/policies?err=invalid_id")
  }
  const enabled = formData.get("enabled") === "true"
  // Confirm step: disabling a deterministic-gate policy requires explicit confirm.
  const requireConfirm = formData.get("require_confirm") === "1"
  const confirmed = formData.get("confirmed") === "1"
  if (requireConfirm && !enabled && !confirmed) {
    redirect(`/policies?confirm_disable=${encodeURIComponent(id)}`)
  }
  try {
    await cloud.setEnabled(id, enabled)
    revalidatePath("/policies")
    redirect(`/policies?msg=toggled`)
  } catch (e: unknown) {
    redirect(`/policies?err=${codeForError(e)}`)
  }
}

function EnforcementBadge({ kind }: { kind: string }) {
  const cls = kind === "deterministic-gate" ? "tag ok"
            : kind === "observe-only" ? "tag review"
            : "tag"
  return <span className={cls}>{kind}</span>
}

function PolicyCard({ item, confirmDisableFor }:
                    { item: PolicyListItem; confirmDisableFor: string | null }) {
  const isHighStakes = item.enforcement === "deterministic-gate"
  const showConfirm = confirmDisableFor === item.id
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div style={{ flex: 1, minWidth: 280, overflowWrap: "anywhere" }}>
          <Link href={`/policies/${encodeURI(item.id)}`}>
            <code style={{ fontSize: 14 }}>{item.id}</code>
          </Link>
          <div className="muted" style={{ marginTop: 4, wordBreak: "break-word" }}>
            {item.description || "(no description)"}
          </div>
          <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
            trigger: <code>{item.trigger.event}</code> ·{" "}
            <code>{item.trigger.matcher}</code> · source: <code>{item.source}</code>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          <EnforcementBadge kind={item.enforcement} />
          {item.enabled
            ? <span className="tag ok" aria-hidden="true">enabled</span>
            : <span className="tag deny" aria-hidden="true">disabled</span>}
          {showConfirm ? (
            <form action={toggleEnabled} style={{ gap: 4 }}>
              <input type="hidden" name="id" value={item.id} />
              <input type="hidden" name="enabled" value="false" />
              <input type="hidden" name="confirmed" value="1" />
              <span className="muted" style={{ fontSize: 11 }}>Disable enforcement?</span>
              <button className="danger" type="submit"
                      aria-label={`Confirm disable of policy ${item.id}`}>
                Confirm disable
              </button>
              <Link href="/policies" className="muted">cancel</Link>
            </form>
          ) : (
            <form action={toggleEnabled}>
              <input type="hidden" name="id" value={item.id} />
              <input type="hidden" name="enabled" value={item.enabled ? "false" : "true"} />
              {isHighStakes && item.enabled && (
                <input type="hidden" name="require_confirm" value="1" />
              )}
              <button type="submit"
                      aria-pressed={item.enabled}
                      aria-label={`${item.enabled ? "Disable" : "Enable"} policy ${item.id}`}>
                {item.enabled ? "Disable" : "Enable"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}

export default async function PoliciesPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string; confirm_disable?: string } }) {
  let items: PolicyListItem[]
  let err: string | null = null
  try { items = await cloud.listPolicies() }
  catch (e: unknown) { items = []; err = codeForError(e) }

  const flash = resolveFlash(searchParams.msg, searchParams.err)

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h1>{err ? "Policies (unavailable)" : `Policies (${items.length})`}</h1>
        <Link href="/policies/new"><button className="primary">+ New policy</button></Link>
      </div>
      {flash?.kind === "ok" && (
        <div className="card" role="status" aria-live="polite">
          <span className="tag ok">{flash.text}</span>
        </div>
      )}
      {flash?.kind === "error" && (
        <div className="card" role="alert" aria-live="assertive">
          <span className="tag deny">{flash.text}</span>
        </div>
      )}
      {err && (
        <div className="card" role="alert">
          <span className="tag deny">cloud unreachable</span>
          <p className="muted">see server logs</p>
        </div>
      )}
      {items.length === 0 && !err && (
        <div className="card muted">
          No policies yet. Add one via the cloud admin API
          (<code>PUT /policies/{`{id}`}</code>) — UI policy builder coming soon.
        </div>
      )}
      {items.map(item =>
        <PolicyCard key={item.id} item={item}
                    confirmDisableFor={searchParams.confirm_disable ?? null} />
      )}
    </>
  )
}
