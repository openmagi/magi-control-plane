import Link from "next/link"
import { getT } from "@/lib/i18n/server"
import { cloud, CloudConfigError } from "@/lib/cloud"
import { NavGroup } from "./NavGroup"
import { NavItem } from "./NavItem"
import { WorkspaceCard } from "./WorkspaceCard"
import { SidebarFooter } from "./SidebarFooter"

const CLOUD_HOST = process.env.MAGI_CP_PUBLIC_CLOUD_URL
  ? new URL(process.env.MAGI_CP_PUBLIC_CLOUD_URL).host
  : process.env.MAGI_CP_CLOUD_URL
    ? new URL(process.env.MAGI_CP_CLOUD_URL).host
    : "cloud.openmagi.ai"

/**
 * Fetches workspace context in parallel. Failures degrade gracefully.
 * Real cache wrapping (unstable_cache + revalidateTag) lands in D4.
 */
async function loadSidebarData() {
  const apiKey = process.env.MAGI_CP_API_KEY
  const [tenant, healthOk, hitlPending] = await Promise.all([
    apiKey ? cloud.getMyTenant(apiKey).catch(() => null) : Promise.resolve(null),
    fetch(
      `${process.env.MAGI_CP_CLOUD_URL ?? "http://127.0.0.1:8787"}/healthz`,
      { cache: "no-store", signal: AbortSignal.timeout(2000) },
    ).then(r => r.ok).catch(() => false),
    cloud.listHitl().then(l => l.length).catch((e: unknown) => {
      if (e instanceof CloudConfigError) return 0
      return 0
    }),
  ])
  return { tenant, healthOk, hitlPending }
}

/**
 * Console sidebar content. Returns the inner column only — the wrapping
 * <aside> element + responsive positioning lives in SidebarClient, so
 * the server fetch happens once and the client wrapper can flip the
 * presentation between desktop sticky column and mobile slide-in drawer
 * without re-rendering the content.
 */
export async function Sidebar() {
  const { t } = await getT()
  const { tenant, healthOk, hitlPending } = await loadSidebarData()

  return (
    <div className="flex flex-col h-full">
      <Link
        href="/overview"
        className="flex items-center gap-2 px-4 h-[var(--header-height)] border-b border-[var(--color-border-subtle)] text-[var(--color-text-primary)] hover:no-underline shrink-0"
      >
        <span aria-hidden="true" className="inline-block w-5 h-5 rounded-sm bg-[var(--color-accent)]" />
        <span className="font-medium">magi-control-plane</span>
      </Link>

      <WorkspaceCard
        tenantId={tenant?.synthetic ? null : (tenant?.id ?? null)}
        plan={tenant?.plan ?? null}
        healthOk={healthOk}
        host={CLOUD_HOST}
      />

      <nav className="px-3 flex-1 overflow-y-auto" aria-label={t("nav.primary")}>
        <NavGroup label={t("nav.group.authoring")}>
          <NavItem href="/policies" label={t("nav.policies")} icon="policies" />
          <NavItem href="/policies/compile" label={t("nav.compile")} icon="compile" />
          <NavItem href="/presets" label={t("nav.presets")} icon="presets" />
        </NavGroup>

        <NavGroup label={t("nav.group.runtime")}>
          <NavItem href="/verify" label={t("nav.verify")} icon="verify" />
          <NavItem href="/hitl" label={t("nav.reviewQueue")} icon="hitl" badge={hitlPending} />
        </NavGroup>

        <NavGroup label={t("nav.group.audit")}>
          <NavItem href="/overview" label={t("nav.overview")} icon="overview" />
          <NavItem href="/ledger" label={t("nav.audit")} icon="ledger" />
        </NavGroup>

        <NavGroup label={t("nav.group.setup")}>
          <NavItem href="/setup" label={t("setup.title")} icon="setup" />
        </NavGroup>
      </nav>

      <SidebarFooter />
    </div>
  )
}
