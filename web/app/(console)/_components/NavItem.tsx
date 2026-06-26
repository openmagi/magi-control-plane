"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  ShieldCheckIcon, SparklesIcon, BookOpenIcon,
  CheckBadgeIcon, InboxIcon,
  Squares2X2Icon, DocumentTextIcon,
  ArrowDownTrayIcon, RectangleStackIcon,
  ServerIcon, QuestionMarkCircleIcon,
  Cog6ToothIcon,
} from "@heroicons/react/24/outline"
import { cn } from "@/lib/cn"
import { longestActiveHref, useNavHrefs } from "./NavItemContext"

const ICONS = {
  policies:  ShieldCheckIcon,
  compile:   SparklesIcon,
  presets:   BookOpenIcon,
  rules:     RectangleStackIcon,
  verify:    CheckBadgeIcon,
  hitl:      InboxIcon,
  overview:  Squares2X2Icon,
  ledger:    DocumentTextIcon,
  setup:     ArrowDownTrayIcon,
  endpoints: ServerIcon,
  docs:      QuestionMarkCircleIcon,
  settings:  Cog6ToothIcon,
} as const

export type NavIconName = keyof typeof ICONS

export interface NavItemProps {
  href: string
  label: string
  icon: NavIconName
  badge?: number | null
}

/**
 * Sidebar nav row. Mirrors the magi-agent SidebarNav active pattern:
 *   active   = border border-primary/20 bg-primary/10 text-primary-light shadow-sm
 *   hover    = border-black/[0.04] bg-white text-gray-950
 *   inactive = text-gray-600
 * Icons are heroicons (already imported elsewhere) sized w-4 h-4.
 *
 * Client component because the active state needs useSelectedLayoutSegment().
 */
export function NavItem({ href, label, icon, badge }: NavItemProps) {
  const pathname = usePathname()
  const allHrefs = useNavHrefs()
  const active = longestActiveHref(pathname, allHrefs) === href
  const Icon = ICONS[icon]

  return (
    <li>
      <Link
        href={href}
        prefetch={false}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group flex min-h-10 items-center gap-3 rounded-xl px-3 text-[13px] font-semibold transition-colors duration-200 cursor-pointer",
          "outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]/30",
          active
            ? "border border-[var(--color-accent)]/20 bg-[var(--color-accent)]/10 text-[var(--color-accent-light)] shadow-sm shadow-[var(--color-accent)]/5"
            : "border border-transparent text-[var(--color-text-secondary)] hover:border-black/[0.04] hover:bg-white hover:text-[var(--color-text-primary)]",
        )}
      >
        <Icon
          aria-hidden="true"
          strokeWidth={2}
          className={cn(
            "h-4 w-4 shrink-0",
            active
              ? "text-[var(--color-accent-light)]"
              : "text-[var(--color-text-tertiary)] group-hover:text-[var(--color-text-primary)]",
          )}
        />
        <span className="flex-1 truncate">{label}</span>
        {badge != null && badge > 0 && (
          <span
            aria-label={`${badge} pending`}
            className="ml-auto inline-flex items-center justify-center min-w-[20px] h-[20px] px-1.5 text-[11px] font-semibold rounded-full bg-[var(--color-review-bg)] text-[var(--color-review-fg)] tabular-nums"
          >
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </Link>
    </li>
  )
}
