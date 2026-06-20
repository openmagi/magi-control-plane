import { setLocale } from "@/lib/i18n/actions"
import { getT } from "@/lib/i18n/server"

/** Locale switcher in the NavBar. Renders as a tiny <select> + submit
 * button so it works with JavaScript off; with JS, the same form posts. */
export default async function LangSelect() {
  const { locale, t } = await getT()
  return (
    <form action={setLocale} className="flex items-center gap-1">
      <label className="sr-only" htmlFor="locale-select">
        {t("nav.locale.label")}
      </label>
      <select
        id="locale-select"
        name="locale"
        defaultValue={locale}
        className="h-7 text-xs rounded-md px-2 border border-[var(--color-border-subtle)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
        style={{
          backgroundColor: "var(--color-surface-overlay)",
          color: "var(--color-text-secondary)",
        }}
      >
        <option value="ko">{t("nav.locale.ko")}</option>
        <option value="en">{t("nav.locale.en")}</option>
      </select>
      <button
        type="submit"
        className="h-7 px-2 text-xs rounded-md border border-[var(--color-border-subtle)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-focus)] cursor-pointer"
        aria-label={t("nav.locale.label")}
      >
        ↵
      </button>
    </form>
  )
}
