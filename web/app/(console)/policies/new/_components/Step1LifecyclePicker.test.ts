import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  matchesQuery,
  COMMON_GROUP,
  ADVANCED_GROUPS,
  ADVANCED_OPEN_STORAGE_KEY,
  type LifecycleSlug,
} from "./step1-lifecycle-groups"

/**
 * D61: Step 1 lifecycle picker invariants.
 *
 * The picker collapses 30 hook events into a default-expanded
 * "Common" group (4) plus 6 collapsed-by-default "Advanced" groups
 * (26). Behaviour is exercised below; source-level invariants guard
 * the wiring that a future refactor is most likely to silently break:
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
  it("Common group exposes the 4 recommended events in order", () => {
    expect(COMMON_GROUP.kind).toBe("common")
    expect(COMMON_GROUP.members).toEqual([
      "before_tool_use",
      "after_tool_use",
      "user_prompt",
      "pre_final",
    ])
  })

  it("every advanced group is marked as advanced (collapsed by default)", () => {
    for (const group of ADVANCED_GROUPS) {
      expect(group.kind).toBe("advanced")
    }
  })

  it("Common (4) + Advanced (26) cover all 30 lifecycle events with no overlap", () => {
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

  it("PreToolUse / PostToolUse / UserPromptSubmit / Stop live in Common (not Advanced)", () => {
    const advancedSet = new Set(
      ADVANCED_GROUPS.flatMap((g) => g.members as readonly LifecycleSlug[]),
    )
    expect(advancedSet.has("before_tool_use")).toBe(false)
    expect(advancedSet.has("after_tool_use")).toBe(false)
    expect(advancedSet.has("user_prompt")).toBe(false)
    expect(advancedSet.has("pre_final")).toBe(false)
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
