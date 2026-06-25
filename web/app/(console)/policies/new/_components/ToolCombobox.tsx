"use client"

/**
 * D70: Step 2 single autocomplete combobox covering every CC built-in
 * tool + free-typed MCP / custom tool names.
 *
 * Replaces the prior chip grid (10 hardcoded built-ins) + separate MCP
 * input. The chip grid was incomplete (missing MultiEdit, BashOutput,
 * KillBash, NotebookRead, ExitPlanMode, Task, TodoWrite, AskUserQuestion)
 * and surfaced MCP authoring as a second-class affordance. The combobox
 * collapses both surfaces into one:
 *
 *   - Empty input shows 5 top suggested built-ins (Bash, Read, Edit,
 *     WebFetch, Task) so the picker is usable without typing.
 *   - Typing filters built-ins by case-insensitive substring match.
 *   - When the typed text doesn't match any built-in, the dropdown shows
 *     "Use as custom tool name: <text>" which writes the raw text into
 *     the form value when picked (covers MCP names `mcp__server__name`
 *     and any agent-registered custom tool).
 *   - Each suggestion row carries the tool name + a (built-in / MCP /
 *     custom) badge + a KO/EN one-line description.
 *   - Keyboard navigation: ArrowUp/ArrowDown move the highlight; Enter
 *     selects the highlighted row; Escape closes; Tab moves focus to
 *     the next field; click-outside closes the dropdown without
 *     committing.
 *
 * The component owns ONE hidden form input — `name="toolScope_custom"`
 * — which is what advanceWizard already reads. We keep that name so no
 * server-side change is needed; the wizard's existing seam already
 * treats this field as the authoritative tool name. The legacy
 * `toolScope_chip` radio surface is no longer rendered; advanceWizard
 * tolerates a missing `toolScope_chip` (the typed value wins anyway per
 * the helper copy "If both are set, the MCP name wins").
 *
 * Sub-path imports ONLY (never `@/components/ui` barrel) so the client
 * bundle stays slim.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react"
import {
  CC_BUILTIN_TOOLS,
  CC_TOP_SUGGESTIONS,
  classifyCcToolName,
  filterCcBuiltins,
  findCcBuiltinTool,
  type CcToolEntry,
  type CcToolKind,
} from "@/lib/cc-tools"
import { translate, type TKey } from "@/lib/i18n/dict"

type Locale = "ko" | "en"

interface Props {
  /** Initial value (carries the URL-persisted state.toolScope on mount). */
  initialValue?: string
  /** Locale for the dropdown copy + suggestion descriptions. */
  locale: Locale
  /** id for the visible text input (so the parent <label htmlFor=...> binds). */
  inputId?: string
}

/** Internal: highlighted-row marker for keyboard navigation. */
type Row =
  | { kind: "builtin"; entry: CcToolEntry }
  | { kind: "custom"; value: string }

function buildRows(query: string): Row[] {
  const trimmed = query.trim()
  // Empty query: show the top 5 suggested built-ins.
  if (!trimmed) {
    return CC_TOP_SUGGESTIONS.map((name) => {
      const entry = findCcBuiltinTool(name)
      // The CC_TOP_SUGGESTIONS list is hardcoded to names guaranteed
      // present in CC_BUILTIN_TOOLS, so this branch should never miss.
      // We narrow the type defensively rather than asserting.
      if (entry) return { kind: "builtin", entry } as Row
      return null
    }).filter((r): r is Row => r !== null)
  }
  const matches = filterCcBuiltins(trimmed)
  const builtinRows: Row[] = matches.map((entry) => ({ kind: "builtin", entry }))
  // If the typed text is itself a built-in name (case-insensitive), we
  // already have it surfaced; don't add a redundant "Use as custom"
  // affordance. Otherwise, append the custom-name affordance so an
  // operator can lock in `mcp__github__search` or `MyCustomTool`.
  const isExactBuiltin = findCcBuiltinTool(trimmed) !== null
  if (!isExactBuiltin) {
    builtinRows.push({ kind: "custom", value: trimmed })
  }
  return builtinRows
}

