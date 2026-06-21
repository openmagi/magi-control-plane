import type { Metadata } from "next"
import Link from "next/link"
import { getT } from "@/lib/i18n/server"
import { Badge, Card, CardHeader, CodeBlock, PageHeader } from "@/components/ui"

export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "Install · magi-control-plane",
  description:
    "5분 설치 가이드. Clawy Pro+ 구독자는 자동 키 발급, 자체 호스팅은 본인 인스턴스 사용.",
  openGraph: { title: "Install · magi-control-plane", type: "website" },
  alternates: { canonical: "/install" },
  robots: { index: true, follow: true },
}

export default async function InstallPage() {
  const { t, locale } = await getT()
  const siteUrl =
    process.env.MAGI_CP_PUBLIC_SITE_URL || "https://cloud.openmagi.ai"
  const isKo = locale === "ko"

  return (
    <>
      <PageHeader title={t("install.title")} description={t("install.subtitle")} />

      <Card className="mb-4 border-[var(--color-border-focus)]">
        <div className="flex items-center gap-2 mb-3">
          <Badge variant="ok">{isKo ? "권장" : "Recommended"}</Badge>
          <div className="text-md font-medium">{t("install.oneLiner.title")}</div>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)] mb-4 max-w-3xl">
          {t("install.oneLiner.body")}
        </p>
        <CodeBlock maxHeight="auto">{
`curl -fsSL ${siteUrl}/install.sh \\
  | bash -s -- mcp_YOUR_KEY`
        }</CodeBlock>
      </Card>

      <Card className="mb-4">
        <CardHeader title={t("install.what.title")} />
        <ol className="list-decimal pl-6 space-y-2 text-sm text-[var(--color-text-secondary)]">
          <li>{t("install.what.1")}</li>
          <li>{t("install.what.2")}</li>
          <li>{t("install.what.3")}</li>
          <li>{t("install.what.4")}</li>
          <li>{t("install.what.5")}</li>
        </ol>
      </Card>

      <div className="grid gap-3 md:grid-cols-2 mb-4">
        <Card>
          <CardHeader
            title={t("install.proPlus.title")}
            subtitle={<Badge variant="info">{isKo ? "구독에 포함" : "Bundled"}</Badge>}
          />
          <p className="text-sm text-[var(--color-text-secondary)] mb-3">
            {t("install.proPlus.body")}
          </p>
          <a href="https://clawy.pro/pricing" target="_blank" rel="noopener noreferrer"
             className="text-sm text-[var(--color-accent)] hover:underline">
            {isKo ? "Pro+ 구독 →" : "Subscribe to Pro+ →"}
          </a>
        </Card>

        <Card>
          <CardHeader
            title={t("install.selfHost.title")}
            subtitle={<Badge variant="muted">{isKo ? "무료" : "Free"}</Badge>}
          />
          <p className="text-sm text-[var(--color-text-secondary)] mb-3">
            {t("install.selfHost.body")}
          </p>
          <a href="https://github.com/openmagi/magi-control-plane" target="_blank" rel="noopener noreferrer"
             className="text-sm text-[var(--color-accent)] hover:underline">
            {isKo ? "GitHub 에서 보기 →" : "View on GitHub →"}
          </a>
        </Card>
      </div>

      <p className="text-xs text-[var(--color-text-tertiary)]">
        <a href="https://github.com/openmagi/magi-control-plane/blob/main/docs/install.md"
           target="_blank" rel="noopener noreferrer"
           className="hover:text-[var(--color-text-secondary)] hover:underline">
          {t("install.docs")} →
        </a>
      </p>
    </>
  )
}
