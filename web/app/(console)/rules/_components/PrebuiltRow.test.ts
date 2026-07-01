import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Q94: PrebuiltRow gains a "View source" button that opens a modal
 * showing the prebuilt's underlying Policy IR JSON. Operators want to
 * inspect what the prebuilt actually does before enabling it.
 *
 * Source-level invariants:
 *   1. PrebuiltRow renders a button labelled by rules.prebuilt.viewSource
 *      + aria-label by rules.prebuilt.viewSourceAria.
 *   2. The button sits inside the same control block as the toggle +
 *      Setup / Edit links (i.e. siblings, not a row-level overlay).
 *   3. Clicking the button opens a PrebuiltSourceDialog instance for
 *      that row; the dialog is a pure client component using the
 *      native <dialog> element + showModal() for native focus trap.
 *   4. The dialog renders the IR via JSON.stringify(entry.ir, null, 2)
 *      inside the design system <CodeBlock> primitive.
 *   5. Copy-to-clipboard is wired via the canonical <CopyButton>.
 *   6. Focus is restored to the trigger button on close (the row owns
 *      a ref to the trigger and passes it to the dialog).
 *   7. KO + EN i18n keys exist for viewSource / viewSourceAria /
 *      viewSourceTitle / viewSourceClose / viewSourceCloseAria.
 *   8. Components take a `locale` prop, NOT a `t` closure.
 *   9. Imports are sub-path imports only (no barrel imports from
 *      @/components/ui).
 */
