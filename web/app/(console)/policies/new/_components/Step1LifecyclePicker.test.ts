import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  matchesQuery,
  ADVANCED_GROUP_PREVIEWS,
  COMMON_GROUP,
  ADVANCED_GROUPS,
  ADVANCED_OPEN_STORAGE_KEY,
  findOwningAdvancedGroup,
  type LifecycleSlug,
} from "./step1-lifecycle-groups"

/**
 * D61 + D69: Step 1 lifecycle picker invariants.
 *
 * The picker collapses 30 hook events into a default-expanded
 * "Common" group (5 as of D69; 4 in the original D61 layout) plus
 * 6 collapsed-by-default "Advanced" groups (25). Behaviour is
 * exercised below; source-level invariants guard the wiring that a
 * future refactor is most likely to silently break:
 *
 *   - "use client" pragma (otherwise it server-renders and
 *     toggle/search regress).
 *   - The search input has NO `name` attribute (so it cannot
 *     accidentally post to advanceWizard).
 *   - The radio inputs DO have `name="lifecycle"` (the surrounding
 *     <form action={advanceAction}> reads the picked value from the
 *     radio, not from a hidden mirror).
 *   - localStorage key shape matches the documented contract.
 *   - Sub-path imports only (no `@/components/ui` barrel import).
 *   - Uses translate(locale, ...) instead of a t() closure.
 */

const HERE = __dirname
function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("Step1LifecyclePicker | group composition", () => {
  it("Common group exposes the 5 recommended events in order (D69)", () => {
    // D69: TaskCompleted joined Common because end-of-task automation
    // ("inject task results back into the session", "run a recovery
    // script when a background task finishes") is one of the most
    // common operator patterns. PreToolUse stays first so the
    // "recommended" badge still anchors the new-policy entry path.
    expect(COMMON_GROUP.kind).toBe("common")
    expect(COMMON_GROUP.members).toEqual([
      "before_tool_use",
      "after_tool_use",
      "user_prompt",
      "pre_final",
      "task_completed",
    ])
  })

  it("every advanced group is marked as advanced (collapsed by default)", () => {
    for (const group of ADVANCED_GROUPS) {
      expect(group.kind).toBe("advanced")
    }
  })

  it("Common (5) + Advanced (25) cover all 30 lifecycle events with no overlap", () => {
    const all: LifecycleSlug[] = [
      ...COMMON_GROUP.members,
      ...ADVANCED_GROUPS.flatMap((g) => g.members),
    ]
    expect(all.length).toBe(30)
    expect(new Set(all).size).toBe(30) // no duplicates
  })

  it("every advanced group has at least one member", () => {
    for (const group of ADVANCED_GROUPS) {
      expect(group.members.length).toBeGreaterThan(0)
    }
  })

  it("PreToolUse / PostToolUse / UserPromptSubmit / Stop / TaskCompleted live in Common (not Advanced)", () => {
    const advancedSet = new Set(
      ADVANCED_GROUPS.flatMap((g) => g.members as readonly LifecycleSlug[]),
    )
    expect(advancedSet.has("before_tool_use")).toBe(false)
    expect(advancedSet.has("after_tool_use")).toBe(false)
    expect(advancedSet.has("user_prompt")).toBe(false)
    expect(advancedSet.has("pre_final")).toBe(false)
    expect(advancedSet.has("task_completed")).toBe(false)
  })

  it("uses the documented localStorage key", () => {
    expect(ADVANCED_OPEN_STORAGE_KEY).toBe("magi_cp.step1_advanced_open")
  })
})

