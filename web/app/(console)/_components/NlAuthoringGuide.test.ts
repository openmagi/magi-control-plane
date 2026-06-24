import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * D52e: NlAuthoringGuide source-level invariants.
 *
 * Matches the SteeringAwareField / VerifierFieldChecks / VerifierFormClient
 * test pattern. The full event-level UX (toggle on click, pill click
 * fills textarea, localStorage persistence) is exercised by hand in dev;
 * the source-grep invariants below are the regression risks that would
 * silently break the contract the page.tsx wiring depends on.
 *
 * Specifically:
 *   - The component is a client island ("use client" required for
 *     localStorage + textarea fill).
 *   - The localStorage key is the exact "magi_cp.nl_authoring_guide.expanded"
 *     literal the brief specifies (so an OS-level export/import works
 *     across deploys).
 *   - All three WHEN / CONDITION / WHAT sections are wired with the
 *     right ✓/✗ counts per the brief.
 *   - The pill row carries the six brief-mandated pills, each with the
 *     correct action archetype tone (red=block, amber=ask, blue=audit,
 *     purple=strip).
 *   - The pill click fills a textarea by id (the seam the parent uses
 *     to route fills into the NL <textarea id="nl">).
 *   - All visible text routes through i18n (no hardcoded English).
 */
describe("NlAuthoringGuide source invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "NlAuthoringGuide.tsx"),
    "utf-8",
  )

  it('is marked "use client"', () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("uses the brief-specified localStorage key (cross-deploy contract)", () => {
    expect(src).toContain('"magi_cp.nl_authoring_guide.expanded"')
  })

  it("renders all three sections (WHEN / CONDITION / WHAT) with the brief-mandated counts", () => {
    // WHEN: 4 ✓ + 2 ✗.
    // CONDITION: 7 ✓ (no ✗ in the brief).
    // WHAT: 4 ✓ (no ✗ in the brief).
    const whenOks = (src.match(/nlGuide\.when\.ok\d+\.ex"/g) ?? []).length
    const whenNos = (src.match(/nlGuide\.when\.no\d+\.ex"/g) ?? []).length
    // Each example key is referenced once in the exampleKey field and
    // once in the explainKey field (".explain"). The exampleKey form is
    // the bare `.ex"` suffix; the explainKey is `.ex.explain"`. The
    // regex above intentionally matches only the example side.
    expect(whenOks).toBe(4)
    expect(whenNos).toBe(2)

    const condOks = (src.match(/nlGuide\.condition\.ok\d+\.ex"/g) ?? []).length
    expect(condOks).toBe(7)

    const whatOks = (src.match(/nlGuide\.what\.ok\d+\.ex"/g) ?? []).length
    expect(whatOks).toBe(4)
  })

  it("renders the six brief-mandated TRY ONE OF THESE pills", () => {
    // Block (red, 2). Ask (amber, 1). Audit (blue, 2). Strip (purple, 1).
    const wanted = [
      "nlGuide.pill.blockFetch",
      "nlGuide.pill.denyShell",
      "nlGuide.pill.askMissingSource",
      "nlGuide.pill.auditAwsKey",
      "nlGuide.pill.auditWeakCitations",
      "nlGuide.pill.stripPii",
    ]
    for (const k of wanted) {
      expect(src, `missing pill key ${k}`).toContain(`${k}.label`)
      expect(src, `missing pill fill ${k}`).toContain(`${k}.fill`)
    }
  })

  it("each pill carries the correct action archetype tone (color = action)", () => {
    // The PILLS const literal binds label → tone. Extract via the
    // labelKey + tone shape and assert the brief's color map.
    const expected: Record<string, string> = {
      blockFetch: "block",
      denyShell: "block",
      askMissingSource: "ask",
      auditAwsKey: "audit",
      auditWeakCitations: "audit",
      stripPii: "strip",
    }
    for (const [pillName, tone] of Object.entries(expected)) {
      // Each pill literal looks like:
      //   { labelKey: "nlGuide.pill.<name>.label", fillKey: "...", tone: "<tone>" }
      const re = new RegExp(
        `labelKey:\\s*"nlGuide\\.pill\\.${pillName}\\.label"[\\s\\S]*?tone:\\s*"${tone}"`,
      )
      expect(src.match(re), `pill ${pillName} should have tone=${tone}`).not.toBeNull()
    }
  })

  it("pill color tokens follow action archetype (red/amber/blue/purple light theme)", () => {
    // pillClasses must map each action to its brief-mandated hue.
    expect(src).toMatch(/case "block":[\s\S]*?red-/)
    expect(src).toMatch(/case "ask":[\s\S]*?amber-/)
    expect(src).toMatch(/case "audit":[\s\S]*?blue-/)
    expect(src).toMatch(/case "strip":[\s\S]*?purple-/)
  })

  it("pill click does NOT auto-submit; it only fills the textarea", () => {
    // The pill button is type="button" so it never submits the parent
    // form. Reading the literal proves the contract: a refactor that
    // dropped the type would silently submit the empty NL form.
    expect(src).toMatch(/type="button"/)
    // The fill path is the dispatched 'input' event on the looked-up
    // textarea by id.
    expect(src).toContain('dispatchEvent(new Event("input"')
    // And there is no programmatic form submit anywhere in the
    // component (no requestSubmit / form.submit calls).
    expect(src).not.toMatch(/requestSubmit|form\.submit\(/)
  })

  it("looks up the target textarea by document.getElementById (parent-owned id)", () => {
    // The parent (policies/new/page.tsx) hands the NL textarea id
    // through `targetTextareaId`. We must look it up off the DOM
    // (not a ref the guide owns), so the page can keep using the
    // Textarea UI primitive without threading a ref.
    expect(src).toContain("document.getElementById(targetId)")
  })

  it("ambiguity callout is yellow (border-amber + bg-amber-50)", () => {
    // The yellow note is the brief-mandated visual: yellow = "compiler
    // will ask a question, not refuse." The accent color is load-bearing
    // (matches the SteeringAwareField steering tip), so lock it.
    expect(src).toMatch(/data-testid="nl-authoring-guide-ambiguity"[\s\S]*?border-amber-/)
    expect(src).toMatch(/data-testid="nl-authoring-guide-ambiguity"[\s\S]*?bg-amber-50/)
  })

  it("AUTHORING GUIDE pin uses light-theme purple accent", () => {
    // The purple pin matches the rest of the dashboard's purple chips
    // (preset categories, custom-verifier badge). Lock the color so a
    // tailwind purge / token refactor flags this on diff.
    expect(src).toMatch(/data-testid="nl-authoring-guide-pin"[\s\S]*?border-purple-/)
    expect(src).toMatch(/data-testid="nl-authoring-guide-pin"[\s\S]*?bg-purple-50/)
  })

  it("collapsed by default (initial state = false)", () => {
    // The brief specifies "closed by default; persisted open/closed
    // state per-user in localStorage." Initial state is false; the
    // useEffect later hydrates from localStorage.
    expect(src).toMatch(/useState<boolean>\(false\)/)
  })

  it("all visible text routes through i18n (no hardcoded English copy)", () => {
    // Heuristic: any visible UI string that is NOT routed through t()
    // would show up as a >2-word English literal in JSX. The component
    // does carry a few one-token glyphs (✓ ✗ ▸) intentionally (those
    // are not "copy") but no full English sentences. Lock the absence
    // of obvious tells. We strip /* … */ block comments and // line
    // comments first so explanatory prose in the file header doesn't
    // false-positive.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    const banned = [
      "What can I write",
      "Try one of these",
      "If your phrasing is ambiguous",
      "AUTHORING GUIDE",
      "block / deny",
    ]
    for (const phrase of banned) {
      expect(
        stripped,
        `English literal "${phrase}" must be routed through t(), not hardcoded`,
      ).not.toContain(phrase)
    }
  })

  it("aria-expanded mirrors the open state (a11y disclosure pattern)", () => {
    // The collapsible header is a button; SR users discover the panel
    // via aria-expanded toggling on the button. Locking the attribute
    // prevents a refactor from turning it into a non-disclosed div.
    expect(src).toMatch(/aria-expanded=/)
    expect(src).toMatch(/aria-controls=/)
  })

  it("uses semantic <ul role='list'> for example + pill rows", () => {
    // The ✓/✗ example rows and the pill row are both lists. Use
    // explicit role='list' so AT that strip the role under display:flex
    // (older NVDA + Firefox) still keep the list semantics.
    expect(src).toMatch(/role="list"/)
  })
})

/**
 * D52e: NL compose mode integration. The component must be mounted on
 * the NL compose mode of /policies/new and ONLY there (the Guided and
 * Raw IR modes are structured already and the brief calls out that
 * they do NOT get the guide).
 */
describe("NlAuthoringGuide page wiring (policies/new NL mode)", () => {
  const pageSrc = readFileSync(
    path.join(__dirname, "..", "policies", "new", "page.tsx"),
    "utf-8",
  )

  it("is imported by /policies/new", () => {
    expect(pageSrc).toMatch(
      /import NlAuthoringGuide from ".+NlAuthoringGuide"/,
    )
  })

  it("is mounted inside the NL branch and targets the nl textarea", () => {
    expect(pageSrc).toMatch(/<NlAuthoringGuide[\s\S]*?targetTextareaId="nl"/)
  })

  it("is NOT mounted on the Guided or Advanced branches (structured-already)", () => {
    // Count occurrences to confirm exactly one mount lives in the file.
    const mounts = pageSrc.match(/<NlAuthoringGuide/g) ?? []
    expect(mounts.length).toBe(1)
  })

  it("the NL textarea still uses id='nl' (the seam the guide fills)", () => {
    expect(pageSrc).toMatch(/<Textarea[\s\S]*?id="nl"/)
  })
})

/**
 * D52e: i18n key coverage. The component references roughly 50+ keys.
 * The KO / EN drift gate (web/lib/i18n/dict.test.ts) catches missing
 * keys on either side; this gate catches a SOURCE-side reference to a
 * key that has never been added to the dict at all.
 */
describe("NlAuthoringGuide i18n key coverage", () => {
  const src = readFileSync(
    path.join(__dirname, "NlAuthoringGuide.tsx"),
    "utf-8",
  )
  const dictSrc = readFileSync(
    path.join(__dirname, "..", "..", "..", "lib", "i18n", "dict.ts"),
    "utf-8",
  )

  it("every nlGuide.* key referenced in the component exists in dict.ts", () => {
    const referenced = new Set<string>()
    for (const m of src.matchAll(/"(nlGuide\.[a-zA-Z0-9.]+)"/g)) {
      referenced.add(m[1])
    }
    expect(referenced.size, "no nlGuide keys referenced").toBeGreaterThan(10)
    const missing = [...referenced].filter(
      (k) => !dictSrc.includes(`"${k}":`),
    )
    expect(
      missing,
      `nlGuide keys referenced in the component but missing in dict.ts:\n${missing.join("\n")}`,
    ).toEqual([])
  })

  it("the AUTHORING GUIDE pin label exists in both locales", () => {
    expect(dictSrc).toContain('"nlGuide.header.pin": "AUTHORING GUIDE"')
  })

  it("placeholder copy in the dict matches the brief's control-plane idioms", () => {
    // The brief explicitly rewrote compile.field.placeholder. Lock the
    // new control-plane idioms so a future "tidy up" doesn't roll back
    // to the old "법원 filing" copy.
    expect(dictSrc).toContain("deny shell exec")
    expect(dictSrc).toContain("rewire privilege scan to opt-in")
  })
})
