"use client"

/**
 * D70 / D71: Step 2 single autocomplete combobox covering every CC
 * built-in tool + free-typed MCP / custom tool names.
 *
 * Replaces the prior chip grid (10 hardcoded built-ins, mostly stale
 * names) + separate MCP input. The legacy surface was incomplete and
 * stale; the v2.1.170 binary actually registers the following
 * canonicals which the chip grid silently omitted: Agent (was Task),
 * Skill, ToolSearch, StructuredOutput, ListMcpResourcesTool,
 * ReadMcpResourceTool, CronCreate, CronDelete, CronList, DesignSync,
 * Monitor, EnterWorktree, ExitWorktree, PushNotification,
 * RemoteTrigger, TaskCreate, TaskGet, TaskList, TaskUpdate, TaskStop,
 * TaskOutput, SendUserMessage, SendUserFile, ScheduleWakeup, REPL,
 * LSP, Workflow, PowerShell, TeamCreate, TeamDelete, SendMessage,
 * ListAgents, ShareOnboardingGuide, Cd. See cc-tools.ts for the
 * source-verified canonical list.
 *
 * Empty input shows 5 top suggested built-ins (Bash, Read, Edit,
 * WebFetch, Agent — replacing the stale 'Task' default). Typing
 * filters built-ins by case-insensitive substring; an unmatched
 * typed value shows a "Use as custom tool name" affordance.
 *
 * A11y (WAI ARIA 1.2 combobox-with-listbox-popup):
 *   - role="combobox" on the input + aria-autocomplete="list".
 *   - aria-expanded reflects open state.
 *   - aria-controls points at the listbox ONLY when it's mounted.
 *   - aria-activedescendant tracks the highlighted option id (each
 *     option has a stable DOM id) so AT announces ArrowUp/Down moves.
 *   - aria-selected on options reflects the COMMITTED value, not the
 *     keyboard cursor; the cursor is conveyed via aria-activedescendant.
 *   - The component does NOT render its own visible label; the parent
 *     wires the label/`htmlFor` via the `inputId` + `ariaLabelledBy`
 *     props.
 *   - Keyboard: ArrowDown / ArrowUp move the highlight (opening the
 *     dropdown on the first press from a closed state without
 *     skipping the first option); Home / End / PageUp / PageDown move
 *     by larger steps; Enter commits; Escape reverts in-flight query
 *     to the last-committed value when open, or clears when already
 *     closed; Tab closes the popup and falls through to default focus
 *     behaviour.
 *   - Click-outside / touch-outside / focus-leave all close the popup.
 *
 * Form contract: the component owns ONE hidden form input named
 * `toolScope_custom` which is what advanceWizard reads. The value
 * written to that input is always the post-rename CANONICAL name
 * when the typed string resolves to a built-in (case-normalised so
 * downstream `TOOL_SPECIFIC_BY_NAME["Bash"]` lookups hit). Free-typed
 * MCP / custom names land verbatim per the brief's custom-fallback
 * contract.
 *
 * Legacy-alias hint: when the user types one of the pre-rename
 * canonicals (Task / BashOutput / KillBash / etc), an inline hint
 * points to the post-rename canonical so authors who carried over
 * old presets get nudged onto the new name.
 *
 * Sub-path imports ONLY (never `@/components/ui` barrel) so the
 * client bundle stays slim.
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
  legacyAliasCanonical,
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
  /** id of an external <label> element if the parent uses aria-labelledby. */
  ariaLabelledBy?: string
}

/** Internal: highlighted-row marker for keyboard navigation. */
type Row =
  | { kind: "builtin"; entry: CcToolEntry }
  | { kind: "custom"; value: string }

