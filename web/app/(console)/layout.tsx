import { Suspense } from "react"
import { getT } from "@/lib/i18n/server"
import { Sidebar } from "./_components/Sidebar"
import { SidebarClient } from "./_components/SidebarClient"
import { RuntimeHeader } from "./_components/RuntimeHeader"

/**
 * Console shell. Two-column layout mirrored from magi-agent's
 * DashboardLayout: bg gradient (via globals.css body), sticky sidebar
 * w-72, sticky RuntimeHeader, main content padding.
 *
 * Mobile (<md): SidebarClient renders the sticky mobile header with
 * hamburger, hides the sidebar off-screen, slides it in on tap.
 */
export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const { t } = await getT()
  return (
    <div className="flex min-h-screen bg-[var(--surface-console)]">
      <SidebarClient
        openMenuLabel={t("nav.openMenu")}
        closeMenuLabel={t("nav.closeMenu")}
        brandLabel={t("nav.brand")}
      >
        <Sidebar />
      </SidebarClient>
      <main
        id="main-content"
        tabIndex={-1}
        className="flex-1 min-w-0 outline-none"
      >
        <Suspense>
          <RuntimeHeader />
        </Suspense>
        <div className="min-w-0 px-4 py-5 sm:px-6 md:px-8 md:py-7">
          <div className="mx-auto" style={{ maxWidth: "var(--content-max)" }}>
            {children}
          </div>
        </div>
      </main>
    </div>
  )
}
