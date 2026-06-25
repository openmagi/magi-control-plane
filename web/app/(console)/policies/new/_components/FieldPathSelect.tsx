"use client"

/**
 * D82c hotfix: custom-popup field-path picker for the Step 3 regex
 * "Field to match" selector.
 *
 * The native <select> popup is rendered by the OS (Chrome shows a
 * system-styled list with no theming), which broke visual parity with
 * the rest of the wizard. This component renders the same options as
 * a styled listbox popup (button + portal-less absolute panel) so the
 * dropdown matches the surrounding controls.
 *
 * Contract:
 *   - Mirrors the original native select's form contract: emits a
 *     hidden <input name=...> with the current value so saveWizard
 *     reads it from FormData unchanged.
 *   - Click outside or Escape closes the popup.
 *   - Arrow Up/Down navigate; Enter selects; Tab moves on.
 *   - PayloadFieldChips below the picker still set the value by
 *     dispatching a `regex-field-path-set` CustomEvent on
 *     document with detail.value; this component listens and applies.
 *
 * Sub-path imports only (NO @/components/ui barrel).
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"

export type FieldOption = {
  /** Raw path like `tool_input.command`. */
  path: string
  /** Operator-friendly label, e.g. "Bash command". */
  displayLabel?: string
  /** Field type hint, e.g. "str". */
  type?: string
}

type Props = {
  name: string
  id?: string
  initialValue: string
  options: FieldOption[]
  /** Optional aria label override. */
  ariaLabel?: string
  /** Test id passed to the visible button. */
  testId?: string
  /** Optional id of a CustomEvent the chips fire. Default is
   *  "regex-field-path-set". */
  chipEventName?: string
}

export function FieldPathSelect({
  name, id, initialValue, options, ariaLabel, testId,
  chipEventName = "regex-field-path-set",
}: Props) {
  const [value, setValue] = useState<string>(initialValue)
  const [open, setOpen] = useState<boolean>(false)
  const [highlight, setHighlight] = useState<number>(() => {
    const idx = options.findIndex((o) => o.path === initialValue)
    return idx >= 0 ? idx : 0
  })
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLUListElement | null>(null)
  const buttonRef = useRef<HTMLButtonElement | null>(null)
  const reactId = useId()
  const listboxId = id ? `${id}-listbox` : `field-path-${reactId}-listbox`

  // Listen for chip-click events that should set the value without
  // popping the listbox open.
  useEffect(() => {
    function onChip(e: Event) {
      const ce = e as CustomEvent<{ value?: string }>
      const next = (ce.detail?.value ?? "").trim()
      if (!next) return
      setValue(next)
      const idx = options.findIndex((o) => o.path === next)
      if (idx >= 0) setHighlight(idx)
    }
    document.addEventListener(chipEventName, onChip as EventListener)
    return () => {
      document.removeEventListener(chipEventName, onChip as EventListener)
    }
  }, [chipEventName, options])

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      const root = wrapRef.current
      if (root && !root.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [open])

  // Scroll the highlighted row into view when keyboard navigating.
  useEffect(() => {
    if (!open) return
    const list = listRef.current
    if (!list) return
    const row = list.querySelectorAll<HTMLElement>("[role='option']")[highlight]
    if (row) row.scrollIntoView({ block: "nearest" })
  }, [open, highlight])

  const selected = useMemo(
    () => options.find((o) => o.path === value) ?? null,
    [options, value],
  )

  const commit = useCallback(
    (next: string) => {
      setValue(next)
      const idx = options.findIndex((o) => o.path === next)
      if (idx >= 0) setHighlight(idx)
      setOpen(false)
      buttonRef.current?.focus()
    },
    [options],
  )

  function onButtonKey(e: React.KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      setOpen(true)
    }
  }

  function onListKey(e: React.KeyboardEvent<HTMLUListElement>) {
    if (e.key === "Escape") {
      e.preventDefault()
      setOpen(false)
      buttonRef.current?.focus()
      return
    }
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, options.length - 1))
      return
    }
    if (e.key === "ArrowUp") {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
      return
    }
    if (e.key === "Enter") {
      e.preventDefault()
      const opt = options[highlight]
      if (opt) commit(opt.path)
      return
    }
    if (e.key === "Tab") {
      setOpen(false)
    }
  }

  const buttonLabel = selected
    ? selected.displayLabel
      ? `${selected.displayLabel} (${selected.path})`
      : selected.path
    : value || ""

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        id={id}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label={ariaLabel}
        data-testid={testId}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onButtonKey}
        className={
          "h-10 w-full px-3 text-sm text-left rounded-md " +
          "bg-[var(--color-surface-input)] " +
          "border border-[var(--color-border-strong)] " +
          "text-[var(--color-text-primary)] " +
          "hover:bg-black/[0.02] " +
          "focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40 " +
          "flex items-center justify-between gap-2"
        }
      >
        <span className="truncate font-mono">{buttonLabel}</span>
        <span aria-hidden className="text-[var(--color-text-tertiary)]">▾</span>
      </button>
      {/* Form contract: emit the value as a hidden input so saveWizard
       *  reads `regexFieldPath` from FormData unchanged. */}
      <input type="hidden" name={name} value={value} />
      {open && (
        <ul
          ref={listRef}
          id={listboxId}
          role="listbox"
          tabIndex={-1}
          aria-label={ariaLabel}
          onKeyDown={onListKey}
          autoFocus
          className={
            "absolute z-20 mt-1 max-h-72 w-full overflow-y-auto " +
            "rounded-lg border border-black/[0.08] bg-white shadow-lg " +
            "py-1"
          }
        >
          {options.map((opt, i) => {
            const isSelected = opt.path === value
            const isHighlighted = i === highlight
            return (
              <li
                key={opt.path}
                role="option"
                aria-selected={isSelected}
                onMouseDown={(e) => { e.preventDefault(); commit(opt.path) }}
                onMouseEnter={() => setHighlight(i)}
                className={
                  "px-3 py-2 cursor-pointer flex items-center justify-between gap-3 " +
                  (isHighlighted
                    ? "bg-[var(--color-accent)]/[0.10] "
                    : "")
                }
              >
                <span className="flex flex-col">
                  <span className="text-sm">
                    {opt.displayLabel ?? opt.path}
                  </span>
                  {opt.displayLabel && (
                    <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
                      {opt.path}
                    </span>
                  )}
                </span>
                {opt.type && (
                  <span className="text-[11px] font-mono text-[var(--color-text-tertiary)] shrink-0">
                    :{opt.type}
                  </span>
                )}
              </li>
            )
          })}
          {options.length === 0 && (
            <li
              role="option"
              aria-selected={false}
              className="px-3 py-2 text-xs italic text-[var(--color-text-tertiary)]"
            >
              (no fields available)
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
