import { getLocale, getT } from "@/lib/i18n/server"
import NavBarClient, { type NavItem } from "./NavBarClient"
import LangSelect from "./LangSelect"

/** Marketing server-side nav shell. Picks locale, builds marketing nav
 * items (anchors + install + GitHub, not authed dashboard routes), then
 * renders the client navbar with the locale switcher pinned right. */
export default async function NavBarShell() {
  const { t } = await getT()
  const locale = await getLocale()
  const isKo = locale === "ko"
  const items: NavItem[] = [
    { href: "/welcome#how", label: isKo ? "동작 방식" : "How it works" },
    { href: "/install",     label: isKo ? "설치"       : "Install" },
    { href: "/docs",        label: isKo ? "문서"       : "Docs" },
    { href: "https://github.com/openmagi", label: "GitHub" },
  ]
  return (
    <NavBarClient
      brand={t("nav.brand")}
      openMenuLabel={t("nav.openMenu")}
      closeMenuLabel={t("nav.closeMenu")}
      items={items}
      rightSlot={<LangSelect />}
    />
  )
}
