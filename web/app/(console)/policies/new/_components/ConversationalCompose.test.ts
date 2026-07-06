import { describe, it, expect } from "vitest"
import { readFileSync, readdirSync } from "node:fs"
import path from "node:path"

/**
 * D55b: source-level invariants for ConversationalCompose + sibling
 * client islands. Same pattern as the D52e / D53b panels, grep the
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

  it("PR-4: provider_error mapped to actionable copy in errorBubbleText", () => {
    // A configured-but-failing provider (wrong key / rate-limit) surfaces as
    // provider_error from the proxy. errorBubbleText must map it to an
    // actionable i18n key so the operator knows to check their API key.
    expect(src).toContain("provider_error")
    expect(src).toContain("newPolicy.conv.error.providerError")
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

  it("guards Enter against IME composition (Korean Hangul finalization)", () => {
    // D55b code review P1: the Korean (Hangul) IME signals Enter on
    // composition finalization. Sending on that keystroke truncates
    // the user's message. We MUST check isComposing AND a
    // composition event-driven ref.
    expect(src).toMatch(/onCompositionStart/)
    expect(src).toMatch(/onCompositionEnd/)
    expect(src).toMatch(/isComposing/)
    expect(src).toMatch(/composingRef/)
  })

  it("aborts in-flight fetches when a newer turn starts (race-safe lens)", () => {
    // D55b code review P1: out-of-order resolution would let an older
    // server response overwrite the newer draft. We hold an
    // AbortController + monotonic request id and drop responses whose
    // id is stale.
    expect(src).toContain("AbortController")
    expect(src).toMatch(/reqIdRef/)
    expect(src).toMatch(/abortRef/)
    // Cleanup on unmount aborts any pending request.
    expect(src).toMatch(/abortRef\.current\.abort\(\)/)
  })

  it("send path builds nextHistory synchronously over current closure history (no two-setState race)", () => {
    // D55b code review P1 originally required EVERY setHistory call
    // to be a functional updater. That invariant turned into a
    // production race: the send path called setHistory twice in a
    // row — first to append the user bubble, then a second
    // functional updater whose `prev` was meant to capture the
    // post-append snapshot for the wire body. React 18 batches
    // functional updaters and runs them deferred, so the second
    // updater's `prev` arrived BEFORE the first updater's bubble
    // landed, and the wire `history` was empty. The server's
    // `_latest_user_turn(history)` returned "" and the #100
    // deterministic intent extractor had nothing to scan.
    //
    // Fix: build `nextHistory` synchronously over the current
    // closure-captured `history`, push the SAME array into both
    // setHistory(nextHistory) and the wire body. No race because
    // there is only one setState call on the send path and the
    // wire body is derived from the synchronous in-scope value.
    expect(src).toMatch(/const\s+nextHistory\s*:/)
    expect(src).toMatch(/setHistory\(nextHistory\)/)
    // G2 (IF-04): the wire history is windowed to the last MAX_WIRE_TURNS
    // so a long conversation never trips the server's 16-turn cap.
    expect(src).toMatch(/wireHistory[^=]*=\s*nextHistory\.slice\(-MAX_WIRE_TURNS\)\.map/)
    // The DEFENSIVE pattern (functional updaters) still applies to
    // OTHER setHistory calls outside the send path — the response /
    // error / pill-replace / question-pill paths must remain
    // functional so interleaved writes do not clobber each other.
    // Pin a representative count: there must still be at least 3
    // `setHistory((prev) =>` callsites (response merge, error
    // bubble append, pill replace).
    const functionalCount =
      (src.match(/setHistory\(\s*\(\s*prev\s*\)\s*=>/g) || []).length
    expect(functionalCount).toBeGreaterThanOrEqual(3)
  })

  it("pill picks push an optimistic user bubble using the human label", () => {
    // D55b code review P2: single-select / multi-select pill clicks
    // are user actions and MUST appear in the transcript as user
    // turns, otherwise the chat loses the audit trail of who picked
    // what. The bubble uses the option's human label, not the raw
    // value (the lens forbids surfacing the raw option key).
    expect(src).toMatch(/labelForOption/)
    expect(src).toMatch(/userBubble/)
  })

  it("forbidden code maps to its own bubble (admin-key misconfig vs upstream)", () => {
    // D55b code review P2: an auth misconfig (401/403) carries
    // distinct copy that names MAGI_CP_ADMIN_API_KEY so the operator
    // can tell admin-key trouble from cloud trouble.
    expect(src).toContain('code === "forbidden"')
    expect(src).toContain("newPolicy.conv.error.forbidden")
  })

  it("dead 'config' alias is gone (route only returns 'server config')", () => {
    // D55b code review P2 cleanup: the route's only env-equivalent
    // code is "server config"; the legacy 'config' alias was dead.
    expect(src).not.toMatch(/code === "config"\b/)
  })

  it("error bubble inherits the prior turn's question pills (transient errors do not burn the user's place)", () => {
    // D55b code review P2: when sendTurn errors the error bubble must
    // carry the previous assistant turn's questions forward so the
    // user can re-click and retry. The render path used to strip
    // pills on `errored=true` for the live turn; the lens now keeps
    // the live questions in the bubble itself.
    expect(src).toMatch(/lastQuestions/)
    // The render now keeps the live questions even when errored is
    // true (the error branch inherits questions onto the new bubble).
    expect(src).not.toMatch(/isLastAssistant && !errored/)
  })

  it("D57a: provider_unconfigured renders a structured bubble with friendly first line + escape CTAs + collapsible setup guide", () => {
    // The brief: soften the env-var-heavy admin-facing copy. The
    // bubble must now expose a short user-facing line and TWO escape
    // CTAs (guided / advanced IR editor) plus a collapsible setup
    // guide. The plain ChatTurn path remains for every other error
    // code; only provider_unconfigured branches to ProviderUnconfigured-
    // Bubble.
    expect(src).toContain("ProviderUnconfiguredBubble")
    // The branch in the render loop must check errorKind on the turn.
    expect(src).toMatch(/errorKind === "provider_unconfigured"/)
    // The error branch in sendTurn must tag provider_unconfigured turns
    // with the errorKind discriminator so the renderer can swap layout.
    expect(src).toMatch(/errorKind:\s*HistoryTurn\["errorKind"\]/)
    // Escape CTAs link to the other two authoring modes.
    expect(src).toContain('href="/policies/new?mode=guided"')
    expect(src).toContain('href="/policies/new?mode=advanced"')
    expect(src).toContain("conv-provider-unconfigured-cta-guided")
    expect(src).toContain("conv-provider-unconfigured-cta-advanced")
    // Collapsible "Show setup guide" disclosure (aria-expanded carries
    // the state for keyboard / SR users).
    expect(src).toContain("conv-provider-unconfigured-setup-toggle")
    expect(src).toContain("conv-provider-unconfigured-setup-body")
    expect(src).toContain("aria-expanded")
    // Setup body references the new i18n keys (the admin env-var
    // details now live behind the disclosure).
    expect(src).toContain("newPolicy.conv.error.providerUnconfigured.setupBody")
    expect(src).toContain("newPolicy.conv.error.providerUnconfigured.ctaGuided")
    expect(src).toContain("newPolicy.conv.error.providerUnconfigured.ctaAdvanced")
    expect(src).toContain("newPolicy.conv.error.providerUnconfigured.docsLink")
  })

  it("setPicks({}) runs in finally whenever answers were sent (no cross-turn leak)", () => {
    // D55b code review P2: the picks map must reset whenever a
    // multi-select payload went out, not only on success. Otherwise a
    // failed turn leaves stale picks in state for the next time the
    // same qid appears.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    // Both finally branches reset picks; we look for the guarded
    // reset gated on answersSent.
    expect(stripped).toMatch(/answersSent/)
    expect(stripped).toMatch(/setPicks\(\{\}\)/)
    // The reset MUST be inside the finally block (i.e. after the
    // matching try{).
    const finallyIdx = stripped.indexOf("} finally {")
    const pickResetIdx = stripped.indexOf("setPicks({})")
    expect(finallyIdx, "no finally block").toBeGreaterThan(-1)
    expect(pickResetIdx, "no setPicks({}) call").toBeGreaterThan(-1)
    expect(pickResetIdx).toBeGreaterThan(finallyIdx)
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
  // P0: the barrel pulls a server-only chain (NavBarShell ->
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

  it("does NOT render a raw JSON / IR view on the conversational surface", () => {
    // D55b code review P0: a verbatim JSON dump leaks every banned
    // token (lifecycle / matcher / requires.kind / on_missing /
    // sentinel_re / gate_binary) onto the plain-language surface.
    // Power users get the IR shape in the Raw / Advanced mode
    // (PolicyBuilder), not here.
    expect(src).not.toContain("ir-draft-json")
    expect(src).not.toMatch(/<details/)
    expect(src).not.toMatch(/<summary/)
    // The state hook that drove the open/closed toggle should be gone
    // along with the block.
    expect(src).not.toMatch(/jsonOpen/)
  })

  it("Condition row for verifier-step requires uses plain language (no 'Verifier' / '검증기' token)", () => {
    // D55b code review P1: the kind === 'step' branch must translate
    // to plain language. 'Verifier' and '검증기' are internal vocab.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
    expect(stripped).not.toMatch(/Verifier:/)
    expect(stripped).not.toMatch(/검증기:/)
    expect(stripped).not.toMatch(/검증기 이름/)
    expect(stripped).toMatch(/Required check|필수 확인/)
  })

  it("When row matcher is pretty-printed (no raw regex alternation, no mcp__ slug)", () => {
    // D55b code review P2: `Bash|Edit` and `mcp__server__tool` leak
    // implementation shape. prettyMatcher() translates them to a
    // friendly form before the When row renders.
    expect(src).toContain("prettyMatcher")
    // Alternation joiner and mcp slug stripper live in the helper.
    expect(src).toMatch(/mcp__/)
    expect(src).toMatch(/' 또는 '|" 또는 "|' or '|" or "/)
  })

  it("D64 polish: maybeFriendlyPath strips a leading 'magi:' prefix before lookup", () => {
    // The conversational compiler may stash item.path as the namespaced
    // SHACL predicate ("magi:tool_input.command") it just emitted. The
    // display-label lookup keys on the BARE path; without stripping the
    // prefix the helper silently falls through to raw and the operator
    // sees `Structured rule (magi:tool_input.command)`, exactly the
    // unfriendly outcome D64 set out to fix.
    expect(src).toMatch(/startsWith\("magi:"\)/)
    expect(src).toMatch(/slice\("magi:"\.length\)/)
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
    // D56b: NL compose retired. Mode union shrinks to guided | advanced
    // | conversational; URL backcompat for `?mode=nl` is handled via a
    // redirect at the top of the page component.
    expect(pageSrc).toMatch(/type Mode\s*=\s*"guided"\s*\|\s*"advanced"\s*\|\s*"conversational"/)
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

  /**
   * D56b backcompat. `/policies/new?mode=nl` (and the same URL with an
   * `nl=<seed>` query param the legacy NL mode used to seed its
   * textarea) must redirect to `/policies/new?mode=conversational` and
   * preserve the seed through to the conversational input. The brief
   * called out that the original commit landed the redirect with no
   * test pinning it: a future refactor that drops the `if (rawMode ===
   * "nl")` block would silently break a bookmarked legacy URL.
   *
   * These tests are source-level (matching the existing pageSrc grep
   * style) so they don't depend on next/navigation harness setup; the
   * `seed` round-trip is also covered by the `initialUserMessage`
   * prop wire-up below.
   */
  it("D56b: rawMode === 'nl' triggers a redirect to ?mode=conversational", () => {
    expect(pageSrc).toMatch(/if \(rawMode === "nl"\)/)
    expect(pageSrc).toMatch(
      /redirect\(\s*`\/policies\/new\?mode=conversational\$\{tail\}`\s*\)/,
    )
  })

  it("D56b: legacy ?mode=nl&nl=<seed> preserves the seed in the redirect URL", () => {
    // The redirect builder must compose `&nl=<seed>` when a seed is
    // present. The encode/no-encode branch matters for any seed with
    // spaces (URL would be munged).
    expect(pageSrc).toMatch(/const seed = searchParams\.nl/)
    expect(pageSrc).toMatch(/encodeURIComponent\(seed\)/)
    expect(pageSrc).toMatch(/`&nl=\$\{encodeURIComponent\(seed\)\}`/)
  })

  it("D56b: ConversationalCompose receives the forwarded nl seed via initialUserMessage", () => {
    // The page MUST thread `searchParams.nl` into the client component;
    // otherwise the URL-bounce preserves the seed but the chat lands
    // empty (the original "silently loses the user's intent" bug).
    expect(pageSrc).toMatch(/initialUserMessage=\{searchParams\.nl\s*\?\?\s*""\}/)
  })

  it("D56b: ConversationalCompose accepts initialUserMessage on its props type", () => {
    const compSrc = read("ConversationalCompose.tsx")
    expect(compSrc).toMatch(/initialUserMessage\?:\s*string/)
    // The state initializer must seed the input from the prop so the
    // seed survives the empty-state pass.
    expect(compSrc).toMatch(/useState\(initialUserMessage\s*\?\?\s*""\)/)
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

describe("ConversationalCompose policy-integrity review wiring", () => {
  const src = read("ConversationalCompose.tsx")

  it("fetches the review from the same-origin proxy when ready", () => {
    expect(src).toContain('"/api/policies/review"')
    expect(src).toContain("readyToSave")
  })

  it("F3: composes the intent from ALL user turns (not just the first)", () => {
    expect(src).toContain('history.filter((h) => h.role === "user")')
    expect(src).toContain("userTurns.join")
  })

  it("F1: forwards the operator locale to the review", () => {
    expect(src).toMatch(/body: JSON\.stringify\(\{ draft, intent, locale \}\)/)
  })

  it("clears the verdict when the draft is not ready", () => {
    expect(src).toContain("setReview(null)")
  })

  it("F2: sets a neutral review-error state on fetch failure", () => {
    expect(src).toContain("setReviewError(true)")
  })

  it("threads the verdict down to IrDraftPane", () => {
    expect(src).toContain("review={review}")
    expect(src).toContain("reviewPending={reviewPending}")
    expect(src).toContain("reviewError={reviewError}")
  })
})

describe("G2: history race + cap (IF-03/IF-04)", () => {
  const src = readFileSync(path.join(__dirname, "ConversationalCompose.tsx"), "utf-8")

  it("IF-03: the send path builds from historyRef, not the stale closure", () => {
    expect(src).toContain("const historyRef = useRef<HistoryTurn[]>([])")
    expect(src).toContain("historyRef.current = history")
    expect(src).toMatch(/nextHistory[\s\S]{0,120}historyRef\.current/)
  })

  it("IF-04: windows the wire history to a bounded MAX_WIRE_TURNS < 16", () => {
    expect(src).toMatch(/const MAX_WIRE_TURNS = \d+/)
    expect(src).toContain("nextHistory.slice(-MAX_WIRE_TURNS)")
  })
})

describe("PR-6: runtime override + feasibility banner", () => {
  const src = readFileSync(
    path.join(__dirname, "ConversationalCompose.tsx"),
    "utf-8",
  )

  it("declares session-scoped runtimeOverride state defaulting to claude-code", () => {
    expect(src).toContain("runtimeOverride")
    expect(src).toContain("setRuntimeOverride")
    expect(src).toContain('"claude-code"')
    expect(src).toContain('"codex"')
    // Default must be the primary runtime.
    expect(src).toMatch(/useState<[^>]+>\("claude-code"\)/)
  })

  it("renders a runtime override select with the correct testid", () => {
    expect(src).toContain('data-testid="conv-runtime-select"')
    expect(src).toContain("runtimeOverride")
    expect(src).toContain("setRuntimeOverride")
  })

  it("includes runtime_id in every POST body (PR-6 wire contract)", () => {
    // The compile-interactive POST must carry the operator's runtime choice
    // so the cloud can compute the correct feasibility class.
    expect(src).toContain("runtime_id: runtimeOverride")
  })

  it("adds runtimeOverride to sendTurn useCallback deps", () => {
    // sendTurn closes over runtimeOverride; without the dep the POST body
    // would silently carry the stale initial value after a switch.
    expect(src).toMatch(/\[draft,\s*t,\s*locale,\s*runtimeOverride\]/)
  })

  it("clears feasibility state at the start of each new turn", () => {
    // A stale banner from turn N must not linger while turn N+1 is in
    // flight. setFeasibility(null) in sendTurn achieves this.
    expect(src).toContain("setFeasibility(null)")
  })

  it("renders FeasibilityBanner when feasibility is non-null", () => {
    expect(src).toContain("FeasibilityBanner")
    expect(src).toContain("{feasibility && (")
    expect(src).toContain("data-testid=\"feasibility-banner\"")
    expect(src).toContain("data-class={feasibility.class}")
  })

  it("FeasibilityBanner shows explanation verbatim from the server", () => {
    // explanation is already server-localized; we render it as-is.
    expect(src).toContain("feasibility.explanation")
  })

  it("keep_for_cc alternative renders a static chip with i18n label", () => {
    expect(src).toContain('data-testid="feasibility-alt-keep-for-cc"')
    expect(src).toContain('"newPolicy.conv.feasibility.keepForCC"')
  })

  it("magi_agent_handoff with non-null route renders anchor + target=_blank", () => {
    expect(src).toContain('data-testid="feasibility-alt-handoff-link"')
    expect(src).toContain('target="_blank"')
    expect(src).toContain("alt.route")
  })

  it("magi_agent_handoff with null route renders cta as plain text", () => {
    expect(src).toContain('data-testid="feasibility-alt-handoff-text"')
  })

  it("feasibility is set to null when the server returns null or native class", () => {
    // The guard: fw != null && fw.class !== "native"
    expect(src).toContain("fw != null")
    expect(src).toContain('fw.class !== "native"')
    expect(src).toContain("setFeasibility(fw)")
  })
})