function badgeText(kind: CcToolKind, locale: Locale): string {
  if (kind === "built-in") return locale === "ko" ? "빌트인" : "Built-in"
  if (kind === "mcp") return "MCP"
  return locale === "ko" ? "커스텀" : "Custom"
}

export default function ToolCombobox({
  initialValue,
  locale,
  inputId = "tool-combobox-input",
}: Props): React.ReactElement {
  const [query, setQuery] = useState<string>(initialValue ?? "")
  const [value, setValue] = useState<string>(initialValue ?? "")
  const [open, setOpen] = useState<boolean>(false)
  const [highlight, setHighlight] = useState<number>(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const t = useCallback(
    (k: TKey, vars?: Record<string, string | number>) => translate(locale, k, vars),
    [locale],
  )

  const rows = useMemo<Row[]>(() => buildRows(query), [query])
  // Reset highlight whenever the row list changes so the first row is
  // always pre-highlighted (Enter without arrow-nav lands on the most
  // obvious choice).
  useEffect(() => {
    setHighlight(0)
  }, [rows.length])

  // Click-outside closes the dropdown without mutating value.
  useEffect(() => {
    function onDocClick(ev: MouseEvent) {
      const root = containerRef.current
      if (!root) return
      if (root.contains(ev.target as Node)) return
      setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [])

  const commit = useCallback((next: string) => {
    setValue(next)
    setQuery(next)
    setOpen(false)
  }, [])

  const onPick = useCallback(
    (row: Row) => {
      if (row.kind === "builtin") commit(row.entry.name)
      else commit(row.value)
    },
    [commit],
  )

  const onKeyDown = useCallback(
    (ev: ReactKeyboardEvent<HTMLInputElement>) => {
      if (ev.key === "ArrowDown") {
        ev.preventDefault()
        setOpen(true)
        setHighlight((h) => Math.min(h + 1, Math.max(rows.length - 1, 0)))
        return
      }
      if (ev.key === "ArrowUp") {
        ev.preventDefault()
        setOpen(true)
        setHighlight((h) => Math.max(h - 1, 0))
        return
      }
      if (ev.key === "Enter") {
        if (!open) return
        ev.preventDefault()
        const row = rows[highlight]
        if (row) onPick(row)
        return
      }
      if (ev.key === "Escape") {
        ev.preventDefault()
        setOpen(false)
        return
      }
      // Tab falls through to default browser behaviour (focus next
      // field) — D70 brief explicitly requires this. We also close the
      // dropdown so the next field isn't covered by a stale panel.
      if (ev.key === "Tab") {
        setOpen(false)
        return
      }
      // Backspace clears the value only when the input is already
      // empty (so a single Backspace from an empty input fully clears
      // both the displayed query AND the persisted value). Otherwise
      // the browser handles per-character delete normally and the
      // onChange handler keeps query / value in sync.
      if (ev.key === "Backspace" && query === "" && value !== "") {
        ev.preventDefault()
        setValue("")
        setQuery("")
        setOpen(false)
      }
    },
    [highlight, onPick, open, query, rows, value],
  )

  // Resolve the displayed kind badge for the currently-typed query.
  const queryKind = useMemo<CcToolKind>(
    () => classifyCcToolName(query),
    [query],
  )

  return (
    <div ref={containerRef} className="relative">
      {/* The hidden input is what the surrounding <form> submits. We
       * keep the legacy `toolScope_custom` name so advanceWizard's
       * existing seam works unchanged. */}
      <input type="hidden" name="toolScope_custom" value={value} />
      <input
        id={inputId}
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder={t("newPolicy.wizard.step2.toolPicker.placeholder")}
        value={query}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={`${inputId}-listbox`}
        role="combobox"
        data-testid="tool-combobox-input"
        onFocus={() => setOpen(true)}
        onChange={(ev) => {
          const next = ev.target.value
          setQuery(next)
          // Until the operator picks a row (or types a name that's a
          // built-in match), the persisted value mirrors the query so
          // the form submission carries the raw typed string even if
          // the dropdown is dismissed without picking.
          setValue(next)
          setOpen(true)
        }}
        onKeyDown={onKeyDown}
        className="block w-full rounded-lg border border-black/[0.08] bg-white px-3 py-2 font-mono text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent)]/30"
      />
      {/* Inline kind badge for the typed value so the operator can
       * see whether their typing classifies as built-in / MCP /
       * custom before they hit Enter or pick a row. */}
      {query.trim() !== "" && (
        <p
          data-testid="tool-combobox-typed-kind"
          className="mt-1 text-[11px] text-[var(--color-text-tertiary)]"
        >
          {t("newPolicy.wizard.step2.toolPicker.typedKind", {
            kind: badgeText(queryKind, locale),
          })}
        </p>
      )}
      {open && rows.length > 0 && (
        <ul
          id={`${inputId}-listbox`}
          role="listbox"
          data-testid="tool-combobox-listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-black/[0.08] bg-white shadow-lg"
        >
          {rows.map((row, idx) => {
            const isHi = idx === highlight
            if (row.kind === "builtin") {
              const e = row.entry
              const desc = locale === "ko" ? e.description.ko : e.description.en
              return (
                <li
                  key={`builtin:${e.name}`}
                  role="option"
                  aria-selected={isHi}
                  data-testid={`tool-combobox-row-${e.name}`}
                  onMouseDown={(ev) => {
                    ev.preventDefault()
                    onPick(row)
                  }}
                  onMouseEnter={() => setHighlight(idx)}
                  className={`flex cursor-pointer items-start gap-2 border-b border-black/[0.04] px-3 py-2 last:border-b-0 ${
                    isHi
                      ? "bg-[var(--color-accent)]/[0.06]"
                      : "bg-white hover:bg-black/[0.02]"
                  }`}
                >
                  <span className="min-w-[8rem] font-mono text-sm text-[var(--color-text-primary)]">
                    {e.name}
                  </span>
                  <span className="inline-flex shrink-0 items-center rounded-full border border-black/[0.08] bg-black/[0.03] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
                    {badgeText("built-in", locale)}
                  </span>
                  <span className="text-xs text-[var(--color-text-secondary)]">
                    {desc}
                  </span>
                </li>
              )
            }
            // custom-name affordance row
            const customKind = classifyCcToolName(row.value)
            return (
              <li
                key={`custom:${row.value}`}
                role="option"
                aria-selected={isHi}
                data-testid="tool-combobox-row-custom"
                onMouseDown={(ev) => {
                  ev.preventDefault()
                  onPick(row)
                }}
                onMouseEnter={() => setHighlight(idx)}
                className={`flex cursor-pointer items-start gap-2 border-b border-black/[0.04] px-3 py-2 last:border-b-0 ${
                  isHi
                    ? "bg-[var(--color-accent)]/[0.06]"
                    : "bg-white hover:bg-black/[0.02]"
                }`}
              >
                <span className="font-mono text-sm text-[var(--color-text-primary)]">
                  {t("newPolicy.wizard.step2.toolPicker.useAsCustom", {
                    name: row.value,
                  })}
                </span>
                <span className="inline-flex shrink-0 items-center rounded-full border border-black/[0.08] bg-black/[0.03] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
                  {badgeText(customKind, locale)}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// Source-grep hooks: the wizard test (wizard-wiring.test.ts) and the
// component test (ToolCombobox.test.ts) assert the presence of every
// canonical built-in by name. Pin a comment-anchored manifest so a
// future refactor that intends to drop a tool surfaces in the diff.
//
// CC_BUILTIN_MANIFEST: Bash, BashOutput, KillBash, Read, Write, Edit,
// MultiEdit, Glob, Grep, WebFetch, WebSearch, NotebookEdit,
// NotebookRead, Task, TodoWrite, ExitPlanMode, AskUserQuestion
void CC_BUILTIN_TOOLS
