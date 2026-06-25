import { Suspense } from "react"
import { getT } from "@/lib/i18n/server"
import { Sidebar } from "../(console)/_components/Sidebar"
import { SidebarClient } from "../(console)/_components/SidebarClient"
import { RuntimeHeader } from "../(console)/_components/RuntimeHeader"

/**
 * D78: docs route group. We reuse the console Sidebar + RuntimeHeader
 * so /docs/* renders inside the same console shell (no "leaving the
 * app" feeling). The docs left rail (DocsLayout) sits inside the main
 * content column, not as a second app sidebar.
 */
export default async function DocsLayout({ children }: { children: React.ReactNode }) {
  const { t } = await getT()
  return (
    <div className="flex min-h-screen">
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
