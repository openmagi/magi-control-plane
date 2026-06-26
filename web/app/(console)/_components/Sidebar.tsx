import Link from "next/link"
import { getT } from "@/lib/i18n/server"
import { Logo } from "@/components/ui/Logo"
import { getWorkspaceData } from "../_data/workspace"
import { NavGroup } from "./NavGroup"
import { NavItem } from "./NavItem"
import { NavHrefsProvider } from "./NavItemContext"
import { WorkspaceCard } from "./WorkspaceCard"
import { SidebarFooter } from "./SidebarFooter"

/** Single source of truth. the longest-prefix matcher inside NavItem
 * uses this list to decide which item wins the active highlight. */
const NAV_HREFS = [
  "/rules",
  "/policies",
  "/policies/new",
  "/presets",
  "/scripts",
  "/settings",
  "/verify",
  "/hitl",
  "/overview",
  "/ledger",
  "/endpoints",
  "/shared",
  "/setup",
  "/docs",
] as const

const CLOUD_HOST = process.env.MAGI_CP_PUBLIC_CLOUD_URL
  ? new URL(process.env.MAGI_CP_PUBLIC_CLOUD_URL).host
  : process.env.MAGI_CP_CLOUD_URL
    ? new URL(process.env.MAGI_CP_CLOUD_URL).host
    : "cloud.openmagi.ai"

/**
 * Console sidebar content. Mirrors the magi-agent OSS dashboard's
 * SidebarNav structure: brand logo + workspace card + grouped nav +
 * locale switcher. The outer <aside> + responsive positioning lives
 * in SidebarClient so the server fetch happens once.
 */
export async function Sidebar() {
  const { t } = await getT()
  const { tenant, healthOk, hitlPending } = await getWorkspaceData()

  return (
    <div className="flex flex-col h-full p-5">
      <Link
        href="/overview"
        aria-label={t("nav.brand")}
        className="inline-flex hover:no-underline shrink-0"
      >
        <Logo />
      </Link>

      <WorkspaceCard
        tenantId={tenant?.synthetic ? null : (tenant?.id ?? null)}
        plan={tenant?.plan ?? null}
        healthOk={healthOk}
        host={CLOUD_HOST}
      />

      <nav className="space-y-1 flex-1 overflow-y-auto min-h-0 -mx-1 px-1" aria-label={t("nav.primary")}>
        <NavHrefsProvider hrefs={NAV_HREFS}>
          <NavGroup label={t("nav.group.authoring")}>
            <NavItem href="/rules" label={t("nav.rules")} icon="rules" />
          </NavGroup>

          <NavGroup label={t("nav.group.runtime")}>
            <NavItem href="/verify" label={t("nav.verify")} icon="verify" />
            <NavItem href="/hitl" label={t("nav.reviewQueue")} icon="hitl" badge={hitlPending} />
          </NavGroup>

          <NavGroup label={t("nav.group.audit")}>
            <NavItem href="/overview" label={t("nav.overview")} icon="overview" />
            <NavItem href="/ledger" label={t("nav.audit")} icon="ledger" />
            <NavItem
              href="/endpoints"
              label={t("nav.endpoints")}
              icon="endpoints"
            />
            <NavItem href="/shared" label={t("nav.shared")} icon="ledger" />
          </NavGroup>

          <NavGroup label={t("nav.group.setup")}>
            <NavItem href="/setup" label={t("setup.title")} icon="setup" />
            <NavItem href="/scripts" label={t("nav.scripts")} icon="setup" />
            <NavItem href="/settings" label={t("nav.settings")} icon="settings" />
          </NavGroup>

          <NavGroup label={t("nav.group.help")}>
            <NavItem href="/docs" label={t("nav.docs")} icon="docs" />
          </NavGroup>
        </NavHrefsProvider>
      </nav>

      <SidebarFooter />
    </div>
  )
}
