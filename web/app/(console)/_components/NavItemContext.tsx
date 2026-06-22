"use client"

import { createContext, useContext, type ReactNode } from "react"

/**
 * Lets NavItem decide its own active state by longest-prefix match
 * against the current pathname, using the full set of leaf hrefs in
 * the sidebar. Without this, `/policies/compile` would activate both
 * `/policies` and `/policies/compile` because both share the same
 * useSelectedLayoutSegment() value ("policies").
 */
const NavHrefsContext = createContext<readonly string[]>([])

export function NavHrefsProvider({
  hrefs, children,
}: { hrefs: readonly string[]; children: ReactNode }) {
  return (
    <NavHrefsContext.Provider value={hrefs}>
      {children}
    </NavHrefsContext.Provider>
  )
}

export function useNavHrefs(): readonly string[] {
  return useContext(NavHrefsContext)
}

/**
 * Returns the single href in `allHrefs` that is the *longest* prefix
 * of `pathname`. That single href wins the "active" highlight.
 *
 * - pathname `/policies`            + hrefs `["/policies","/policies/compile"]` → `/policies`
 * - pathname `/policies/compile`    + same hrefs                                → `/policies/compile`
 * - pathname `/policies/legal/v1`   + same hrefs                                → `/policies`
 * - pathname `/welcome`             + same hrefs                                → null
 */
export function longestActiveHref(
  pathname: string,
  allHrefs: readonly string[],
): string | null {
  let best: string | null = null
  for (const href of allHrefs) {
    if (pathname === href || pathname.startsWith(href + "/")) {
      if (best === null || href.length > best.length) best = href
    }
  }
  return best
}