describe("PrebuiltRow + PrebuiltSourceDialog source invariants (Q94)", () => {
  const rowSrc = readFileSync(
    path.join(__dirname, "PrebuiltRow.tsx"), "utf-8",
  )
  const dialogSrc = readFileSync(
    path.join(__dirname, "PrebuiltSourceDialog.tsx"), "utf-8",
  )
  const dictSrc = readFileSync(
    path.join(__dirname, "..", "..", "..", "..", "lib", "i18n", "dict.ts"),
    "utf-8",
  )

  // ── Trigger button on the row ──────────────────────────────────
  it("PrebuiltRow renders a View source menu item that opens the dialog", () => {
    // D82e: View source moved from a sibling button into the kebab
    // menu. The i18n key + dialog wiring still lives here.
    expect(rowSrc).toContain("rules.prebuilt.viewSource")
    expect(rowSrc).toMatch(/setSourceOpen\(true\)/)
    expect(rowSrc).toContain("PrebuiltSourceDialog")
  })

  it("D82e: secondary actions live inside a kebab menu, not inline on the row", () => {
    // Screenshot review flagged the D82d layout (three inline actions
    // per row) as UI-overwhelming. D82e collapses the row to
    // identity + status + toggle + one kebab; secondary actions
    // (Details / View source / Setup or Edit) live behind the kebab.
    expect(rowSrc).toContain("rules.prebuilt.moreAria")
    expect(rowSrc).toMatch(/aria-haspopup="menu"/)
    expect(rowSrc).toContain('role="menu"')
    expect(rowSrc).toContain('role="menuitem"')
    // Kebab item that opens the source dialog.
    expect(rowSrc).toMatch(/setSourceOpen\(true\);\s*setMenuOpen\(false\)/)
  })

  it("PrebuiltRow passes a triggerRef to the dialog so focus restores on close", () => {
    expect(rowSrc).toContain("viewSourceTriggerRef")
    expect(rowSrc).toMatch(/useRef<HTMLButtonElement>/)
    expect(rowSrc).toMatch(/ref=\{viewSourceTriggerRef\}/)
    expect(rowSrc).toMatch(/triggerRef=\{viewSourceTriggerRef\}/)
  })

  it("PrebuiltRow imports PrebuiltSourceDialog from its sibling file", () => {
    expect(rowSrc).toContain('from "./PrebuiltSourceDialog"')
  })

  // ── Dialog component ───────────────────────────────────────────
  it("PrebuiltSourceDialog is a 'use client' component taking a locale prop", () => {
    expect(dialogSrc.startsWith('"use client"')).toBe(true)
    expect(dialogSrc).toMatch(/locale:\s*Locale/)
    // The dialog derives `t` from locale via translate() like every
    // other client component in this repo (no `t` closure passed in
    // from the parent).
    expect(dialogSrc).toContain("translate(locale,")
    // Pin the prop names: the only props are entry/open/onClose/
    // locale/triggerRef. Any new prop should be a deliberate change.
    expect(dialogSrc).toMatch(/entry,\s*open,\s*onClose,\s*locale,\s*triggerRef,/)
  })

  it("PrebuiltSourceDialog uses a native <dialog> with showModal() for focus trap", () => {
    expect(dialogSrc).toContain("<dialog")
    expect(dialogSrc).toContain("HTMLDialogElement")
    expect(dialogSrc).toContain("showModal()")
    // close() drives the imperative close path; the native dialog
    // handles Escape + backdrop + focus trap for free.
    expect(dialogSrc).toMatch(/dlg\.close\(\)|dialogRef\.current\?\.close\(\)/)
  })

  it("PrebuiltSourceDialog listens for the native close event so onClose fires for any close path", () => {
    expect(dialogSrc).toContain('addEventListener("close"')
    expect(dialogSrc).toContain("onClose")
  })

  it("PrebuiltSourceDialog explicitly restores focus to the trigger on close", () => {
    expect(dialogSrc).toContain("triggerRef")
    expect(dialogSrc).toMatch(/trigger.*\.focus\(\)/)
  })

  it("PrebuiltSourceDialog renders the IR via JSON.stringify inside <CodeBlock>", () => {
    expect(dialogSrc).toMatch(/JSON\.stringify\(entry\.ir,\s*null,\s*2\)/)
    expect(dialogSrc).toContain("<CodeBlock")
    expect(dialogSrc).toContain('from "@/components/ui/Code"')
  })

  it("PrebuiltSourceDialog wires copy-to-clipboard via the canonical <CopyButton>", () => {
    expect(dialogSrc).toContain("<CopyButton")
    expect(dialogSrc).toContain('from "@/components/ui/CopyButton"')
  })

  it("PrebuiltSourceDialog backdrop click closes the dialog", () => {
    // Click on the dialog element itself (i.e. the ::backdrop area)
    // triggers close(); clicks on inner content do not.
    expect(dialogSrc).toMatch(/e\.target\s*===\s*dialogRef\.current/)
  })

  it("PrebuiltSourceDialog renders a close button + the IR title from i18n", () => {
    expect(dialogSrc).toContain("rules.prebuilt.viewSourceTitle")
    expect(dialogSrc).toContain("rules.prebuilt.viewSourceCloseAria")
  })

  it("PrebuiltSourceDialog labels itself for aria via aria-labelledby + the title id", () => {
    expect(dialogSrc).toContain("aria-labelledby={titleId}")
    expect(dialogSrc).toContain("useId()")
  })

  // ── i18n drift gate ────────────────────────────────────────────
  it("KO + EN dictionaries declare every new viewSource key", () => {
    const keys = [
      "rules.prebuilt.viewSource",
      "rules.prebuilt.viewSourceAria",
      "rules.prebuilt.viewSourceTitle",
      "rules.prebuilt.viewSourceClose",
      "rules.prebuilt.viewSourceCloseAria",
    ]
    for (const key of keys) {
      // The dict file declares KO_RAW first then EN; both blocks must
      // contain the key as a quoted literal.
      const matches = dictSrc.match(new RegExp(`"${key.replace(/\./g, "\\.")}"`, "g"))
      expect(matches?.length ?? 0).toBeGreaterThanOrEqual(2)
    }
  })

  it("KO viewSource copy is the operator-facing '소스 보기'", () => {
    expect(dictSrc).toContain('"rules.prebuilt.viewSource": "소스 보기"')
  })

  it("EN viewSource copy is 'View source'", () => {
    expect(dictSrc).toContain('"rules.prebuilt.viewSource": "View source"')
  })
})
