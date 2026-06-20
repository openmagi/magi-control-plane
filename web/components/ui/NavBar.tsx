"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"

type NavItem = { href: string; label: string }

const ITEMS: NavItem[] = [
  { href: "/policies", label: "Policies" },
  { href: "/policies/compile", label: "Compile" },
  { href: "/verify", label: "Verify" },
  { href: "/presets", label: "Presets" },
  { href: "/hitl", label: "Review queue" },
  { href: "/ledger", label: "Audit" },
]

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/"
  // /policies/compile should mark Compile, not Policies.
  // Pick the longest matching nav href.
  const match = ITEMS
    .map(i => i.href)
    .filter(h => pathname === h || pathname.startsWith(h + "/"))
    .sort((a, b) => b.length - a.length)[0]
  return match === href
}

export default function NavBar() {
  const pathname = usePathname() || "/"
  const [open, setOpen] = useState(false)

  // Close drawer when route changes
  useEffect(() => { setOpen(false) }, [pathname])

  // Lock body scroll when drawer open
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => { document.body.style.overflow = prev }
  }, [open])

  return (
    <header className="topbar relative" role="banner">
      <Link
        href="/"
        aria-label="magi-control-plane home"
        className="font-medium text-[var(--color-text-primary)] hover:no-underline"
      >
        magi-control-plane
      </Link>

      {/* Desktop nav (≥ 768px) */}
      <nav
        aria-label="Primary"
        className="hidden md:flex gap-[18px] items-center"
      >
        {ITEMS.map(item => {
          const active = isActive(pathname, item.href)
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={
                active
                  ? "text-[var(--color-text-primary)] font-medium border-b border-[var(--color-accent)] pb-0.5 hover:no-underline"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:no-underline"
              }
            >
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* Mobile menu trigger (< 768px) */}
      <button
        type="button"
        className="md:hidden inline-flex items-center justify-center w-9 h-9 rounded-md border border-[var(--color-border-subtle)] bg-transparent"
        aria-controls="primary-nav-drawer"
        aria-expanded={open}
        aria-label={open ? "Close navigation" : "Open navigation"}
        onClick={() => setOpen(v => !v)}
      >
        {/* Hamburger / close icon (SVG, no emoji) */}
        <svg
          width="20" height="20" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          aria-hidden="true"
        >
          {open ? (
            <>
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </>
          ) : (
            <>
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </>
          )}
        </svg>
      </button>

      {/* Mobile drawer (slides from top) */}
      {open && (
        <div
          id="primary-nav-drawer"
          className="md:hidden absolute left-0 right-0 top-full z-[10] border-b border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] shadow-[var(--shadow-lg)]"
        >
          <nav aria-label="Primary mobile" className="flex flex-col p-2">
            {ITEMS.map(item => {
              const active = isActive(pathname, item.href)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={
                    "px-3 py-2 rounded-md hover:no-underline " +
                    (active
                      ? "bg-[var(--color-surface-overlay)] text-[var(--color-text-primary)] font-medium"
                      : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-overlay)] hover:text-[var(--color-text-primary)]")
                  }
                >
                  {item.label}
                </Link>
              )
            })}
          </nav>
        </div>
      )}
    </header>
  )
}
