"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useRouter } from "next/navigation"

export type Command = {
  id: string
  label: string
  /** Short right-aligned context (group name, e.g. "Go to" / "Action"). */
  hint: string
  href: string
  /** Extra match terms not shown but searchable. */
  keywords?: string
}

/** Custom event any client component can dispatch to open the palette
 * (e.g. a visible "Search" trigger in the header), so the trigger and the
 * palette do not need to share React state. */
export const OPEN_COMMAND_PALETTE_EVENT = "magi:open-command-palette"

function matches(cmd: Command, q: string): boolean {
  if (!q) return true
  const hay = `${cmd.label} ${cmd.hint} ${cmd.keywords ?? ""}`.toLowerCase()
  // Subsequence match so "opol" finds "Open · Policies", plus plain substring.
  const needle = q.toLowerCase()
  if (hay.includes(needle)) return true
  let i = 0
  for (const ch of hay) {
    if (ch === needle[i]) i++
    if (i === needle.length) return true
  }
  return false
}

export function CommandPalette({
  commands,
  placeholder,
  emptyLabel,
}: {
  commands: Command[]
  placeholder: string
  emptyLabel: string
}) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  const results = useMemo(
    () => commands.filter((c) => matches(c, query)),
    [commands, query],
  )

  const close = useCallback(() => {
    setOpen(false)
    setQuery("")
    setActive(0)
    // Return focus to whatever was focused before opening.
    restoreFocusRef.current?.focus?.()
  }, [])

  const openPalette = useCallback(() => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null
    setOpen(true)
  }, [])

  // Global open shortcut (⌘K / Ctrl+K) + external open event.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault()
        setOpen((v) => {
          if (!v) restoreFocusRef.current = document.activeElement as HTMLElement | null
          return !v
        })
      }
    }
    function onOpenEvent() { openPalette() }
    window.addEventListener("keydown", onKey)
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, onOpenEvent)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, onOpenEvent)
    }
  }, [openPalette])

  // On open: lock scroll, focus the input.
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    inputRef.current?.focus()
    return () => { document.body.style.overflow = prev }
  }, [open])

  // Keep the active row clamped and scrolled into view.
  useEffect(() => {
    if (active >= results.length) setActive(Math.max(0, results.length - 1))
  }, [results.length, active])

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)
    el?.scrollIntoView({ block: "nearest" })
  }, [active])

  const run = useCallback((cmd: Command | undefined) => {
    if (!cmd) return
    close()
    router.push(cmd.href)
  }, [close, router])

  function onInputKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); close(); return }
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive((i) => (results.length ? (i + 1) % results.length : 0))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0))
    } else if (e.key === "Enter") {
      e.preventDefault()
      run(results[active])
    }
  }

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 p-4 pt-[12vh] backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) close() }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={placeholder}
        className="w-full max-w-xl overflow-hidden rounded-xl border border-[var(--color-border-strong)] bg-[var(--color-surface-raised)] shadow-[0_24px_64px_-16px_rgba(15,23,42,0.45)]"
      >
        <div className="flex items-center gap-2.5 border-b border-[var(--color-border-subtle)] px-4">
          <svg
            aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round"
            className="shrink-0 text-[var(--color-text-tertiary)]"
          >
            <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
          </svg>
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-activedescendant={results[active] ? `cmd-${results[active].id}` : undefined}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0) }}
            onKeyDown={onInputKey}
            placeholder={placeholder}
            className="w-full bg-transparent py-3.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none"
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <div
          ref={listRef}
          id="command-palette-list"
          role="listbox"
          className="max-h-[52vh] overflow-y-auto p-1.5"
        >
          {results.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-[var(--color-text-tertiary)]">
              {emptyLabel}
            </div>
          ) : (
            results.map((cmd, idx) => (
              <button
                key={cmd.id}
                id={`cmd-${cmd.id}`}
                data-idx={idx}
                role="option"
                aria-selected={idx === active}
                type="button"
                onMouseMove={() => setActive(idx)}
                onClick={() => run(cmd)}
                className={
                  "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm " +
                  (idx === active
                    ? "bg-[var(--color-surface-overlay)] text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)]")
                }
              >
                <span className="truncate">{cmd.label}</span>
                <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  {cmd.hint}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default CommandPalette