function buildRows(query: string): Row[] {
  const trimmed = query.trim()
  // Empty query: show the FULL built-in list, with the top 5
  // suggestions surfaced first so the most-common picks stay at the
  // top of the scroll. The dropdown container already has max-h-72 +
  // overflow-y-auto so the remaining ~12 entries are reachable by
  // scrolling. The earlier 5-only slice hid Task / MultiEdit /
  // NotebookRead / BashOutput / KillBash / ExitPlanMode / AskUser from
  // first-time visitors.
  if (!trimmed) {
    const seen = new Set<string>()
    const ordered: Row[] = []
    for (const name of CC_TOP_SUGGESTIONS) {
      const entry = findCcBuiltinTool(name)
      if (entry && !seen.has(entry.name)) {
        ordered.push({ kind: "builtin", entry })
        seen.add(entry.name)
      }
    }
    for (const entry of filterCcBuiltins("")) {
      if (!seen.has(entry.name)) {
        ordered.push({ kind: "builtin", entry })
        seen.add(entry.name)
      }
    }
    return ordered
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

function badgeKey(kind: CcToolKind): TKey {
  if (kind === "built-in") return "newPolicy.wizard.step2.toolPicker.badge.builtin"
  if (kind === "mcp") return "newPolicy.wizard.step2.toolPicker.badge.mcp"
  return "newPolicy.wizard.step2.toolPicker.badge.custom"
}

/** Stable DOM id for an option row (referenced by aria-activedescendant). */
function optionDomId(row: Row, idx: number, inputId: string): string {
  if (row.kind === "builtin") return `${inputId}-opt-${idx}-${row.entry.name}`
  return `${inputId}-opt-custom`
}

/**
 * Regex matching syntactically reasonable tool names (mirrors the
 * legacy MCP input's pattern). When the typed value is classified
 * "custom" AND fails this pattern, we surface an inline warning but
 * do NOT block submit (custom-fallback contract per the brief).
 */
const CUSTOM_NAME_PATTERN = /^(mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+|[A-Za-z][A-Za-z0-9_]*)$/

/** Hard cap on the typed string to forgive runaway paste. */
const MAX_TOOL_NAME_LENGTH = 256

/**
 * Normalize a value for the hidden form input: if the typed string
 * resolves to a built-in canonical, write the canonical (case-fixed)
 * name; otherwise leave the value as typed so MCP / custom fallback
 * lands verbatim per the brief.
 */
function canonicalizeForSubmit(typed: string): string {
  const trimmed = typed.trim()
  if (!trimmed) return trimmed
  const builtin = findCcBuiltinTool(trimmed)
  if (builtin) return builtin.name
  return typed
}

export default function ToolCombobox({
  initialValue,
  locale,
  inputId = "tool-combobox-input",
  ariaLabelledBy,
}: Props): React.ReactElement {
  const [query, setQuery] = useState<string>(initialValue ?? "")
  const [value, setValue] = useState<string>(canonicalizeForSubmit(initialValue ?? ""))
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

  // Click-outside (mouse + touch) / focus-out close the dropdown
  // without mutating value. The `pointerdown` listener covers both
  // mouse and touch; `focusout` covers Tab-to-sibling.
  useEffect(() => {
    function onDocPointer(ev: Event) {
      const root = containerRef.current
      if (!root) return
      if (root.contains(ev.target as Node)) return
      setOpen(false)
    }
    function onFocusOut(ev: FocusEvent) {
      const root = containerRef.current
      if (!root) return
      const next = ev.relatedTarget as Node | null
      if (next && root.contains(next)) return
      setOpen(false)
    }
    document.addEventListener("pointerdown", onDocPointer)
    const root = containerRef.current
    root?.addEventListener("focusout", onFocusOut)
    return () => {
      document.removeEventListener("pointerdown", onDocPointer)
      root?.removeEventListener("focusout", onFocusOut)
    }
  }, [])

  const commit = useCallback((next: string) => {
    const canonical = canonicalizeForSubmit(next)
    setValue(canonical)
    setQuery(canonical)
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
        // First ArrowDown from a closed dropdown opens the listbox
        // and lands on the FIRST option (do not advance past it on
        // the same keystroke per WAI ARIA APG).
        if (!open) {
          setOpen(true)
          setHighlight(0)
          return
        }
        setHighlight((h) => Math.min(h + 1, Math.max(rows.length - 1, 0)))
        return
      }
      if (ev.key === "ArrowUp") {
        ev.preventDefault()
        // First ArrowUp from a closed dropdown opens the listbox and
        // lands on the LAST option (symmetry with the ArrowDown rule).
        if (!open) {
          setOpen(true)
          setHighlight(Math.max(rows.length - 1, 0))
          return
        }
        setHighlight((h) => Math.max(h - 1, 0))
        return
      }
      if (ev.key === "Home") {
        if (!open) return
        ev.preventDefault()
        setHighlight(0)
        return
      }
      if (ev.key === "End") {
        if (!open) return
        ev.preventDefault()
        setHighlight(Math.max(rows.length - 1, 0))
        return
      }
      if (ev.key === "PageDown") {
        if (!open) return
        ev.preventDefault()
        setHighlight((h) => Math.min(h + 5, Math.max(rows.length - 1, 0)))
        return
      }
      if (ev.key === "PageUp") {
        if (!open) return
        ev.preventDefault()
        setHighlight((h) => Math.max(h - 5, 0))
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
        if (open) {
          // Open -> close popup AND revert in-flight query to the
          // last-committed value (do NOT mutate value).
          setQuery(value)
          setOpen(false)
        } else {
          // Already closed -> clear both query and value (WAI ARIA
          // APG combobox reference pattern, second Escape press).
          setQuery("")
          setValue("")
        }
        return
      }
      // Tab falls through to default browser behaviour (focus next
      // field) — D70 brief explicitly requires this. We also close
      // the dropdown so the next field isn't covered by a stale
      // panel. None of the <li role="option"> rows carry a tabindex,
      // so even if React hasn't re-rendered before the browser picks
      // the next focusable element, focus correctly skips the
      // listbox.
      if (ev.key === "Tab") {
        setOpen(false)
        return
      }
    },
    [highlight, onPick, open, rows, value],
  )

  // Resolve the displayed kind badge for the currently-typed query.
  const queryKind = useMemo<CcToolKind>(
    () => classifyCcToolName(query),
    [query],
  )

  // Legacy-alias hint: if the user typed a pre-rename canonical, point
  // them at the post-rename canonical (case-sensitive against the
  // binary's literal alias map).
  const legacyHint = useMemo<{ from: string; to: string } | null>(() => {
    const trimmed = query.trim()
    if (!trimmed) return null
    const canonical = legacyAliasCanonical(trimmed)
    if (!canonical) return null
    return { from: trimmed, to: canonical }
  }, [query])

  // Custom-name validity hint: only fires when the typed string
  // classifies as "custom" AND does not match the legacy MCP regex.
  // Warning only — does not block the form per the custom-fallback
  // contract in the brief.
  const customInvalid = useMemo<boolean>(() => {
    const trimmed = query.trim()
    if (!trimmed) return false
    if (queryKind !== "custom") return false
    return !CUSTOM_NAME_PATTERN.test(trimmed)
  }, [query, queryKind])

  const activeDescendant =
    open && rows[highlight] ? optionDomId(rows[highlight], highlight, inputId) : undefined
  const listboxRendered = open && rows.length > 0
  const listboxId = `${inputId}-listbox`

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
        maxLength={MAX_TOOL_NAME_LENGTH}
        placeholder={t("newPolicy.wizard.step2.toolPicker.placeholder")}
        value={query}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listboxRendered ? listboxId : undefined}
        aria-activedescendant={activeDescendant}
        aria-labelledby={ariaLabelledBy}
        role="combobox"
        data-testid="tool-combobox-input"
        onFocus={() => setOpen(true)}
        onChange={(ev) => {
          const next = ev.target.value
          setQuery(next)
          // Mirror the typed text into the persisted value so the
          // form submission carries something even if the dropdown
          // is dismissed without picking. Canonicalize on built-in
          // matches so downstream TOOL_SPECIFIC_BY_NAME["Bash"] hits
          // when the user typed "bash".
          setValue(canonicalizeForSubmit(next))
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
            kind: t(badgeKey(queryKind)),
          })}
        </p>
      )}
      {/* Legacy-alias hint: typed pre-rename canonical -> point at
       * post-rename canonical so authors don't silently persist a
       * dead matcher. */}
      {legacyHint && (
        <p
          data-testid="tool-combobox-legacy-alias-hint"
          className="mt-1 text-[11px] text-amber-700"
        >
          {t("newPolicy.wizard.step2.toolPicker.legacyAliasHint", {
            from: legacyHint.from,
            to: legacyHint.to,
          })}
        </p>
      )}
      {/* Custom-name regex warning (does NOT block submit). */}
      {customInvalid && !legacyHint && (
        <p
          data-testid="tool-combobox-custom-invalid-hint"
          className="mt-1 text-[11px] text-amber-700"
        >
          {t("newPolicy.wizard.step2.toolPicker.customInvalidHint")}
        </p>
      )}
      {listboxRendered && (
        <ul
          id={listboxId}
          role="listbox"
          data-testid="tool-combobox-listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-black/[0.08] bg-white shadow-lg"
        >
          {rows.map((row, idx) => {
            const isHi = idx === highlight
            const domId = optionDomId(row, idx, inputId)
            if (row.kind === "builtin") {
              const e = row.entry
              const desc = locale === "ko" ? e.description.ko : e.description.en
              // aria-selected reflects the COMMITTED value, not the
              // keyboard cursor; the highlight is conveyed via
              // aria-activedescendant on the input.
              const isCommitted = e.name === value
              return (
                <li
                  key={`builtin:${e.name}`}
                  id={domId}
                  role="option"
                  aria-selected={isCommitted}
                  data-highlighted={isHi ? "true" : undefined}
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
                    {t(badgeKey("built-in"))}
                  </span>
                  <span className="text-xs text-[var(--color-text-secondary)]">
                    {desc}
                  </span>
                </li>
              )
            }
            // custom-name affordance row
            const customKind = classifyCcToolName(row.value)
            const isCommitted = row.value === value
            return (
              <li
                key={`custom:${row.value}`}
                id={domId}
                role="option"
                aria-selected={isCommitted}
                data-highlighted={isHi ? "true" : undefined}
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
                  {t(badgeKey(customKind))}
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
// CC_BUILTIN_MANIFEST: Bash, PowerShell, Read, Write, Edit,
// NotebookEdit, Glob, Grep, WebFetch, WebSearch, Agent, TeamCreate,
// TeamDelete, ListAgents, SendMessage, EnterPlanMode, ExitPlanMode,
// TodoWrite, AskUserQuestion, SendUserMessage, SendUserFile,
// PushNotification, StructuredOutput, TaskCreate, TaskGet, TaskUpdate,
// TaskList, TaskStop, TaskOutput, Skill, ToolSearch, Workflow, REPL,
// LSP, Monitor, ScheduleWakeup, ListMcpResourcesTool,
// ReadMcpResourceTool, CronCreate, CronDelete, CronList, RemoteTrigger,
// EnterWorktree, ExitWorktree, DesignSync, ShareOnboardingGuide, Cd
void CC_BUILTIN_TOOLS
