import "server-only"
import { cookies, headers } from "next/headers"
import {
  DEFAULT_LOCALE, LOCALES, LOCALE_COOKIE, translate, type Locale, type TKey,
} from "./dict"

/** Pick locale from cookie → Accept-Language → DEFAULT_LOCALE. */
function pickLocale(): Locale {
  const cookieVal = cookies().get(LOCALE_COOKIE)?.value
  if (cookieVal && LOCALES.includes(cookieVal as Locale)) {
    return cookieVal as Locale
  }
  const accept = headers().get("accept-language") ?? ""
  // very small parser: pick the first language whose primary subtag matches a supported locale
  const primary = accept.split(",")[0]?.split(";")[0]?.trim().toLowerCase() ?? ""
  if (primary.startsWith("ko")) return "ko"
  if (primary.startsWith("en")) return "en"
  return DEFAULT_LOCALE
}

/** Server-component translator. Capture once per page. */
export async function getT(): Promise<{
  locale: Locale
  t: (key: TKey, vars?: Record<string, string | number>) => string
}> {
  const locale = pickLocale()
  return {
    locale,
    t: (key, vars) => translate(locale, key, vars),
  }
}

/** For places that only need the locale string. */
export function getLocale(): Locale {
  return pickLocale()
}

/** Intl helpers bound to the current locale. */
export async function getIntl(): Promise<{
  locale: Locale
  nf: Intl.NumberFormat
  dtf: Intl.DateTimeFormat
}> {
  const locale = pickLocale()
  const tag = locale === "ko" ? "ko-KR" : "en-US"
  return {
    locale,
    nf: new Intl.NumberFormat(tag),
    dtf: new Intl.DateTimeFormat(tag, {
      dateStyle: "medium", timeStyle: "medium", timeZone: "UTC",
    }),
  }
}
