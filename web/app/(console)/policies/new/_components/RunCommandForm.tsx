"use client"

/**
 * D63 — Step 4b inline form for the "Run a command" action archetype.
 *
 * Mode toggle (radio):
 *   - "Type a command"    inline textarea + runtime select + args + timeout
 *                         + fail_closed checkbox.
 *   - "Attach a script"   file <input type=file> POSTed to /api/scripts;
 *                         on success the returned script id lives in form
 *                         state as `attachedScriptId`. Runtime defaults
 *                         from the file's shebang, override allowed.
 *
 * Inline command body is bounded at 4000 chars to mirror the cloud-side
 * RunCommandPolicy validator. Args take a comma-separated string (server
 * splits and trims). Timeout is a slider with a 100ms..30000ms range
 * (default 5000ms).
 *
 * Below the two modes: a dismissible warning callout. Dismissal lives
 * in localStorage under `magi_cp.run_command_warning_dismissed` per the
 * brief.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"): the barrel pulls
 * a server-only chain into the client bundle.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import type { Locale } from "@/lib/i18n/dict"
import { translate, type TKey } from "@/lib/i18n/dict"
import type { ScriptRuntime } from "@/lib/cloud"

export type RunCommandDraft = {
  mode: "inline" | "attach"
  command: string
  attachedScriptId: string
  attachedScriptName: string
  runtime: ScriptRuntime
  args: string                  // raw user input; the server splits on commas
  timeoutMs: number
  failClosed: boolean
}

export const DEFAULT_RUN_COMMAND_DRAFT: RunCommandDraft = {
  mode: "inline",
  command: "",
  attachedScriptId: "",
  attachedScriptName: "",
  runtime: "bash",
  args: "",
  timeoutMs: 5000,
  failClosed: false,
}

const MAX_INLINE_LEN = 4000
const MIN_TIMEOUT = 100
const MAX_TIMEOUT = 30_000
const WARNING_DISMISSED_KEY = "magi_cp.run_command_warning_dismissed"

function parseShebangRuntime(firstLine: string): ScriptRuntime | null {
  if (!firstLine.startsWith("#!")) return null
  if (firstLine.includes("python")) return "python3"
  if (firstLine.includes("node")) return "node"
  if (firstLine.includes("bash") || firstLine.includes("/sh")) return "bash"
  return null
}

export function RunCommandForm({
  locale,
  draft,
  onChange,
}: {
  locale: Locale
  draft: RunCommandDraft
  onChange: (next: RunCommandDraft) => void
}) {
  const t = useCallback(
    (k: TKey, vars?: Record<string, string | number>) => translate(locale, k, vars),
    [locale],
  )
  const [warningDismissed, setWarningDismissed] = useState<boolean>(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState<boolean>(false)

  useEffect(() => {
    try {
      const v = localStorage.getItem(WARNING_DISMISSED_KEY)
      setWarningDismissed(v === "1")
    } catch {
      // localStorage may be unavailable (SSR / private mode); ignore.
    }
  }, [])

  const dismissWarning = useCallback(() => {
    try {
      localStorage.setItem(WARNING_DISMISSED_KEY, "1")
    } catch {
      // ignore
    }
    setWarningDismissed(true)
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setUploadError(null)
    setUploading(true)
    try {
      // Sniff runtime from the shebang (first line).
      let detectedRuntime: ScriptRuntime | null = null
      try {
        const head = await file.slice(0, 200).text()
        const firstLine = head.split("\n")[0] || ""
        detectedRuntime = parseShebangRuntime(firstLine.trim())
      } catch {
        // ignore; user can override via the dropdown
      }
      const name = file.name.replace(/[^A-Za-z0-9._\-]/g, "-").slice(0, 64)
      const form = new FormData()
      form.append("file", file)
      form.append("name", name)
      form.append(
        "runtime",
        (detectedRuntime ?? draft.runtime) as ScriptRuntime,
      )
      const r = await fetch("/api/scripts", { method: "POST", body: form })
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as { error?: string }
        if (r.status === 403) {
          setUploadError(t("scripts.uploadDisabled"))
        } else {
          setUploadError(body.error || t("scripts.uploadFailed"))
        }
        return
      }
      const body = (await r.json()) as {
        id: string
        name: string
        runtime: ScriptRuntime
      }
      onChange({
        ...draft,
        attachedScriptId: body.id,
        attachedScriptName: body.name,
        runtime: detectedRuntime ?? body.runtime ?? draft.runtime,
      })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setUploadError(msg)
    } finally {
      setUploading(false)
    }
  }, [draft, onChange, t])

  const charsLeft = useMemo(
    () => Math.max(0, MAX_INLINE_LEN - draft.command.length),
    [draft.command],
  )

  return (
    <section
      data-testid="run-command-form"
      className="rounded-lg border border-slate-200 bg-white p-4 mt-4 space-y-4"
    >
      {/* Mode toggle */}
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-slate-700">
          {t("newPolicy.action.runCommand.title")}
        </legend>
        <div className="flex gap-4 text-sm">
          <label className="inline-flex items-center gap-2">
            <input
              type="radio"
              name="run_command_mode"
              value="inline"
              checked={draft.mode === "inline"}
              onChange={() => onChange({ ...draft, mode: "inline" })}
            />
            {t("newPolicy.step4.runCommand.modeInline")}
          </label>
          <label className="inline-flex items-center gap-2">
            <input
              type="radio"
              name="run_command_mode"
              value="attach"
              checked={draft.mode === "attach"}
              onChange={() => onChange({ ...draft, mode: "attach" })}
            />
            {t("newPolicy.step4.runCommand.modeAttach")}
          </label>
        </div>
      </fieldset>

      {/* Inline lane */}
      {draft.mode === "inline" && (
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="font-medium text-slate-700">
              {t("newPolicy.step4.runCommand.commandLabel")}
            </span>
            <textarea
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
              rows={4}
              maxLength={MAX_INLINE_LEN}
              placeholder={t("newPolicy.step4.runCommand.commandPlaceholder")}
              value={draft.command}
              onChange={(e) => onChange({ ...draft, command: e.target.value })}
            />
            <span className="text-xs text-slate-400">{charsLeft}</span>
          </label>
        </div>
      )}

      {/* Attach lane */}
      {draft.mode === "attach" && (
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="font-medium text-slate-700">
              {t("newPolicy.step4.runCommand.attachLabel")}
            </span>
            <input
              type="file"
              className="mt-1 block text-xs"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void handleFile(f)
              }}
              data-testid="run-command-file-input"
            />
            <span className="block text-xs text-slate-500 mt-1">
              {t("newPolicy.step4.runCommand.attachHint")}
            </span>
          </label>
          {uploading && (
            <p className="text-xs text-slate-500">Uploading…</p>
          )}
          {uploadError && (
            <p className="text-xs text-red-600" role="alert">
              {uploadError}
            </p>
          )}
          {draft.attachedScriptId && (
            <p className="text-xs text-emerald-700">
              {t("newPolicy.step4.runCommand.attachUploaded", {
                name: draft.attachedScriptName || draft.attachedScriptId,
              })}
            </p>
          )}
        </div>
      )}

      {/* Shared controls: runtime + args + timeout + fail_closed */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block text-sm">
          <span className="font-medium text-slate-700">
            {t("newPolicy.step4.runCommand.runtime")}
          </span>
          <select
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
            value={draft.runtime}
            onChange={(e) =>
              onChange({
                ...draft,
                runtime: e.target.value as ScriptRuntime,
              })
            }
          >
            <option value="bash">bash</option>
            <option value="python3">python3</option>
            <option value="node">node</option>
          </select>
          <span className="block text-xs text-slate-500 mt-1">
            {t("newPolicy.step4.runCommand.runtimeHint")}
          </span>
        </label>

        <label className="block text-sm">
          <span className="font-medium text-slate-700">
            {t("newPolicy.step4.runCommand.args")}
          </span>
          <input
            type="text"
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
            value={draft.args}
            onChange={(e) => onChange({ ...draft, args: e.target.value })}
            placeholder="--short, --json"
          />
          <span className="block text-xs text-slate-500 mt-1">
            {t("newPolicy.step4.runCommand.argsHint")}
          </span>
        </label>

        <label className="block text-sm sm:col-span-2">
          <span className="font-medium text-slate-700">
            {t("newPolicy.step4.runCommand.timeout")} ({(draft.timeoutMs / 1000).toFixed(1)}s)
          </span>
          <input
            type="range"
            className="mt-1 w-full"
            min={MIN_TIMEOUT}
            max={MAX_TIMEOUT}
            step={100}
            value={draft.timeoutMs}
            onChange={(e) =>
              onChange({ ...draft, timeoutMs: Number(e.target.value) })
            }
          />
          <span className="block text-xs text-slate-500 mt-1">
            {t("newPolicy.step4.runCommand.timeoutHint")}
          </span>
        </label>

        <label className="inline-flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            checked={draft.failClosed}
            onChange={(e) =>
              onChange({ ...draft, failClosed: e.target.checked })
            }
          />
          {t("newPolicy.step4.runCommand.failClosed")}
        </label>
      </div>

      {/* Warning callout */}
      {!warningDismissed && (
        <div
          className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 flex items-start justify-between gap-2"
          role="note"
        >
          <span>{t("newPolicy.step4.runCommand.warning")}</span>
          <button
            type="button"
            className="underline whitespace-nowrap"
            onClick={dismissWarning}
          >
            {t("newPolicy.step4.runCommand.warningDismiss")}
          </button>
        </div>
      )}
    </section>
  )
}

