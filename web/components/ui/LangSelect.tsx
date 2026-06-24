import { setLocale } from "@/lib/i18n/actions"
import { getT } from "@/lib/i18n/server"

/**
 * Locale switcher. A single globe + current-language button that submits to
 * the other locale (this is a two-language site), matching the openmagi.ai
 * marketing nav switcher. Server action, no client JS, no purple toggle
 * fighting the surface.
 */
export default async function LangSelect() {
  const { locale, t } = await getT()
  const next = locale === "ko" ? "en" : "ko"
  const currentLabel = locale === "ko" ? "한국어" : "English"
  return (
    <form action={setLocale}>
      <input type="hidden" name="locale" value={next} />
      <button
        type="submit"
        aria-label={t("nav.locale.label")}
        className="flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-[var(--color-text-secondary)] transition-colors duration-200 hover:bg-black/[0.04] hover:text-[var(--color-text-primary)]"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          className="h-4 w-4"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="10" />
          <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
        <span>{currentLabel}</span>
      </button>
    </form>
  )
}
