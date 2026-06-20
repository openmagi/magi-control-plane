import { getT } from "@/lib/i18n/server"
import NavBarClient, { type NavItem } from "./NavBarClient"
import LangSelect from "./LangSelect"

/** Server component shell — picks the locale, builds the localised nav
 * items, then renders the client navbar (which owns the mobile drawer
 * state) with the locale switcher pinned to the right. */
export default async function NavBarShell() {
  const { t } = await getT()
  const items: NavItem[] = [
    { href: "/policies",         label: t("nav.policies") },
    { href: "/policies/compile", label: t("nav.compile") },
    { href: "/verify",           label: t("nav.verify") },
    { href: "/presets",          label: t("nav.presets") },
    { href: "/hitl",             label: t("nav.reviewQueue") },
    { href: "/ledger",           label: t("nav.audit") },
    { href: "/setup",            label: t("setup.title") },
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
