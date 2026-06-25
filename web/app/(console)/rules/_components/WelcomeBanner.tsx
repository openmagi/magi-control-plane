"use client"

/**
 * D72: first-time-visitor welcome banner on the /rules Policies tab.
 *
 * Renders ONLY when the operator has no user-authored policies AND no
 * prebuilt policy is enabled (the server passes those facts in via
 * props). After the operator dismisses it once, we persist that choice
 * to localStorage so the banner does not reappear on subsequent visits
 * to a fresh-install instance.
 *
 * Storage key: magi_cp.welcome_dismissed (per the D72 spec).
 *
 * Sub-path import policy: this is a client component, so we import the
 * dict translator + locale type via sub-path imports (NOT through the
 * server-only ui barrel). Same convention as UploadScriptButton.
 */

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import type { Locale } from "@/lib/i18n/dict"
import { translate, type TKey } from "@/lib/i18n/dict"

const STORAGE_KEY = "magi_cp.welcome_dismissed"

export interface WelcomeBannerProps {
  locale: Locale
}

export function WelcomeBanner({ locale }: WelcomeBannerProps) {
  const t = (k: TKey) => translate(locale, k)
  // Default hidden so the banner does NOT flash before the localStorage
  // probe finishes (avoids a one-frame "I dismissed this yesterday" jolt).
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    try {
      const dismissed = window.localStorage.getItem(STORAGE_KEY)
      if (dismissed !== "1") setVisible(true)
    } catch {
      // Private mode / quota — default to showing (visible=false → true).
      setVisible(true)
    }
  }, [])

  const onDismiss = useCallback(() => {
    setVisible(false)
    try {
      window.localStorage.setItem(STORAGE_KEY, "1")
    } catch {
      // best effort
    }
  }, [])

  if (!visible) return null

  return (
    <div
      role="region"
      aria-label={t("rules.welcome.title")}
      data-testid="rules-welcome-banner"
      className="mb-4 flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.06] p-4"
    >
      <div className="min-w-0 flex-1">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0">
          {t("rules.welcome.title")}
        </h2>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
          {t("rules.welcome.body")}
        </p>
        <div className="mt-3">
          <Link
            href="/policies/new?mode=conversational"
            className="inline-flex items-center rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[var(--color-accent-light)] hover:no-underline"
          >
            {t("rules.welcome.cta")}
          </Link>
        </div>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t("rules.welcome.dismiss")}
        className="rounded-md px-2 py-1 text-xs text-[var(--color-text-tertiary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)]"
      >
        ×
      </button>
    </div>
  )
}
