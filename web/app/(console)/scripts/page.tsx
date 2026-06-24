/**
 * D63 — Scripts management page.
 *
 * Lists every script the operator has uploaded for run_command policies.
 * Surfaces metadata only (name, runtime, size, sha256 prefix, used-by
 * policies, created_at) plus a per-row delete button.
 *
 * Source of truth = cloud `/scripts`. The list rolls in
 * "used by N policies" by counting every RunCommandPolicy whose
 * `script_path` equals the script's id (best-effort: the list call
 * over /policies is cheap on a single-tenant install).
 */
import Link from "next/link"
import { cloud, type ScriptEntry, CloudConfigError } from "@/lib/cloud"
import { fmtUtc } from "@/lib/format"
import { getT } from "@/lib/i18n/server"
import {
  Card, EmptyState, ErrorState, PageHeader,
} from "@/components/ui"
import { DeleteScriptButton } from "./_components/DeleteScriptButton"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  if (e instanceof CloudConfigError) return "cloud not configured"
  return e instanceof Error ? e.message : String(e)
}

export default async function ScriptsPage() {
  const { t, locale } = await getT()

  let scripts: ScriptEntry[] = []
  let err: string | null = null
  try {
    const data = await cloud.listScripts()
    scripts = data.items
  } catch (e) {
    err = errMsg(e)
  }

  // Build a (script_id → policy ids) map by re-using the existing
  // /policies route. We tolerate the call failing (the page still
  // renders the empty-state and the bare metadata).
  const usedBy: Record<string, string[]> = {}
  try {
    const items = await cloud.listPolicies()
    for (const item of items) {
      const ref = (item as unknown as { script_path?: string }).script_path
      if (typeof ref === "string" && ref) {
        usedBy[ref] = usedBy[ref] || []
        usedBy[ref].push(item.id)
      }
    }
  } catch {
    // best effort
  }

  return (
    <>
      <PageHeader
        title={t("scripts.title")}
        description={t("scripts.subtitle")}
      />

      {err && (
        <ErrorState title={t("scripts.uploadFailed")} body={err} />
      )}

      {!err && scripts.length === 0 && (
        <EmptyState
          title={t("scripts.title")}
          body={t("scripts.empty")}
          action={
            <Link href="/policies/new" className="underline text-sm">
              {t("nav.newPolicy")}
            </Link>
          }
        />
      )}

      {!err && scripts.length > 0 && (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500 border-b">
                <th className="py-2 pr-3">{t("scripts.table.name")}</th>
                <th className="py-2 pr-3">{t("scripts.table.runtime")}</th>
                <th className="py-2 pr-3">{t("scripts.table.size")}</th>
                <th className="py-2 pr-3">{t("scripts.table.hash")}</th>
                <th className="py-2 pr-3">{t("scripts.table.usedBy")}</th>
                <th className="py-2 pr-3 text-right">
                  {t("scripts.table.actions")}
                </th>
              </tr>
            </thead>
            <tbody>
              {scripts.map((s) => {
                const refs = usedBy[s.id] ?? []
                return (
                  <tr key={s.id} className="border-b last:border-b-0">
                    <td className="py-2 pr-3 font-medium">{s.name}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{s.runtime}</td>
                    <td className="py-2 pr-3">{s.size_bytes} B</td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {s.hash.slice(0, 12)}…
                    </td>
                    <td className="py-2 pr-3 text-xs">
                      {refs.length === 0
                        ? "—"
                        : refs.slice(0, 3).join(", ") +
                          (refs.length > 3 ? ` (+${refs.length - 3})` : "")}
                    </td>
                    <td className="py-2 pr-3 text-right">
                      <DeleteScriptButton
                        id={s.id}
                        inUse={refs}
                        locale={locale}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Card>
      )}

      <p className="text-xs text-slate-500 mt-4">
        {fmtUtc(Math.floor(Date.now() / 1000))}
      </p>
    </>
  )
}
