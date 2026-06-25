import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D76 follow-up: source-grep invariants for the /overview chart and
 * the OverviewLive client island. Locks the contract on the fixes for
 * the post-D76 review (touch tap, empty-state caption, parallel SR
 * table, locale-aware date formatting, single-bucket sizing,
 * abort-on-tick, polite live region, lazy localStorage, footer
 * visibility gate, refreshing indicator).
 *
 * Same source-grep convention as
 * `web/app/(console)/d72-empty-states.test.ts`: we lock the contract
 * without spinning up RTL or jsdom.
 */
const HERE = __dirname
function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("EmissionChart — touch / tap surface", () => {
  const src = read("EmissionChart.tsx")

  it("wires onClick on each column so touch users reach the detail panel", () => {
    // The pre-fix component only had onMouseEnter / onFocus, so iOS /
    // Android operators could never surface the detail panel. The
    // selectColumn helper toggles the selection so a second tap also
    // dismisses.
    expect(src).toContain("selectColumn(i)")
    expect(src).toMatch(/onClick=\{\(ev\)\s*=>\s*\{[\s\S]*selectColumn\(i\)/)
  })

  it("a tap on the same column toggles the detail panel off", () => {
    expect(src).toMatch(/setSelectedIdx\(prev\s*=>\s*\(prev\s*===\s*i\s*\?\s*null\s*:\s*i\)\)/)
  })

  it("an SVG-level click on empty space clears the selection", () => {
    expect(src).toContain("clearSelection")
    expect(src).toMatch(/ev\.target\s*===\s*ev\.currentTarget/)
  })
})

describe("EmissionChart — empty-state visible caption", () => {
  const src = read("EmissionChart.tsx")

  it("renders the empty-state copy in-SVG when yMax is 0", () => {
    expect(src).toContain('data-testid="overview-chart-empty"')
    expect(src).toMatch(/isEmpty\s*&&\s*\(\s*<text[\s\S]*emptyBody/)
  })

  it("suppresses faint 25/50/75/100% gridlines in the empty case", () => {
    // The pre-fix component drew the dashed gridlines unconditionally,
    // implying a Y scale that doesn't exist. The fix renders only the
    // baseline when isEmpty.
    expect(src).toMatch(/isEmpty\s*\?\s*\(\s*<line/)
  })
})

describe("EmissionChart — SR parallel table replaces tabIndex on <g>", () => {
  const src = read("EmissionChart.tsx")

  it("renders a hidden table sibling for SR users", () => {
    expect(src).toContain('data-testid="overview-chart-sr-table"')
    expect(src).toContain("<caption>")
  })

  it("does NOT put tabIndex on the column <g>", () => {
    // Safari + VoiceOver + Android TalkBack do not consistently route
    // focus to non-interactive SVG <g> elements. The fix drops the
    // tabIndex and surfaces SR access via the parallel <table>.
    const code = src.replace(/^\s*\/\/.*$/gm, "")
                    .replace(/^\s*\*.*$/gm, "")
                    .replace(/\/\*[\s\S]*?\*\//g, "")
    expect(code).not.toMatch(/<g[^>]*tabIndex/)
  })
})

describe("EmissionChart — locale-aware date formatting", () => {
  const src = read("EmissionChart.tsx")

  it("formats the bucket timestamp via Intl.DateTimeFormat (NOT toLocaleString)", () => {
    // The pre-fix tooltip used `new Date(...).toLocaleString()` with
    // no locale arg, so a Korean dashboard rendered in an English
    // Chrome showed an English date inside Korean copy. Threading the
    // explicit locale tag keeps the chart's hour labels, the detail
    // panel, and the SR table consistent.
    expect(src).toContain("formatBucketTimestamp")
    expect(src).toContain("new Intl.DateTimeFormat(locale")
    expect(src).not.toMatch(/new Date\([^)]*\)\.toLocaleString\(\)/)
  })

  it("accepts an explicit locale prop so the parent threads its locale tag", () => {
    expect(src).toMatch(/locale\?:\s*string/)
    expect(src).toContain("resolveLocale")
  })
})

describe("EmissionChart — single-bucket sizing + detail panel placement", () => {
  const src = read("EmissionChart.tsx")

  it("widens the bar for n<=2 so a single bucket reads as deliberate", () => {
    expect(src).toMatch(/n\s*<=\s*2/)
    expect(src).toContain("colW * 0.6")
  })

  it("places the detail panel left/right based on which half is active", () => {
    expect(src).toContain("alignRight")
    expect(src).toContain("buckets.length / 2")
  })

  it("the detail panel is NOT role=status aria-live (no scrub flooding)", () => {
    // Strip JS line comments so the "no scrub flooding" comment that
    // narrates the fix doesn't trip the assertion.
    const code = src.replace(/^\s*\/\/.*$/gm, "")
                    .replace(/^\s*\*.*$/gm, "")
                    .replace(/\/\*[\s\S]*?\*\//g, "")
    expect(code).not.toMatch(/role=['"]status['"]/)
    expect(code).not.toMatch(/aria-live=['"]polite['"]/)
  })
})

describe("OverviewLive — fetch lifecycle and races", () => {
  const src = read("OverviewLive.tsx")

  it("hoists a per-effect AbortController and wires it into fetch()", () => {
    expect(src).toContain("AbortController")
    expect(src).toContain("ctrl.signal")
    expect(src).toContain("signal: ctrl.signal")
  })

  it("the polling-loop cleanup aborts the in-flight controller", () => {
    expect(src).toMatch(/clearInterval\(id\)[\s\S]*ctrl\.abort\(\)/)
  })

  it("treats AbortError as a no-op (not a network blip)", () => {
    expect(src).toMatch(/name\?\s*:\s*string/)
    expect(src).toContain('"AbortError"')
  })

  it("uses last-write-wins: a fresh tick aborts an in-flight predecessor", () => {
    // The pre-fix component dropped overlapping ticks via an
    // `inFlight` boolean. With slow upstreams that froze the dashboard
    // silently. The fix aborts the predecessor + tracks
    // `isRefreshing` so the UI shows a "refreshing" indicator.
    expect(src).toMatch(/inFlightCtrl\.current\.abort/)
    expect(src).toContain("isRefreshing")
  })
})

describe("OverviewLive — aria-live announces only meaningful changes", () => {
  const src = read("OverviewLive.tsx")

  it("wraps a derived sentence in aria-live=polite aria-atomic=true", () => {
    expect(src).toMatch(/aria-live=['"]polite['"]/)
    expect(src).toMatch(/aria-atomic=['"]true['"]/)
    expect(src).toContain('data-testid="overview-live-region"')
  })

  it("derives the announcement via useMemo of detailText + chain state", () => {
    expect(src).toMatch(/announcement\s*=\s*useMemo/)
    expect(src).toContain("chainOk")
  })
})

describe("OverviewLive — lazy localStorage init + cross-tab sync", () => {
  const src = read("OverviewLive.tsx")

  it("reads the storageKey opt-out via a lazy useState initialiser", () => {
    expect(src).toContain("readEnabledFromStorage")
    expect(src).toMatch(/useState<boolean>\(\s*\(\)\s*=>\s*readEnabledFromStorage/)
  })

  it("subscribes to the 'storage' event so cross-tab toggles converge", () => {
    expect(src).toContain('addEventListener("storage"')
    expect(src).toMatch(/e\.key\s*!==\s*storageKey/)
  })
})

describe("OverviewLive — RefreshFooter gates its 5s ticker on tab visibility", () => {
  const src = read("OverviewLive.tsx")

  it("RefreshFooter receives tabVisible and short-circuits the interval when hidden", () => {
    expect(src).toMatch(/RefreshFooter[\s\S]*tabVisible/)
    expect(src).toMatch(/useEffect\(\(\)\s*=>\s*\{\s*\n?\s*if\s*\(!tabVisible\)\s*return/)
  })

  it("renders a 'refreshing' indicator + disables Refresh-Now while inflight", () => {
    expect(src).toContain("overview-refreshing-indicator")
    expect(src).toContain("disabled={isRefreshing}")
    expect(src).toContain("aria-busy={isRefreshing}")
  })
})

describe("RecentActivity — closed-vocab verdict projection", () => {
  const src = read("RecentActivity.tsx")

  it("projects raw producer verdicts onto the dashboard's closed vocabulary", () => {
    // The chart's by_verdict.fail bar and this row's badge must speak
    // the same word for the same ledger row. Map deny→fail and
    // review→needs_review on egress (mirror of `_project_verdict` in
    // src/magi_cp/cloud/metrics.py).
    expect(src).toContain("projectVerdict")
    expect(src).toMatch(/raw\s*===\s*"deny"[\s\S]*return\s*"fail"/)
    expect(src).toMatch(/raw\s*===\s*"review"[\s\S]*return\s*"needs_review"/)
  })

  it("Verdict type drops the raw `deny` / `review` variants", () => {
    expect(src).toMatch(/type Verdict\s*=\s*\n?\s*\|\s*"pass"\s*\|\s*"fail"\s*\|\s*"needs_review"\s*\|\s*"not_applicable"/)
  })
})
