"use client"

/**
 * D63 review (P1 ux-bug + missing-feature): Step 4b run_command
 * inline-vs-attach fields. The original Step 4b rendered both groups
 * unconditionally — whichever didn't match the mode <select> was
 * silently dropped server-side, training operators to fill both.
 *
 * This client island:
 *   - Reads the mode <select> value and conditionally renders only
 *     the matching group, so the unused input never lands in the
 *     FormData.
 *   - Surfaces a real <input type="file"> wired to /api/scripts in
 *     the attach lane (the wizard previously made operators hand-
 *     paste a sha256). Shebang sniff (#!/usr/bin/env python3 etc.)
 *     auto-fills the runtime; the runtime <select> override still
 *     wins.
 *   - Adds a "Browse scripts" link next to the script-id field so a
 *     first-time operator can find the /scripts page where uploads
 *     also live.
 *   - Uses a dedicated commandHint i18n key (newPolicy.step4.runCommand.commandHint)
 *     under the inline textarea, instead of the misleading attachHint
 *     which referenced "Up to 64KB. Re-uploading the same name…".
 *
 * Wizard server flow stays intact: this island still emits exactly
 * the same field names (`runCommandMode`, `runCommandRuntime`,
 * `runCommandBody`, `runCommandScriptId`, etc.) the inline page.tsx
 * read previously, so saveWizard's branch is unchanged.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"): the barrel
 * pulls a server-only chain into the client bundle.
 */

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { Locale } from "@/lib/i18n/dict"
import { translate, type TKey } from "@/lib/i18n/dict"

type Runtime = "bash" | "python3" | "node"
type Mode = "inline" | "attach"

export interface Step4bRunCommandFieldsProps {
  locale: Locale
  defaultMode?: Mode
  defaultRuntime?: Runtime
  defaultBody?: string
  defaultScriptId?: string
  defaultScriptName?: string
  defaultArgs?: string
  defaultTimeoutMs?: number
  defaultFailClosed?: boolean
  inputClassName: string
  fieldLabelClassName: string
  /**
   * D68 follow-up (P2 ux-clarity): when the Step 4 advance gate
   * refuses because BOTH `runCommandBody` AND `runCommandScriptId`
   * are empty, the parent passes hasError=true so this island
   * applies the red ring on the actual command-body textarea and
   * the script-id input (matching the affordance inject_context and
   * input_rewrite show). Previously the operator saw the banner at
   * the top of the sub-form but the empty field itself looked
   * unmarked.
   */
  hasError?: boolean
  /**
   * D68 follow-up (P2 ux-clarity): tailwind class applied to the
   * empty input(s) when hasError is set. Driven from the parent so
   * the styling stays consistent with the rewriter / inject ring.
   */
  errorRingClassName?: string
}

const MAX_INLINE_LEN = 4000
const MIN_TIMEOUT = 100
const MAX_TIMEOUT = 30_000
const WARNING_DISMISSED_KEY = "magi_cp.run_command_warning_dismissed"

function parseShebangRuntime(firstLine: string): Runtime | null {
  if (!firstLine.startsWith("#!")) return null
  if (firstLine.includes("python")) return "python3"
  if (firstLine.includes("node")) return "node"
  if (firstLine.includes("bash") || firstLine.includes("/sh")) return "bash"
  return null
}

