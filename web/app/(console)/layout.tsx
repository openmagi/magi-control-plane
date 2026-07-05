import { Suspense } from "react"
import { getT } from "@/lib/i18n/server"
import { isPackCentricEnabled } from "@/lib/pack-centric"
import { Sidebar } from "./_components/Sidebar"
import { SidebarClient } from "./_components/SidebarClient"
import { RuntimeHeader } from "./_components/RuntimeHeader"
import { CommandPalette, type Command } from "./_components/CommandPalette"
import { getWorkspaceData } from "./_data/workspace"

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
  // Cached fetch (same key as the sidebar), so this is a cache hit: gate the
  // palette's destinations exactly like the nav so it never offers a route
  // that is hidden in this mode.
  const { tenant } = await getWorkspaceData()
  const selfHost = tenant?.synthetic ?? true
  const packCentric = isPackCentricEnabled()

  const goto = (label: string): string => `${t("command.goPrefix")} · ${label}`
  const commands: Command[] = [
    { id: "new-policy", label: t("command.newPolicy"), hint: t("command.action"), href: "/policies/new", keywords: "new create policy rule authoring 정책" },
    { id: "new-evidence-gate", label: t("command.newEvidenceGate"), hint: t("command.action"), href: "/policies/new/evidence-gate", keywords: "new create evidence gate 증거 게이트" },
    { id: "go-rules", label: goto(t("nav.rules")), hint: t("command.goto"), href: "/rules", keywords: "rules policies packs 룰 정책" },
    ...(packCentric ? [{ id: "go-sessions", label: goto(t("nav.sessions")), hint: t("command.goto"), href: "/sessions", keywords: "sessions 세션" }] : []),
    { id: "go-verify", label: goto(t("nav.verify")), hint: t("command.goto"), href: "/verify", keywords: "verify 검증" },
    { id: "go-hitl", label: goto(t("nav.reviewQueue")), hint: t("command.goto"), href: "/hitl", keywords: "hitl review queue approve 리뷰 대기열" },
    { id: "go-overview", label: goto(t("nav.overview")), hint: t("command.goto"), href: "/overview", keywords: "overview dashboard kpi 개요" },
    { id: "go-ledger", label: goto(t("nav.audit")), hint: t("command.goto"), href: "/ledger", keywords: "ledger audit chain 감사 원장" },
    ...(!selfHost ? [
      { id: "go-endpoints", label: goto(t("nav.endpoints")), hint: t("command.goto"), href: "/endpoints", keywords: "endpoints" },
      { id: "go-shared", label: goto(t("nav.shared")), hint: t("command.goto"), href: "/shared", keywords: "shared runs" },
    ] : []),
    { id: "go-scripts", label: goto(t("nav.scripts")), hint: t("command.goto"), href: "/scripts", keywords: "scripts 스크립트" },
    { id: "go-settings", label: goto(t("nav.settings")), hint: t("command.goto"), href: "/settings", keywords: "settings config keys 설정" },
    { id: "go-docs", label: goto(t("nav.docs")), hint: t("command.goto"), href: "/docs", keywords: "docs help 문서" },
  ]

  return (
    <div className="flex min-h-screen bg-[var(--surface-console)]"><CommandPalette commands={commands} placeholder={t("command.placeholder")} emptyLabel={t("command.empty")} />
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