/**
 * D63 — wizard helper. Translate a {@link RunCommandDraft} into the IR
 * fragment the wizard's IR-draft pane / save mutation expects.
 *
 * Public on purpose: the page-level Step 6 review surface reads the
 * same draft and asks this module for the plain-language summary, so
 * both surfaces stay in lock-step.
 */
export function runCommandDraftToIr(draft: RunCommandDraft): {
  runtime: ScriptRuntime
  command: string
  script_path: string
  args: string[]
  timeout_ms: number
  fail_closed: boolean
} {
  const args = draft.args
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  return {
    runtime: draft.runtime,
    command: draft.mode === "inline" ? draft.command.trim() : "",
    script_path: draft.mode === "attach" ? draft.attachedScriptId : "",
    args,
    timeout_ms: draft.timeoutMs,
    fail_closed: draft.failClosed,
  }
}

/** Plain-language description for Step 6 review + IR draft pane. */
export function runCommandDraftSummary(
  locale: Locale,
  draft: RunCommandDraft,
): string[] {
  const t = (k: TKey, vars?: Record<string, string | number>) =>
    translate(locale, k, vars)
  const lines: string[] = []
  if (draft.mode === "inline") {
    const cmd = draft.command.trim() || "—"
    lines.push(t("newPolicy.review.runCommand.inline", { command: cmd }))
  } else {
    const args = draft.args.trim() || "—"
    const name = draft.attachedScriptName || draft.attachedScriptId || "—"
    lines.push(
      t("newPolicy.review.runCommand.attached", { name, args }),
    )
  }
  lines.push(t("newPolicy.review.runCommand.timeout", { ms: draft.timeoutMs }))
  lines.push(
    draft.failClosed
      ? t("newPolicy.review.runCommand.failClosed")
      : t("newPolicy.review.runCommand.failOpen"),
  )
  return lines
}
