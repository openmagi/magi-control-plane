import { setLocale } from "@/lib/i18n/actions"
import { getT } from "@/lib/i18n/server"

/**
 * Locale switcher. Two small pill buttons (KO / EN), each its own
 * submit. Clicking either pill calls setLocale via a server action and
 * the page re-renders in the picked locale. No "pick + confirm"
 * handshake, no styled native <select> that fights the surrounding
 * surface.
 */
export default async function LangSelect() {
  const { locale, t } = await getT()
  return (
    <form action={setLocale} className="inline-flex items-center rounded-lg border border-[var(--color-border-strong)] bg-white shadow-sm overflow-hidden">
      <span className="sr-only">{t("nav.locale.label")}</span>
      <LangPill value="ko" active={locale === "ko"} label="KO" />
      <span aria-hidden="true" className="w-px h-5 bg-[var(--color-border-subtle)]" />
      <LangPill value="en" active={locale === "en"} label="EN" />
    </form>
  )
}

function LangPill({ value, active, label }: { value: string; active: boolean; label: string }) {
  return (
    <button
      type="submit"
      name="locale"
      value={value}
      aria-pressed={active}
      className={
        "h-7 px-2.5 text-[11px] font-semibold tracking-wide cursor-pointer transition-colors " +
        (active
          ? "bg-[var(--color-accent)] text-white"
          : "bg-white text-[var(--color-text-secondary)] hover:bg-black/[0.04] hover:text-[var(--color-text-primary)]")
      }
    >
      {label}
    </button>
  )
}
