"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useState, type ReactNode } from "react"
import { LogoLockup } from "./LogoLockup"

export type NavItem = { href: string; label: string }

interface Props {
  brand: string
  openMenuLabel: string
  closeMenuLabel: string
  items: NavItem[]
  rightSlot?: ReactNode
}

function isActive(pathname: string, href: string, all: string[]): boolean {
  const match = all
    .filter(h => pathname === h || pathname.startsWith(h + "/"))
    .sort((a, b) => b.length - a.length)[0]
  return match === href
}

export default function NavBarClient({
  brand, openMenuLabel, closeMenuLabel, items, rightSlot,
}: Props) {
  const pathname = usePathname() || "/"
  const [open, setOpen] = useState(false)
  const hrefs = items.map(i => i.href)

  useEffect(() => { setOpen(false) }, [pathname])

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
        aria-label={`${brand} home`}
        className="hover:no-underline"
      >
        <LogoLockup size="md" />
      </Link>

      {/* Desktop nav */}
      <nav
        aria-label="Primary"
        className="hidden md:flex gap-[18px] items-center"
      >
        {items.map(item => {
          const active = isActive(pathname, item.href, hrefs)
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
        {rightSlot && (
          <div className="ml-2 pl-3 border-l border-[var(--color-border-subtle)]">
            {rightSlot}
          </div>
        )}
      </nav>

      {/* Mobile menu trigger */}
      <button
        type="button"
        className="md:hidden inline-flex items-center justify-center w-11 h-11 rounded-md border border-[var(--color-border-subtle)] bg-transparent cursor-pointer"
        aria-controls="primary-nav-drawer"
        aria-expanded={open}
        aria-label={open ? closeMenuLabel : openMenuLabel}
        onClick={() => setOpen(v => !v)}
      >
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

      {/* Mobile drawer */}
      {open && (
        <div
          id="primary-nav-drawer"
          className="md:hidden absolute left-0 right-0 top-full z-[10] border-b border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] shadow-[var(--shadow-lg)]"
        >
          <nav aria-label="Primary mobile" className="flex flex-col p-2 gap-1">
            {items.map(item => {
              const active = isActive(pathname, item.href, hrefs)
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
            {rightSlot && (
              <div className="mt-2 pt-2 border-t border-[var(--color-border-subtle)] px-3">
                {rightSlot}
              </div>
            )}
          </nav>
        </div>
      )}
    </header>
  )
}