describe("Step1LifecyclePicker | matchesQuery", () => {
  // The plain-language label is what the operator scans visually,
  // so the search must catch substrings against either the slug or
  // the label (the picker passes the locale-resolved label in).
  it("returns true for every row when query is empty or whitespace", () => {
    expect(matchesQuery("before_tool_use", "Before a tool runs (PreToolUse)", "")).toBe(true)
    expect(matchesQuery("before_tool_use", "Before a tool runs (PreToolUse)", "   ")).toBe(true)
  })

  it("matches a substring of the slug, case-insensitively", () => {
    expect(matchesQuery("before_tool_use", "Before a tool runs (PreToolUse)", "TOOL")).toBe(true)
    expect(matchesQuery("worktree_create", "Worktree created (WorktreeCreate)", "worktree")).toBe(true)
  })

  it("matches a substring of the plain-language label, case-insensitively", () => {
    expect(matchesQuery("user_prompt", "Before a user prompt (UserPromptSubmit)", "prompt")).toBe(true)
    expect(matchesQuery("user_prompt", "Before a user prompt (UserPromptSubmit)", "PROMPT")).toBe(true)
  })

  it("matches a CC event-name substring even when the slug differs (PreToolUse vs before_tool_use)", () => {
    expect(matchesQuery("before_tool_use", "Before a tool runs (PreToolUse)", "PreToolUse")).toBe(true)
    expect(matchesQuery("pre_final", "When the agent stops (Stop)", "stop")).toBe(true)
  })

  it("returns false when neither slug nor label contains the query", () => {
    expect(matchesQuery("before_tool_use", "Before a tool runs (PreToolUse)", "zzznomatch")).toBe(false)
  })

  it("trims surrounding whitespace before comparing", () => {
    expect(matchesQuery("worktree_create", "Worktree created (WorktreeCreate)", "  worktree  ")).toBe(true)
  })
})

