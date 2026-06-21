import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * Source-level invariants for SidebarClient — guards the a11y
 * + interaction contract without a full React Testing Library setup.
 * The runtime behaviours (drawer open/close, ESC, backdrop tap) are
 * exercised manually in the running dev server during D3 + D7.
 */
describe("SidebarClient invariants", () => {
  const src = readFileSync(
    path.join(__dirname, "SidebarClient.tsx"),
    "utf-8",
  )

  it("is marked \"use client\"", () => {
    expect(src.startsWith('"use client"')).toBe(true)
  })

  it("closes the drawer on route change (usePathname dep)", () => {
    expect(src).toMatch(/usePathname/)
    expect(src).toMatch(/useEffect\(\(\) => \{ setOpen\(false\) \}, \[pathname\]\)/)
  })

  it("locks body scroll while open + restores on cleanup", () => {
    expect(src).toMatch(/document\.body\.style\.overflow = "hidden"/)
    expect(src).toMatch(/document\.body\.style\.overflow = prev/)
  })

  it("ESC closes the drawer when open", () => {
    expect(src).toMatch(/e\.key === "Escape"/)
    expect(src).toMatch(/setOpen\(false\)/)
  })

  it("hamburger button announces aria-expanded + aria-controls", () => {
    expect(src).toMatch(/aria-expanded=\{open\}/)
    expect(src).toMatch(/aria-controls="primary-nav-drawer"/)
  })

  it("aside sets role=dialog + aria-modal only while drawer open", () => {
    expect(src).toMatch(/aria-modal=\{open \? "true" : undefined\}/)
    expect(src).toMatch(/role=\{open \? "dialog" : undefined\}/)
  })

  it("hides hamburger header + backdrop ≥md (desktop)", () => {
    expect(src).toMatch(/md:hidden/)
  })

  it("desktop sidebar uses translate-x-0 (no transform animation)", () => {
    expect(src).toMatch(/md:translate-x-0/)
  })

  it("focuses the close button when drawer opens (keyboard nav)", () => {
    expect(src).toMatch(/closeButtonRef\.current\?\.focus\(\)/)
  })
})
