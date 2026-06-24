"use client"

/**
 * D63 — per-row delete button on /scripts.
 *
 * The button is disabled when the row carries one or more referencing
 * policy ids; hovering shows the matching i18n hint so the operator
 * knows to detach the policy first. When unreferenced, clicking POSTs
 * /api/scripts?id=… and refreshes the page.
 *
 * Sub-path imports ONLY (avoid the @/components/ui barrel).
 */

import { useState } from "react"
import { useRouter } from "next/navigation"
import { translate, type Locale } from "@/lib/i18n/dict"

export function DeleteScriptButton({
  id,
  inUse,
  locale,
}: {
  id: string
  inUse: string[]
  locale: Locale
}) {
  const router = useRouter()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const disabled = inUse.length > 0

  const t = (k: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
    translate(locale, k, vars)

  async function onClick() {
    if (disabled) return
    setBusy(true)
    setErr(null)
    try {
      const r = await fetch(
        `/api/scripts?id=${encodeURIComponent(id)}`,
        { method: "DELETE" },
      )
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as { error?: string }
        setErr(body.error || t("scripts.deleteFailed"))
        return
      }
      router.refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (disabled) {
    return (
      <button
        type="button"
        className="text-xs text-slate-400 cursor-not-allowed"
        title={t("scripts.deleteInUse", { ids: inUse.join(", ") })}
        disabled
      >
        {t("scripts.delete")}
      </button>
    )
  }

  return (
    <div className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="text-xs text-red-600 underline disabled:opacity-50"
      >
        {t("scripts.delete")}
      </button>
      {err && <span className="text-xs text-red-600">{err}</span>}
    </div>
  )
}
