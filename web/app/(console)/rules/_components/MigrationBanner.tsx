"use client"

/**
 * P5: one-time pack-centric migration banner on the /rules Packs tab.
 *
 * When the cloud boots under the flipped-on pack-centric runtime it runs
 * a migration that moves every enabled policy into the tenant's floor
 * pack (so the same rules that fired yesterday keep firing today). This
 * banner tells the operator that happened and nudges them to split the
 * floor pack into session-scoped packs at their convenience.
 *
 * One-time: after the operator dismisses it we persist the choice to
 * localStorage so it does not reappear. Same dismissal pattern as
 * WelcomeBanner (versioned key + module-scoped session fallback for
 * storage-blocked environments).
 *
 * Sub-path import policy: client component, so the dict translator +
 * locale type come in via sub-path imports (NOT the server-only ui
 * barrel). Client components take `locale`, never a `t` closure.
 */

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import type { Locale } from "@/lib/i18n/dict"
import { translate, type TKey } from "@/lib/i18n/dict"

// Versioned key so a future banner iteration can ship under `.v2`
// without colliding with an operator who already dismissed the v1
// migration banner.
const STORAGE_KEY = "magi_cp.pack_centric_migration_dismissed.v1"

// Module-scoped fallback for environments where localStorage is
// unavailable (private mode, blocked storage). Keeps the banner
// suppressed for the rest of the SPA session when setItem throws.
let sessionDismissed = false

export interface MigrationBannerProps {
  locale: Locale
}

export function MigrationBanner({ locale }: MigrationBannerProps) {
  const t = (k: TKey) => translate(locale, k)
  // Default hidden so the banner does not flash before the localStorage
  // probe resolves.
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (sessionDismissed) return
    try {
      const dismissed = window.localStorage.getItem(STORAGE_KEY)
      if (dismissed !== "1") setVisible(true)
    } catch {
      setVisible(true)
    }
  }, [])

  const onDismiss = useCallback(() => {
    setVisible(false)
    sessionDismissed = true
    try {
      window.localStorage.setItem(STORAGE_KEY, "1")
    } catch {
      // best effort; sessionDismissed keeps it suppressed this session.
    }
  }, [])

  if (!visible) return null

  return (
    <div
      role="region"
      aria-label={t("rules.packCentric.migration.title")}
      data-testid="pack-centric-migration-banner"
      className="mb-4 flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.06] p-4"
    >
      <div className="min-w-0 flex-1">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0">
          {t("rules.packCentric.migration.title")}
        </h2>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
          {t("rules.packCentric.migration.body")}
        </p>
        <div className="mt-3">
          <Link
            href="/rules?tab=packs"
            className="inline-flex items-center rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--color-accent-light)] hover:no-underline"
          >
            {t("rules.packCentric.migration.cta")}
          </Link>
        </div>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t("rules.packCentric.migration.dismiss")}
        className="rounded-md px-2 py-1 text-xs text-[var(--color-text-tertiary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)]"
      >
        ×
      </button>
    </div>
  )
}
