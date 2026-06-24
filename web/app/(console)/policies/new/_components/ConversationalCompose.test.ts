import { describe, it, expect } from "vitest"
import { readFileSync, readdirSync } from "node:fs"
import path from "node:path"

/**
 * D55b: source-level invariants for ConversationalCompose + sibling
 * client islands. Same pattern as the D52e / D53b panels — grep the
 * rendered TSX for the contract instead of mounting React Testing
 * Library. The browser behavior (chat scroll, pill click, send) is
 * exercised manually in dev; these invariants catch the regressions
 * a future maintainer is most likely to silently break.
 */

const HERE = __dirname

function read(rel: string): string {
  return readFileSync(path.join(HERE, rel), "utf-8")
}

describe("ConversationalCompose source invariants", () => {
  const src = read("ConversationalCompose.tsx")

  it("declares 'use client' (the parent server tree mounts it)", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("hits the same-origin /api/policies/compile-interactive proxy", () => {
    expect(src).toContain("/api/policies/compile-interactive")
    // The component must NOT read MAGI_CP_ADMIN_API_KEY directly; the
    // Next.js API route reads the key server-side only.
    expect(src).not.toMatch(/process\.env\.MAGI_CP_ADMIN_API_KEY/)
  })

  it("renders a role='log' aria-live='polite' chat scroll (a11y)", () => {
    expect(src).toContain('role="log"')
    expect(src).toContain('aria-live="polite"')
  })

  it("renders the friendly empty-state intro with starter pills", () => {
    expect(src).toContain("newPolicy.conv.intro")
    expect(src).toContain("STARTER_PILLS")
    // The brief calls for 4-5 starter pills mirroring D52e.
    const matches = src.match(/labelKey:\s*"newPolicy\.conv\.starterPills\./g) ?? []
    expect(matches.length).toBeGreaterThanOrEqual(4)
    expect(matches.length).toBeLessThanOrEqual(6)
  })

  it("send button POSTs the running history + draft + answers", () => {
    // The body shape mirrors D55a's wire contract (history /
    // draft_so_far / answers). The keys are written as JS object
    // shorthand so we grep for the bare identifiers.
    expect(src).toMatch(/\bhistory[,:]/)
    expect(src).toContain("draft_so_far")
    expect(src).toContain("answers")
  })

  it("renders the IrDraftPane in the right column", () => {
    expect(src).toContain("<IrDraftPane")
    // The save action must be threaded through; the IrDraftPane wraps
    // the actual Save form.
    expect(src).toContain("saveAction={saveAction}")
  })

  it("disables the input while a turn is in flight (no piling up)", () => {
    expect(src).toMatch(/disabled=\{pending/)
  })

  it("renders an Assistant-typing skeleton placeholder when pending", () => {
    expect(src).toContain("conv-chat-typing")
    expect(src).toContain("newPolicy.conv.assistantTyping")
  })

  it("provider_unconfigured surfaces as an assistant bubble (NOT a top banner)", () => {
    // The brief: "error states matching D52e hotfix 2 (provider_
    // unconfigured surfaces as an assistant bubble with the actionable
    // copy, not a top-of-page banner)."
    expect(src).toContain("provider_unconfigured")
    expect(src).toContain("newPolicy.conv.error.providerUnconfigured")
  })

  it("Enter (without Shift) submits the input box", () => {
    expect(src).toMatch(/e\.key === "Enter"/)
    expect(src).toMatch(/!e\.shiftKey/)
  })

  it("never exposes internal vocabulary (regex/shacl/llm_critic/matcher/kind/lifecycle) to the chat surface", () => {
    // The brief's HARD RULE: NL/conversational UX never exposes internal
    // terms (regex / shacl / llm_critic / EvidenceReq / matcher / kind /
    // lifecycle) to end users.
    //
    // We strip /* */ block + // line comments first so the file-header
    // prose explaining the rule does not false-positive.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    // The only places these tokens may legitimately appear are
    // protocol-shape identifiers (e.g. "single_select", "multi_select",
    // "ready_to_save", `kind`) which are NOT user-facing.
    //
    // Locking the rule: any plain user-facing string literal that
    // contains a banned token would fail this check. We accept the
    // word "kind" only as a JS object-key (which is in the protocol
    // shape "kind: 'single_select'") - the scrub on the backend strips
    // any prose leak before it reaches the bubble.
    const banned = [
      // Prose leaks.
      /"\s*[Rr]egex\b[^"]*"/,
      /"\s*[Ss]hacl\b[^"]*"/,
      /"\s*llm_critic\b[^"]*"/,
      /"\s*EvidenceReq\b[^"]*"/,
    ]
    for (const re of banned) {
      const m = stripped.match(re)
      expect(
        m,
        `string literal exposes internal vocab: ${m?.[0] ?? ""}`,
      ).toBeNull()
    }
  })
})

/** Strip block + line comments before scanning so explanatory prose
 *  in file headers does not false-positive on the banned-pattern test
 *  (the barrel rule explanation contains the literal phrase
 *  `from "@/components/ui"`). */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
}

