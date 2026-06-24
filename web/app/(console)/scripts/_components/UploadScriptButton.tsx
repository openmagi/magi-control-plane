"use client"

/**
 * D63 review (P1): /scripts page Upload button. Operators can pick a
 * script body, type a friendly name, choose a runtime, and POST it
 * through the existing /api/scripts multipart proxy. On success we
 * `router.refresh()` so the table picks up the new row server-side.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"): the barrel
 * pulls a server-only chain into the client bundle.
 */

import { useCallback, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import type { Locale } from "@/lib/i18n/dict"
import { translate, type TKey } from "@/lib/i18n/dict"

type Runtime = "bash" | "python3" | "node"

function parseShebangRuntime(firstLine: string): Runtime | null {
  if (!firstLine.startsWith("#!")) return null
  if (firstLine.includes("python")) return "python3"
  if (firstLine.includes("node")) return "node"
  if (firstLine.includes("bash") || firstLine.includes("/sh")) return "bash"
  return null
}

export function UploadScriptButton({ locale }: { locale: Locale }) {
  const router = useRouter()
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [okMsg, setOkMsg] = useState<string | null>(null)
  const t = (k: TKey, vars?: Record<string, string | number>) => translate(locale, k, vars)
  const ko = locale === "ko"

  const onPick = useCallback(async (file: File) => {
    setBusy(true)
    setError(null)
    setOkMsg(null)
    try {
      let detected: Runtime | null = null
      try {
        const head = await file.slice(0, 200).text()
        const firstLine = head.split("\n")[0] || ""
        detected = parseShebangRuntime(firstLine.trim())
      } catch { /* ignore */ }
      const name = file.name.replace(/[^A-Za-z0-9._\-]/g, "-").slice(0, 64)
      const form = new FormData()
      form.append("file", file)
      form.append("name", name)
      form.append("runtime", (detected ?? "bash") as Runtime)
      const r = await fetch("/api/scripts", { method: "POST", body: form })
      if (!r.ok) {
        const json = (await r.json().catch(() => ({}))) as { error?: string }
        if (r.status === 403) {
          setError(t("scripts.uploadDisabled"))
        } else {
          setError(json.error || t("scripts.uploadFailed"))
        }
        return
      }
      const json = (await r.json()) as { id: string; name: string }
      setOkMsg(
        ko
          ? `업로드 완료: ${json.name} (${json.id.slice(0, 12)}…)`
          : `Uploaded: ${json.name} (${json.id.slice(0, 12)}…)`,
      )
      router.refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }, [router, t, ko])

  return (
    <div className="flex items-start gap-3" data-testid="upload-script-button">
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
      >
        {busy
          ? (ko ? "업로드 중…" : "Uploading…")
          : (ko ? "스크립트 업로드" : "Upload script")}
      </button>
      <input
        type="file"
        ref={fileRef}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) void onPick(f)
        }}
      />
      {error && (
        <p className="text-xs text-red-600 mt-1" role="alert">{error}</p>
      )}
      {okMsg && (
        <p className="text-xs text-emerald-700 mt-1" role="status">{okMsg}</p>
      )}
    </div>
  )
}

export default UploadScriptButton
