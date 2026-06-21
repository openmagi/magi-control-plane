import { getT } from "@/lib/i18n/server"
import { Sidebar } from "./_components/Sidebar"
import { SidebarClient } from "./_components/SidebarClient"

/**
 * Console shell: sidebar + content.
 *
 * Desktop (≥md): sticky sidebar column on the left, content fluid on
 * the right.
 *
 * Mobile (<md): SidebarClient renders the sticky mobile header (with
 * hamburger) at the top and a slide-in drawer containing the Sidebar.
 * Content fills the full width below the header.
 */
export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const { t } = await getT()
  return (
    <div className="md:flex md:min-h-screen">
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
        className="flex-1 min-w-0 px-5 md:px-8 py-6 outline-none"
      >
        <div className="mx-auto" style={{ maxWidth: "var(--content-max)" }}>
          {children}
        </div>
      </main>
    </div>
  )
}
