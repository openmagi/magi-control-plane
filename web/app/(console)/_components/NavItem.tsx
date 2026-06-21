"use client"

import Link from "next/link"
import { useSelectedLayoutSegment } from "next/navigation"
import {
  ShieldCheckIcon, SparklesIcon, BookOpenIcon,
  CheckBadgeIcon, InboxIcon,
  Squares2X2Icon, DocumentTextIcon,
  ArrowDownTrayIcon,
} from "@heroicons/react/24/outline"
import { cn } from "@/lib/cn"

/**
 * Icon registry — server Sidebar passes a string key here instead of a
 * component reference. Next.js cannot serialize component references
 * across the server→client boundary, so the lookup happens in the
 * client component.
 */
const ICONS = {
  policies: ShieldCheckIcon,
  compile:  SparklesIcon,
  presets:  BookOpenIcon,
  verify:   CheckBadgeIcon,
  hitl:     InboxIcon,
  overview: Squares2X2Icon,
  ledger:   DocumentTextIcon,
  setup:    ArrowDownTrayIcon,
} as const

export type NavIconName = keyof typeof ICONS

export interface NavItemProps {
  /** href: must start with /. Matched against the top-level segment for active state. */
  href: string
  /** Visible label. */
  label: string
  /** Icon key from the ICONS registry above. */
  icon: NavIconName
  /** Optional count badge (HITL pending etc.). Hidden when null/0. */
  badge?: number | null
}

/**
 * Sidebar nav row. Highlights itself when the current route's top-level
 * segment matches its href's first segment.
 *
 * Client component because useSelectedLayoutSegment() needs the router
 * context. The parent Sidebar is server-rendered.
 */
export function NavItem({ href, label, icon, badge }: NavItemProps) {
  const segment = useSelectedLayoutSegment()
  const itemSegment = href.replace(/^\//, "").split("/")[0]
  const active = segment === itemSegment ||
    (segment === null && itemSegment === "")
  const Icon = ICONS[icon]

  return (
    <li>
      <Link
        href={href}
        prefetch={false}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group flex items-center gap-2.5 px-3 h-9 rounded-md text-sm",
          "transition-colors duration-150 ease-out cursor-pointer",
          "outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]/40",
          active
            ? "bg-[var(--color-surface-overlay)] text-[var(--color-text-primary)]"
            : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-overlay)]/60 hover:text-[var(--color-text-primary)]",
        )}
      >
        <Icon
          aria-hidden="true"
          className={cn(
            "w-4 h-4 shrink-0",
            active
              ? "text-[var(--color-text-primary)]"
              : "text-[var(--color-text-tertiary)] group-hover:text-[var(--color-text-secondary)]",
          )}
        />
        <span className="flex-1 truncate">{label}</span>
        {badge != null && badge > 0 && (
          <span
            aria-label={`${badge} pending`}
            className="ml-auto inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-medium rounded-full bg-[var(--color-review-bg)] text-[var(--color-review-fg)] tabular-nums"
          >
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </Link>
    </li>
  )
}
