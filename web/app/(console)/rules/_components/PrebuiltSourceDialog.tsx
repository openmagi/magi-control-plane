"use client"

import { useCallback, useEffect, useId, useRef } from "react"
import type { PrebuiltPolicyEntry } from "@/lib/cloud"
import { CodeBlock } from "@/components/ui/Code"
import { CopyButton } from "@/components/ui/CopyButton"
import { translate, type Locale, type TKey } from "@/lib/i18n/dict"

type TFunc = (
  k: TKey,
  v?: Record<string, string | number>,
) => string

/**
 * Q94: PrebuiltSourceDialog.
 *
 * Opens a native <dialog> via showModal() so the browser handles focus
 * trap, Escape-to-close, and aria-modal semantics for free. The body
 * renders the prebuilt's underlying Policy IR JSON inside the design
 * system <CodeBlock> primitive, with the canonical <CopyButton> on
 * top-right for copy-to-clipboard.
 *
 * Behaviour:
 *   - `open` is a controlled prop. Toggling true calls dialog.showModal();
 *     toggling false calls dialog.close().
 *   - Backdrop click (target === dialog element) closes.
 *   - The native `close` event fires onClose so parent state stays in
 *     sync regardless of how the dialog closed (Escape, backdrop click,
 *     close button, programmatic close).
 *   - On close we focus() the trigger element via triggerRef so keyboard
 *     users land back on the View source button instead of the document
 *     body. (Native dialog already restores focus to whatever the
 *     activeElement was before showModal(), but we keep the explicit
 *     restore so a refocus during render doesn't strand the user.)
 */
export function PrebuiltSourceDialog({
  entry,
  open,
  onClose,
  locale,
  triggerRef,
}: {
  entry: PrebuiltPolicyEntry
  open: boolean
  onClose: () => void
  locale: Locale
  triggerRef?: React.RefObject<HTMLButtonElement | null>
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const t: TFunc = useCallback(
    (key, vars) => translate(locale, key, vars),
    [locale],
  )

  // Drive the native <dialog> imperatively from the `open` prop. Using
  // showModal() (not show()) gives us focus trap + Escape + backdrop
  // pseudo-element for free.
  useEffect(() => {
    const dlg = dialogRef.current
    if (!dlg) return
    if (open && !dlg.open) {
      dlg.showModal()
    } else if (!open && dlg.open) {
      dlg.close()
    }
  }, [open])

  // Notify parent on any close path (Escape, backdrop, close button,
  // programmatic). Also restore focus to the trigger button.
  useEffect(() => {
    const dlg = dialogRef.current
    if (!dlg) return
    const handleClose = () => {
      onClose()
      const trigger = triggerRef?.current
      if (trigger && typeof trigger.focus === "function") {
        trigger.focus()
      }
    }
    dlg.addEventListener("close", handleClose)
    return () => dlg.removeEventListener("close", handleClose)
  }, [onClose, triggerRef])

  // Backdrop click closes when the click target is the dialog element
  // itself (i.e. the user clicked the ::backdrop, not the content).
  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDialogElement>) => {
      if (e.target === dialogRef.current) {
        dialogRef.current?.close()
      }
    },
    [],
  )

  const json = JSON.stringify(entry.ir, null, 2)
  const title = t("rules.prebuilt.viewSourceTitle", { title: entry.title })

  return (
    <dialog
      ref={dialogRef}
      onClick={handleClick}
      aria-labelledby={titleId}
      className="rounded-xl p-0 w-[90vw] max-w-2xl border border-[var(--color-border-subtle)] bg-[var(--color-surface-base)] text-[var(--color-text-primary)] backdrop:bg-black/40 backdrop:backdrop-blur-sm"
    >
      <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border-subtle)] px-4 py-3">
        <h2
          id={titleId}
          className="text-sm font-semibold text-[var(--color-text-primary)] truncate"
        >
          {title}
        </h2>
        <button
          type="button"
          onClick={() => dialogRef.current?.close()}
          aria-label={t("rules.prebuilt.viewSourceCloseAria", {
            title: entry.title,
          })}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)]"
        >
          <span aria-hidden>×</span>
        </button>
      </div>
      <div className="flex flex-col gap-2 p-4">
        <div className="flex justify-end">
          <CopyButton
            value={json}
            size="sm"
            label={t("common.copy")}
            copiedLabel={t("common.copied")}
          />
        </div>
        <CodeBlock>{json}</CodeBlock>
      </div>
    </dialog>
  )
}
