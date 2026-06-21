import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * NavItem renders icons from a string-keyed registry (not a passed
 * ComponentType, because Next.js cannot serialize component references
 * across the server→client boundary). This test makes sure every key
 * referenced by the server Sidebar exists in the client ICONS map —
 * a string typo would otherwise crash the page at runtime instead of
 * surfacing as a TS error.
 */
describe("NavItem icon registry coverage", () => {
  const navItemSrc = readFileSync(
    path.join(__dirname, "NavItem.tsx"),
    "utf-8",
  )
  const sidebarSrc = readFileSync(
    path.join(__dirname, "Sidebar.tsx"),
    "utf-8",
  )

  it("every icon key referenced in Sidebar exists in NavItem's ICONS map", () => {
    const declared = new Set<string>()
    // Match the `key: SomeIcon,` lines inside the ICONS = {} block.
    const iconsBlock = navItemSrc.match(/const ICONS = \{([\s\S]*?)\} as const/)
    expect(iconsBlock, "ICONS block missing").toBeTruthy()
    for (const line of iconsBlock![1].split("\n")) {
      const m = line.match(/^\s*([a-z]+):\s*[A-Z]/)
      if (m) declared.add(m[1])
    }

    const referenced = new Set<string>()
    // Sidebar uses NavItem like: <NavItem icon="policies" />
    for (const m of sidebarSrc.matchAll(/icon="([a-z]+)"/g)) {
      referenced.add(m[1])
    }

    expect(declared.size, "ICONS map empty").toBeGreaterThan(0)
    expect(referenced.size, "no icon references in Sidebar").toBeGreaterThan(0)

    const missing = [...referenced].filter(k => !declared.has(k))
    expect(missing, `keys used in Sidebar but missing from ICONS:\n${missing.join("\n")}`).toEqual([])
  })
})