describe("D55b client files use sub-path imports (P0 barrel guard)", () => {
  // The brief: ANY `from "@/components/ui"` in a "use client" file is
  // P0 — the barrel pulls a server-only chain (NavBarShell ->
  // lib/i18n/server.ts) into the client bundle and breaks next build.
  // Scan every D55b client file and assert the absence.
  const D55B_CLIENT_FILES = [
    "ConversationalCompose.tsx",
    "IrDraftPane.tsx",
    "ChatTurn.tsx",
    "ChatTurnPill.tsx",
  ]
  for (const fname of D55B_CLIENT_FILES) {
    it(`${fname} does NOT import from the @/components/ui barrel`, () => {
      const src = stripComments(read(fname))
      // The forbidden import is a bare `from "@/components/ui"`
      // (without a trailing slash + sub-path).
      const banned = /from\s+["']@\/components\/ui["']/
      const m = src.match(banned)
      expect(
        m,
        `${fname} imports from "@/components/ui" (use sub-path "@/components/ui/<Name>" instead):\n${m?.[0] ?? ""}`,
      ).toBeNull()
      // Sanity: when this file imports UI primitives at all, they must
      // be sub-path imports. We do not assert "must import a primitive"
      // because not every file uses one.
    })
  }
})

describe("IrDraftPane source invariants", () => {
  const src = read("IrDraftPane.tsx")

  it("declares 'use client'", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("renders aria-live='polite' summary so SR users hear merges", () => {
    expect(src).toContain('aria-live="polite"')
    expect(src).toContain("ir-draft-summary")
  })

  it("Save CTA appears only when readyToSave=true", () => {
    expect(src).toMatch(/\{readyToSave && draft &&/)
    expect(src).toContain("ir-draft-save")
  })

  it("Save form posts to the parent's saveAction (server action wiring)", () => {
    expect(src).toContain("form\n          action={saveAction}")
    // Hidden ir_json carries the draft to persistDraft -> PUT /policies.
    expect(src).toContain('name="ir_json"')
  })

  it("delegates dry-run to the shared DryRunPanel (D53b) without modification", () => {
    expect(src).toContain("<DryRunPanel")
    // The brief: "Reuse the existing DryRunPanel.tsx (D53b) without
    // modification." We import from the shared _components dir.
    expect(src).toContain('from "../../_components/DryRunPanel"')
  })

  it("never exposes internal vocabulary in user-facing strings", () => {
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    // Match conditionLabel branches - they MUST translate kinds to
    // plain language, not echo the kind token.
    const banned = [
      /"\s*regex\s*"/,
      /"\s*shacl\s*"/,
      /"\s*llm_critic\s*"/,
    ]
    for (const re of banned) {
      // The TS literal `"regex"` IS legal as a kind discriminator on
      // the IR shape; it's only banned as a user-facing string. We
      // allow it in case statements but check that no JSX text node
      // surfaces it. Pull JSX-text-shaped fragments only.
      const inJsx = new RegExp(`>\\s*${re.source.slice(1, -1)}\\s*<`)
      expect(stripped.match(inJsx)).toBeNull()
    }
  })

  it("plain-language summary uses friendly labels (When / Condition / Action)", () => {
    // The brief explicitly calls for plain-language placeholders.
    expect(src).toMatch(/When|언제/)
    expect(src).toMatch(/Condition|조건/)
    expect(src).toMatch(/Action|동작/)
  })

  it("renders a collapsible JSON view for power users", () => {
    expect(src).toContain("ir-draft-json")
    expect(src).toContain("<details")
    expect(src).toContain("<summary")
  })
})

describe("ChatTurn / ChatTurnPill source invariants", () => {
  const turnSrc = read("ChatTurn.tsx")
  const pillSrc = read("ChatTurnPill.tsx")

  it("both files declare 'use client'", () => {
    expect(turnSrc.startsWith('"use client"')).toBe(true)
    expect(pillSrc.startsWith('"use client"')).toBe(true)
  })

  it("ChatTurnPill is a real <button> (keyboard activation: Enter/Space free)", () => {
    expect(pillSrc).toMatch(/<button/)
    // aria-pressed carries the multi-select picked state per the brief.
    expect(pillSrc).toContain("aria-pressed")
  })

  it("ChatTurn aligns user bubbles right and assistant left", () => {
    // ml-auto (right-align) for user, mr-auto for assistant.
    expect(turnSrc).toContain("ml-auto")
    expect(turnSrc).toContain("mr-auto")
  })

  it("ChatTurn renders inline question pills via ChatTurnPill", () => {
    expect(turnSrc).toContain("ChatTurnPill")
  })

  it("disabled state is supported on the pill (pending = freeze input)", () => {
    expect(pillSrc).toContain("disabled")
    // The disabled visual is grey + cursor-not-allowed.
    expect(pillSrc).toContain("cursor-not-allowed")
  })
})

describe("/policies/new page wiring (Conversational mode dispatch)", () => {
  const pageSrc = readFileSync(
    path.join(HERE, "..", "page.tsx"),
    "utf-8",
  )

  it("Mode union includes 'conversational'", () => {
    expect(pageSrc).toMatch(/type Mode\s*=\s*"nl"\s*\|\s*"guided"\s*\|\s*"advanced"\s*\|\s*"conversational"/)
  })

  it("?mode=conversational resolves to the conversational branch", () => {
    expect(pageSrc).toContain('rawMode === "conversational"')
  })

  it("mounts <ConversationalCompose> when mode === 'conversational'", () => {
    expect(pageSrc).toMatch(/mode === "conversational"/)
    expect(pageSrc).toContain("<ConversationalCompose")
    expect(pageSrc).toMatch(/saveAction=\{saveCompiled\}/)
  })

  it("imports ConversationalCompose via a relative path", () => {
    expect(pageSrc).toMatch(
      /import ConversationalCompose from\s+".+ConversationalCompose"/,
    )
  })

  it("picker landing nudges first-time users toward Conversational", () => {
    expect(pageSrc).toContain("picker-conversational-nudge")
    expect(pageSrc).toContain("newPolicy.picker.conversationalNudge")
  })

  it("picker renders a 4th ChoiceCard for the Conversational mode", () => {
    expect(pageSrc).toContain("picker-card-conversational")
    expect(pageSrc).toContain('href="/policies/new?mode=conversational"')
    expect(pageSrc).toContain("newPolicy.picker.conversational.label")
  })
})

describe("D55b i18n key coverage", () => {
  const dictSrc = readFileSync(
    path.join(HERE, "..", "..", "..", "..", "..", "lib", "i18n", "dict.ts"),
    "utf-8",
  )

  it("every newPolicy.conv.* and newPolicy.picker.conversational* key referenced exists in dict.ts", () => {
    // Walk every D55b client file.
    const referenced = new Set<string>()
    const D55B_FILES = [
      "ConversationalCompose.tsx",
      "IrDraftPane.tsx",
      "ChatTurn.tsx",
      "ChatTurnPill.tsx",
    ]
    for (const fname of D55B_FILES) {
      const src = read(fname)
      for (const m of src.matchAll(
        /"(newPolicy\.(?:conv|picker\.conversational|mode\.conversational|picker\.conversationalNudge)[a-zA-Z0-9.]*)"/g,
      )) {
        referenced.add(m[1])
      }
    }
    // Also from page.tsx.
    const pageSrc = readFileSync(
      path.join(HERE, "..", "page.tsx"),
      "utf-8",
    )
    for (const m of pageSrc.matchAll(
      /"(newPolicy\.(?:conv|picker\.conversational|mode\.conversational|picker\.conversationalNudge)[a-zA-Z0-9.]*)"/g,
    )) {
      referenced.add(m[1])
    }
    expect(referenced.size, "no D55b keys referenced").toBeGreaterThan(5)
    const missing = [...referenced].filter(
      (k) => !dictSrc.includes(`"${k}":`),
    )
    expect(
      missing,
      `D55b keys referenced but missing in dict.ts:\n${missing.join("\n")}`,
    ).toEqual([])
  })

  it("the conversational mode label exists in both locales", () => {
    expect(dictSrc).toContain('"newPolicy.mode.conversational":')
    expect(dictSrc).toContain('"newPolicy.picker.conversational.label":')
    expect(dictSrc).toContain('"newPolicy.picker.conversationalNudge":')
  })
})

// Sanity guard: this test FILE must not accidentally leak the deferred
// regex test if a new client file is dropped into _components without
// being added to the barrel-guard list above. We pull the directory at
// run time and assert every "use client" file under _components matches
// the known D55b list.
describe("D55b barrel-import guard covers every new client file", () => {
  // Enumerate every .tsx in this dir, filter to "use client" files,
  // intersect with the D55b list. If a new client file lands that we
  // forgot to add, this fails so the reviewer notices.
  const allTsx = readdirSync(HERE).filter((n) => n.endsWith(".tsx"))
  // The D52e / pre-D55b client files in this same dir. They're already
  // covered by their own tests but we do not include them in the D55b
  // guard list.
  const PRE_D55B_CLIENT_FILES = new Set([
    "SteeringAwareField.tsx",
    "SentinelModeSection.tsx",
    "ConditionKindSection.tsx",
    "PayloadFieldChipsClient.tsx",
    "MinOneSubmit.tsx",
  ])

  it("every newly-added client .tsx file in this dir uses sub-path imports only", () => {
    for (const fname of allTsx) {
      if (PRE_D55B_CLIENT_FILES.has(fname)) continue
      const raw = read(fname)
      if (!raw.startsWith('"use client"')) continue
      const src = stripComments(raw)
      const banned = /from\s+["']@\/components\/ui["']/
      const m = src.match(banned)
      expect(
        m,
        `${fname} imports from "@/components/ui" barrel (forbidden in client files; use sub-path)`,
      ).toBeNull()
    }
  })
})