export function Step4bRunCommandFields(props: Step4bRunCommandFieldsProps) {
  const {
    locale,
    defaultMode = "inline",
    defaultRuntime = "bash",
    defaultBody = "",
    defaultScriptId = "",
    defaultScriptName = "",
    defaultArgs = "",
    defaultTimeoutMs = 5000,
    defaultFailClosed = false,
    inputClassName,
    fieldLabelClassName,
    hasError = false,
    errorRingClassName = "",
  } = props
  const t = useCallback(
    (k: TKey, vars?: Record<string, string | number>) => translate(locale, k, vars),
    [locale],
  )
  const ko = locale === "ko"

  const [mode, setMode] = useState<Mode>(defaultMode)
  const [runtime, setRuntime] = useState<Runtime>(defaultRuntime)
  const [body, setBody] = useState<string>(defaultBody)
  const [scriptId, setScriptId] = useState<string>(defaultScriptId)
  const [scriptName, setScriptName] = useState<string>(defaultScriptName)
  const [args, setArgs] = useState<string>(defaultArgs)
  const [timeoutMs, setTimeoutMs] = useState<number>(defaultTimeoutMs)
  const [failClosed, setFailClosed] = useState<boolean>(defaultFailClosed)
  const [uploading, setUploading] = useState<boolean>(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [warningDismissed, setWarningDismissed] = useState<boolean>(false)

  useEffect(() => {
    try {
      const v = localStorage.getItem(WARNING_DISMISSED_KEY)
      setWarningDismissed(v === "1")
    } catch { /* SSR / private mode */ }
  }, [])

  const dismissWarning = useCallback(() => {
    try { localStorage.setItem(WARNING_DISMISSED_KEY, "1") } catch { /* ignore */ }
    setWarningDismissed(true)
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setUploadError(null)
    setUploading(true)
    try {
      let detected: Runtime | null = null
      try {
        const head = await file.slice(0, 200).text()
        const firstLine = head.split("\n")[0] || ""
        detected = parseShebangRuntime(firstLine.trim())
      } catch { /* ignore */ }
      const cleanName = file.name.replace(/[^A-Za-z0-9._\-]/g, "-").slice(0, 64)
      const form = new FormData()
      form.append("file", file)
      form.append("name", cleanName)
      form.append("runtime", (detected ?? runtime) as Runtime)
      const r = await fetch("/api/scripts", { method: "POST", body: form })
      if (!r.ok) {
        const json = (await r.json().catch(() => ({}))) as { error?: string }
        if (r.status === 403) {
          setUploadError(t("scripts.uploadDisabled"))
        } else {
          setUploadError(json.error || t("scripts.uploadFailed"))
        }
        return
      }
      const json = (await r.json()) as {
        id: string; name: string; runtime: Runtime
      }
      setScriptId(json.id)
      setScriptName(json.name)
      if (detected) setRuntime(detected)
      else if (json.runtime) setRuntime(json.runtime)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }, [runtime, t])

  const charsLeft = useMemo(
    () => Math.max(0, MAX_INLINE_LEN - body.length),
    [body],
  )

  // Step 6 plain summary + IR draft pane read these names; the server
  // action `saveWizard` keys off the same names. Keep them stable.
  return (
    <div className="space-y-3" data-testid="step4b-run-command-fields">
      <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed m-0">
        {t("newPolicy.step4.runCommand.warning")}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <span className={fieldLabelClassName}>
            {t("newPolicy.step4.runCommand.modeInline")}
          </span>
          <select
            name="runCommandMode"
            value={mode}
            onChange={(e) => setMode(e.target.value as Mode)}
            className={inputClassName}
            data-testid="step4b-rc-mode"
          >
            <option value="inline">{t("newPolicy.step4.runCommand.modeInline")}</option>
            <option value="attach">{t("newPolicy.step4.runCommand.modeAttach")}</option>
          </select>
        </div>
        <div>
          <span className={fieldLabelClassName}>
            {t("newPolicy.step4.runCommand.runtime")}
          </span>
          <select
            name="runCommandRuntime"
            value={runtime}
            onChange={(e) => setRuntime(e.target.value as Runtime)}
            className={inputClassName}
          >
            <option value="bash">bash</option>
            <option value="python3">python3</option>
            <option value="node">node</option>
          </select>
        </div>
      </div>

      {mode === "inline" && (
        <div data-testid="step4b-rc-inline">
          <span className={fieldLabelClassName}>
            {t("newPolicy.step4.runCommand.commandLabel")}
          </span>
          <textarea
            name="runCommandBody"
            rows={4}
            maxLength={MAX_INLINE_LEN}
            placeholder={t("newPolicy.step4.runCommand.commandPlaceholder")}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            // D68 follow-up (P2 ux-clarity): light the red ring on the
            // empty command body when the Step 4 advance gate refused.
            // hasError already implies the parent saw BOTH lanes empty;
            // we still check body.trim() so the operator who types a
            // command after the redirect sees the ring clear in-place.
            className={
              inputClassName + " font-mono"
              + (hasError && !body.trim() ? " " + errorRingClassName : "")
            }
            data-testid="step4b-rc-inline-body"
          />
          <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0 flex items-center justify-between gap-2">
            <span>{t("newPolicy.step4.runCommand.commandHint")}</span>
            <span className="font-mono tabular-nums" aria-label="chars left">
              {charsLeft}
            </span>
          </p>
        </div>
      )}

      {mode === "attach" && (
        <div data-testid="step4b-rc-attach" className="space-y-3">
          <div>
            <span className={fieldLabelClassName}>
              {t("newPolicy.step4.runCommand.attachLabel")}
            </span>
            <input
              type="file"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void handleFile(f)
              }}
              className="mt-1 block text-xs"
              data-testid="step4b-rc-file-input"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0">
              {t("newPolicy.step4.runCommand.attachHint")}
            </p>
            {uploading && (
              <p className="mt-1 text-xs text-slate-500" role="status">
                {ko ? "업로드 중…" : "Uploading…"}
              </p>
            )}
            {uploadError && (
              <p className="mt-1 text-xs text-red-600" role="alert">
                {uploadError}
              </p>
            )}
            {scriptId && (
              <p className="mt-1 text-xs text-emerald-700">
                {t("newPolicy.step4.runCommand.attachUploaded", {
                  name: scriptName || scriptId,
                })}
              </p>
            )}
          </div>
          <div>
            <span className={fieldLabelClassName}>
              {ko ? "또는 업로드된 스크립트 id 직접 입력" : "Or paste an uploaded script id"}
            </span>
            <input
              type="text"
              name="runCommandScriptId"
              value={scriptId}
              onChange={(e) => setScriptId(e.target.value.trim())}
              maxLength={64}
              pattern="[A-Fa-f0-9]{64}"
              placeholder="sha256 script id (64 hex chars)"
              // D68 follow-up (P2 ux-clarity): light the red ring on the
              // empty script-id input when the Step 4 advance gate
              // refused. Mirrors the inline-mode body treatment so the
              // attach-mode operator gets the same affordance.
              className={
                inputClassName + " font-mono"
                + (hasError && !scriptId.trim() ? " " + errorRingClassName : "")
              }
              data-testid="step4b-rc-attach-script-id"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0">
              <Link
                href="/scripts"
                className="underline text-[var(--color-accent)]"
                target="_blank"
                rel="noreferrer"
              >
                {ko ? "스크립트 목록 열기 →" : "Browse uploaded scripts →"}
              </Link>
            </p>
            {/* Preserve the displayed name across the form post so
             *  Step 6 summary can show "Will run script <name>". */}
            {scriptName && (
              <input
                type="hidden"
                name="runCommandScriptName"
                value={scriptName}
              />
            )}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <span className={fieldLabelClassName}>
            {t("newPolicy.step4.runCommand.args")}
          </span>
          <input
            type="text"
            name="runCommandArgs"
            value={args}
            onChange={(e) => setArgs(e.target.value)}
            maxLength={4_000}
            placeholder="--short, --json"
            className={inputClassName + " font-mono"}
          />
          <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0">
            {t("newPolicy.step4.runCommand.argsHint")}
          </p>
        </div>
        <div>
          <span className={fieldLabelClassName}>
            {t("newPolicy.step4.runCommand.timeout")}
          </span>
          <input
            type="number"
            name="runCommandTimeoutMs"
            value={timeoutMs}
            min={MIN_TIMEOUT}
            max={MAX_TIMEOUT}
            step={100}
            onChange={(e) => {
              const v = Number.parseInt(e.target.value, 10)
              if (Number.isFinite(v)) setTimeoutMs(v)
            }}
            className={inputClassName + " font-mono"}
          />
          <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)] m-0">
            {t("newPolicy.step4.runCommand.timeoutHint")}
          </p>
        </div>
      </div>

      <label className="flex items-start gap-2 text-xs text-[var(--color-text-secondary)]">
        <input
          type="checkbox"
          name="runCommandFailClosed"
          value="true"
          checked={failClosed}
          onChange={(e) => setFailClosed(e.target.checked)}
          className="mt-0.5"
        />
        <span>{t("newPolicy.step4.runCommand.failClosed")}</span>
      </label>

      {!warningDismissed && (
        <div
          role="note"
          className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 flex items-start justify-between gap-2"
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
    </div>
  )
}

export default Step4bRunCommandFields
