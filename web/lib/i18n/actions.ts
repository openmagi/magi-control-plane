"use server"
import { cookies, headers } from "next/headers"
import { redirect } from "next/navigation"
import { LOCALES, LOCALE_COOKIE, type Locale } from "./dict"

/** Server action: write the locale cookie and stay on the same path. */
export async function setLocale(formData: FormData) {
  const value = String(formData.get("locale") ?? "")
  if (!LOCALES.includes(value as Locale)) {
    redirect("/")
  }
  cookies().set(LOCALE_COOKIE, value, {
    path: "/",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 365,
    httpOnly: false,  // client doesn't need it but cookie is non-sensitive
  })
  const ref = headers().get("referer")
  if (ref) {
    try {
      const url = new URL(ref)
      redirect(url.pathname + url.search)
    } catch {
      /* fall through */
    }
  }
  redirect("/")
}
