"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
import { usePathname } from "next/navigation"
import { Bars3Icon, XMarkIcon } from "@heroicons/react/24/outline"
import { cn } from "@/lib/cn"

export interface SidebarClientProps {
  /** Sidebar inner content rendered server-side (<Sidebar />). */
  children: ReactNode
  /** Localised labels — we accept strings rather than refetching i18n
   * because this component must be client-side for drawer state. */
  openMenuLabel: string
  closeMenuLabel: string
  brandLabel: string
}

/**
 * Sidebar shell: desktop sticky column + mobile slide-in drawer.
 *
 * Behaviour:
 * - Desktop (≥768px): sidebar always visible as a sticky left column.
 *   Hamburger button hidden. Backdrop hidden.
 * - Mobile (<768px): sidebar starts translated off-screen. Hamburger
 *   in the mobile header opens the drawer; backdrop tap / ESC /
 *   route-change closes it.
 * - Body scroll is locked while the drawer is open.
 * - `prefers-reduced-motion` collapses the slide transition to 0ms
 *   (handled by globals.css media query, no JS check needed here).
 */
export function SidebarClient({
  children, openMenuLabel, closeMenuLabel, brandLabel,
}: SidebarClientProps) {
  const [open, setOpen] = useState(false)
  const pathname = usePathname()
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  // Close drawer on route change.
  useEffect(() => { setOpen(false) }, [pathname])

  // Body scroll lock + page content inert while drawer open.
  // Background content gets aria-hidden so screen readers don't see it,
  // and inert so Tab order skips it (cheap focus trap without full
  // tabindex management).
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    const main = document.getElementById("main-content")
    main?.setAttribute("aria-hidden", "true")
    main?.setAttribute("inert", "")
    return () => {
      document.body.style.overflow = prev
      main?.removeAttribute("aria-hidden")
      main?.removeAttribute("inert")
    }
  }, [open])

  // ESC closes drawer.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    return () => { window.removeEventListener("keydown", onKey) }
  }, [open])

  // Move focus to the close button when the drawer opens so keyboard
  // users land somewhere actionable.
  useEffect(() => {
    if (open) closeButtonRef.current?.focus()
  }, [open])

  return (
    <>
      {/* Mobile header — hidden ≥md (desktop sidebar handles it). */}
      <header
        className="md:hidden sticky top-0 z-30 flex items-center gap-3 h-[var(--header-height)] px-4 border-b border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)]"
        role="banner"
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label={openMenuLabel}
          aria-expanded={open}
          aria-controls="primary-nav-drawer"
          className="inline-flex items-center justify-center w-9 h-9 -ml-2 rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-base)] hover:text-[var(--color-text-primary)] cursor-pointer transition-colors duration-150"
        >
          <Bars3Icon aria-hidden="true" className="w-5 h-5" />
        </button>
        <span className="font-medium text-[var(--color-text-primary)]">
          {brandLabel}
        </span>
      </header>

      {/* Backdrop (mobile drawer only). */}
      <div
        aria-hidden="true"
        onClick={() => setOpen(false)}
        className={cn(
          "md:hidden fixed inset-0 z-40 bg-black/40",
          "transition-opacity duration-200 ease-out",
          open
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none",
        )}
      />

      {/* Sidebar element: desktop sticky column / mobile slide-in drawer. */}
      <aside
        id="primary-nav-drawer"
        aria-label={brandLabel}
        aria-modal={open ? "true" : undefined}
        role={open ? "dialog" : undefined}
        className={cn(
          "w-[var(--sidebar-width)] shrink-0 bg-[var(--color-surface-raised)]",
          "border-r border-[var(--color-border-subtle)]",
          // Desktop: sticky column inside the flex layout
          "md:sticky md:top-0 md:h-screen md:translate-x-0",
          // Mobile: fixed slide-in drawer
          "fixed left-0 top-0 z-50 h-full",
          "transition-transform duration-200 ease-out",
          open
            ? "translate-x-0"
            : "-translate-x-full md:translate-x-0",
        )}
      >
        {/* Close button visible only inside drawer (mobile). */}
        <button
          ref={closeButtonRef}
          type="button"
          onClick={() => setOpen(false)}
          aria-label={closeMenuLabel}
          style={{ width: 44, height: 44 }}
          className="md:hidden absolute top-2 right-2 inline-flex items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-surface-base)] hover:text-[var(--color-text-primary)] cursor-pointer transition-colors duration-150 z-10"
        >
          <XMarkIcon aria-hidden="true" style={{ width: 20, height: 20 }} />
        </button>
        {children}
      </aside>
    </>
  )
}