describe("Step1LifecyclePicker | source invariants", () => {
  const src = read("Step1LifecyclePicker.tsx")

  it("is marked \"use client\"", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("does NOT import from the @/components/ui barrel", () => {
    // The barrel pulls a server-only chain into the client bundle.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    expect(stripped).not.toMatch(/from\s+["']@\/components\/ui["']/)
  })

  it("uses translate(locale, ...) instead of accepting a t() closure", () => {
    expect(src).toContain("translate(locale,")
    // The component prop shape declares `locale`, not `t`.
    expect(src).toMatch(/locale:\s*"ko"\s*\|\s*"en"/)
  })

  it("radio inputs carry name=\"lifecycle\" so the surrounding form posts the picked slug", () => {
    expect(src).toMatch(/name="lifecycle"/)
  })

  it("search input has NO `name` attribute (must not post to advanceWizard)", () => {
    // The search input is the only `<input id="step1-search"` in the
    // component. We slice from that anchor to the next self-closing
    // `/>` so the assertion covers ONLY the search element's prop
    // surface (not the unrelated radio input). JSX line comments
    // ("// ...") get stripped first so a documentation comment
    // mentioning `name=` cannot trip the gate.
    const anchor = src.indexOf('id="step1-search"')
    expect(anchor).toBeGreaterThan(-1)
    const tagStart = src.lastIndexOf("<input", anchor)
    const tagEnd = src.indexOf("/>", anchor) + 2
    const tag = src.slice(tagStart, tagEnd)
    const stripped = tag
      .replace(/\/\/[^\n]*/g, "") // strip JSX line comments
      .replace(/\/\*[\s\S]*?\*\//g, "") // strip block comments
    expect(stripped).not.toMatch(/\s+name=["']/)
  })

  it("uses the documented localStorage key shape (magi_cp.step1_advanced_open)", () => {
    expect(src).toContain("magi_cp.step1_advanced_open")
  })

  it("guards readPersistedOpen against malformed JSON / corrupted state", () => {
    // The catch block silently returns an empty Set on parse failure;
    // a thrown SyntaxError here would break the wizard outright.
    expect(src).toMatch(/JSON\.parse/)
    expect(src).toMatch(/catch\s*\{/)
  })

  it("renders a recommended badge ONLY on before_tool_use", () => {
    // The badge prop branches on the slug check at the row-prop site.
    expect(src).toMatch(/showBadge=\{slug === "before_tool_use"\}/)
    // Inside the Common group block, the badge prop is on before_tool_use
    // (the only place where showBadge could be true). The Advanced
    // groups always pass showBadge={false}.
    expect(src).toMatch(/showBadge=\{false\}/)
  })

  it("advanced-group header is a <button> with aria-expanded (a11y)", () => {
    expect(src).toMatch(/<button[\s\S]*?aria-expanded=/)
    expect(src).toMatch(/aria-controls=/)
  })

  it("renders an empty-state hint when the search query has zero matches", () => {
    expect(src).toContain("step1-search-empty")
    expect(src).toContain("newPolicy.wizard.step1.search.empty")
  })

  it("collapsed groups still mount the radio inputs (CSS hide, not unmount)", () => {
    // The row container hides via `hidden ? "hidden" : ""` class,
    // not by skipping render. This way a collapsed Advanced group with
    // a previously-picked event still posts the correct value.
    expect(src).toMatch(/hidden \? "hidden" : ""/)
  })

  it("auto-expands the Advanced group that owns currentLifecycle on mount (P1 discoverability)", () => {
    // When the wizard returns to Step 1 with `currentLifecycle`
    // pointing inside a collapsed-by-default Advanced group (e.g.
    // back-nav from Step 2, error redirect, or D56a Step 6 jump
    // back), the selected card must be visible on first paint.
    // Otherwise the row is hidden by the parent group container
    // and the wizard appears to have forgotten the choice. The
    // mount-effect unions the owning group's key into `openSet`
    // without writing to localStorage (visual-only).
    expect(src).toMatch(/findOwningAdvancedGroup\s*\(\s*currentLifecycle\s*\)/)
    // The effect must depend on currentLifecycle (otherwise a
    // late-arriving prop would not re-open the owning group).
    expect(src).toMatch(/\[\s*currentLifecycle\s*\]/)
    // We must NOT persist the auto-expand: writePersistedOpen() may
    // not be called inside the effect body itself.
    const effAnchor = src.indexOf("findOwningAdvancedGroup")
    const effBlockStart = src.lastIndexOf("useEffect(", effAnchor)
    const effBlockEnd = src.indexOf("}, [", effAnchor)
    const effBody = src.slice(effBlockStart, effBlockEnd)
    expect(effBody).not.toMatch(/writePersistedOpen\s*\(/)
  })

  it("group-toggle button is disabled while the search query is active (P2 ux/feedback)", () => {
    // `effectivelyOpen = queryActive ? true : persistedOpen` already
    // forces matching groups open during search. Letting the user
    // click the toggle anyway silently flips `openSet` (and
    // localStorage) with no visible change, so when they later
    // clear the query the group may be in the opposite state. Block
    // the toggle while searching to keep persisted state honest.
    expect(src).toMatch(/disabled=\{queryActive\}/)
  })

  it("renders a hint when the selected lifecycle row is hidden by the search filter (P1 hidden-row submit)", () => {
    // A `:checked` radio inside a `.hidden` row container is still
    // submitted by the surrounding <form>. The brief flags this
    // exact "empty submit advances anyway" trap. We surface a
    // visible amber hint plus a "Clear search" button so the
    // operator can either widen the filter or pick a visible row
    // before clicking Next.
    expect(src).toContain("step1-selection-hidden-hint")
    expect(src).toContain("newPolicy.wizard.step1.selectionHidden")
    expect(src).toContain("step1-selection-hidden-clear")
  })

  it("renders an inline preview of example event names on each collapsed Advanced group header (P2 discoverability)", () => {
    // Collapsed headers used to carry only the family name + count.
    // An operator who knows they want "PostToolUseFailure" should
    // not have to expand every group to find the right one. The
    // preview slot uses `ADVANCED_GROUP_PREVIEWS[group.key]` and
    // renders inline next to the family label.
    expect(src).toContain("ADVANCED_GROUP_PREVIEWS")
    expect(src).toMatch(/step1-group-preview-/)
  })

  it("uses module-level Tailwind class constants for the selected-state border + bg (P2 consistency)", () => {
    // Pin the accent-color token names against a single source of
    // truth so a theme rename in the server-side <RadioCard> + a
    // missed update here fails CI loudly. The component declares
    // SELECTED_BORDER_CLASS / SELECTED_BG_CLASS / HOVER_BORDER_CLASS
    // and feeds them into the row span's className.
    expect(src).toContain("SELECTED_BORDER_CLASS")
    expect(src).toContain("SELECTED_BG_CLASS")
    expect(src).toContain("HOVER_BORDER_CLASS")
    // The selected-state tokens must reference `--color-accent`,
    // matching the rest of the wizard's <RadioCard>. A future
    // theme rename here without the matching <RadioCard> change
    // fails the gate.
    expect(src).toMatch(
      /SELECTED_BORDER_CLASS\s*=\s*"peer-checked:border-\[var\(--color-accent\)\]"/,
    )
    expect(src).toMatch(
      /SELECTED_BG_CLASS\s*=\s*"peer-checked:bg-\[var\(--color-accent\)\]\/\[0\.05\]"/,
    )
  })

  it("renders a single 'Advanced' section header above the Advanced groups (P2 ux/polish)", () => {
    // The disclosure tier is announced once. Per-group labels do
    // not need to repeat "(advanced)" on every row.
    expect(src).toContain("step1-advanced-section-header")
    expect(src).toContain("newPolicy.wizard.step1.advancedSection")
  })
})

describe("Step1LifecyclePicker | helpers", () => {
  it("findOwningAdvancedGroup returns the group for an Advanced slug", () => {
    // permission_request lives in the Permissions advanced group.
    const g = findOwningAdvancedGroup("permission_request")
    expect(g).not.toBeNull()
    expect(g!.key).toBe("newPolicy.wizard.step1.group.permissions")
    expect(g!.kind).toBe("advanced")
  })

  it("findOwningAdvancedGroup returns null for a Common slug", () => {
    // before_tool_use is in the always-expanded Common group, no
    // need to auto-expand any Advanced group.
    expect(findOwningAdvancedGroup("before_tool_use")).toBeNull()
    expect(findOwningAdvancedGroup("after_tool_use")).toBeNull()
    expect(findOwningAdvancedGroup("user_prompt")).toBeNull()
    expect(findOwningAdvancedGroup("pre_final")).toBeNull()
    // D69: task_completed is now in Common.
    expect(findOwningAdvancedGroup("task_completed")).toBeNull()
  })

  it("ADVANCED_GROUP_PREVIEWS covers every Advanced group with at least one example", () => {
    for (const group of ADVANCED_GROUPS) {
      const preview = ADVANCED_GROUP_PREVIEWS[group.key]
      expect(preview).toBeDefined()
      expect(preview.length).toBeGreaterThanOrEqual(1)
      expect(preview.length).toBeLessThanOrEqual(3)
    }
  })
})

describe("Step1LifecyclePicker | page.tsx wiring", () => {
  const pageSrc = readFileSync(
    path.join(__dirname, "..", "page.tsx"),
    "utf-8",
  )

  it("imports Step1LifecyclePicker from the colocated _components dir", () => {
    expect(pageSrc).toMatch(
      /import\s+Step1LifecyclePicker\s+from\s+["']\.\/_components\/Step1LifecyclePicker["']/,
    )
  })

  it("Step 1 surface renders <Step1LifecyclePicker .../>", () => {
    expect(pageSrc).toMatch(/<Step1LifecyclePicker[\s\S]*?\/>/)
  })

  it("passes locale + currentLifecycle + labels props (no t() closure)", () => {
    const tag = pageSrc.match(/<Step1LifecyclePicker[\s\S]*?\/>/)
    expect(tag).not.toBeNull()
    expect(tag![0]).toMatch(/locale=\{locale\}/)
    expect(tag![0]).toMatch(/currentLifecycle=\{current\}/)
    expect(tag![0]).toMatch(/labels=\{labels\}/)
  })

  it("Step 1 form still posts to the wizard server action with _step=1", () => {
    // The wrapping <form action={action}> + hidden _step=1 input is
    // what advances the wizard; the client picker only renders the
    // radio. If a future refactor accidentally removed the form, the
    // submit would no-op.
    const step1FuncStart = pageSrc.indexOf("function Step1Lifecycle(")
    expect(step1FuncStart).toBeGreaterThan(-1)
    const step1FuncEnd = pageSrc.indexOf("\nfunction ", step1FuncStart + 1)
    const step1Block = pageSrc.slice(step1FuncStart, step1FuncEnd)
    expect(step1Block).toMatch(/<form action=\{action\}/)
    expect(step1Block).toMatch(/name="_step" value="1"/)
  })
})
