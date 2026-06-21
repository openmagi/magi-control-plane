import Link from "next/link"
import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { cloud } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Button, Card, CodeBlock, CopyButton, ErrorState,
  Input, PageHeader, SubmitButton,
} from "@/components/ui"

export const dynamic = "force-dynamic"

const SETUP_COOKIE = "magi-cp-setup-key"

async function verifyKey(formData: FormData): Promise<void> {
  "use server"
  const key = String(formData.get("apiKey") ?? "").trim()
  if (!key) redirect("/setup?err=required")
  try {
    await cloud.getMyTenant(key)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg.includes("401") || msg.includes("403")) {
      redirect("/setup?err=invalid")
    }
    redirect("/setup?err=cloud")
  }
  const { cookies } = await import("next/headers")
  cookies().set({
    name: SETUP_COOKIE,
    value: key,
    path: "/setup",
    httpOnly: true,
    sameSite: "lax",
    maxAge: 60 * 60 * 24,
  })
  revalidatePath("/setup")
  redirect("/setup?msg=verified")
}

async function clearKey(): Promise<void> {
  "use server"
  const { cookies } = await import("next/headers")
  cookies().delete(SETUP_COOKIE)
  redirect("/setup")
}

async function readKeyCookie(): Promise<string | null> {
  const { cookies } = await import("next/headers")
  return cookies().get(SETUP_COOKIE)?.value ?? null
}

export default async function SetupPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string } }) {
  const { t } = await getT()
  const storedKey = await readKeyCookie()

  let tenant: Awaited<ReturnType<typeof cloud.getMyTenant>> | null = null
  if (storedKey) {
    try { tenant = await cloud.getMyTenant(storedKey) }
    catch { tenant = null }
  }

  const errCard = (() => {
    if (searchParams.err === "required") {
      return <ErrorState status="invalid" title={t("setup.error.invalidKey")} severity="warning" />
    }
    if (searchParams.err === "invalid") {
      return <ErrorState status="401" title={t("setup.error.invalidKey")} severity="warning" />
    }
    if (searchParams.err === "cloud") {
      return <ErrorState status={t("common.cloudUnreachable")} title={t("setup.error.cloud")} />
    }
    return null
  })()

  return (
    <>
      <PageHeader
        title={t("setup.title")}
        description={t("setup.subtitle")}
      />

      {errCard}

      {!tenant && (
        <form action={verifyKey} className="grid gap-3 max-w-xl mb-6">
          <Input
            name="apiKey"
            type="password"
            required
            autoComplete="off"
            spellCheck={false}
            label={t("setup.field.apiKey")}
            helper={t("setup.field.apiKeyHelper")}
            placeholder="mcp_…"
          />
          <div>
            <SubmitButton
              label={t("setup.verify")}
              pendingLabel={t("setup.verify.pending")}
            />
          </div>
        </form>
      )}

      {tenant && (
        <>
          <Card tone="status" className="mb-6 flex flex-wrap items-center gap-3">
            <div className="text-sm">
              {t("setup.tenant.label")}: <code translate="no" className="font-mono">{tenant.id}</code>
            </div>
            <Badge variant={tenant.status === "active" ? "ok" : "deny"}>
              {tenant.status === "active"
                ? t("setup.tenant.statusActive")
                : t("setup.tenant.statusSuspended")}
            </Badge>
            <Badge variant="info">{t("setup.tenant.plan")}: {tenant.plan}</Badge>
            <form action={clearKey} className="ml-auto">
              <Button type="submit" size="sm" variant="ghost">
                {t("common.cancel")}
              </Button>
            </form>
          </Card>

          {/* Recommended path — single curl|bash command */}
          <Card className="mb-3 border-[var(--color-border-focus)]">
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="ok">{t("setup.quickstart.recommended")}</Badge>
              <div className="text-sm font-medium">{t("setup.quickstart.title")}</div>
            </div>
            <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
              {t("setup.quickstart.body")}
            </p>
            <CodeBlock maxHeight="auto">{
`curl -fsSL ${process.env.MAGI_CP_PUBLIC_CLOUD_URL || "https://cloud.openmagi.ai"}/install.sh \\
  | bash -s -- ${storedKey ?? "mcp_YOUR_KEY"}`
            }</CodeBlock>
            <p className="text-xs text-[var(--color-text-tertiary)] mt-3">
              {t("setup.quickstart.detail")}
            </p>
          </Card>

          <details className="mb-3">
            <summary className="cursor-pointer text-sm text-[var(--color-text-secondary)] mb-3">
              {t("setup.manual.toggle")}
            </summary>

          <h2 className="text-md font-semibold mb-3 mt-4">{t("setup.steps.title")}</h2>

          {/* Step 1 — cloud URL */}
          <Card className="mb-3">
            <div className="text-sm font-medium mb-1">{t("setup.step1.title")}</div>
            <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
              {t("setup.step1.body")}
            </p>
            <CodeBlock maxHeight="auto">{
`export MAGI_CP_CLOUD_URL=https://cloud.openmagi.ai
export MAGI_CP_API_KEY=${storedKey ?? "mcp_…"}`
            }</CodeBlock>
          </Card>

          {/* Step 2 — install gate + plugin */}
          <Card className="mb-3">
            <div className="text-sm font-medium mb-1">{t("setup.step2.title")}</div>
            <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
              {t("setup.step2.body")}
            </p>
            <div className="flex flex-wrap gap-2 mb-3">
              <a href="/api/downloads/managed-settings" download>
                <Button variant="primary" size="sm">
                  {t("setup.download.managedSettings")}
                </Button>
              </a>
              <a href="/api/downloads/gate-binary" download>
                <Button variant="secondary" size="sm">
                  {t("setup.download.gateBinary")}
                </Button>
              </a>
            </div>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {t("setup.download.helper")}
            </p>
            <CodeBlock maxHeight="auto" className="mt-3">{
`# macOS
mkdir -p "$HOME/Library/Application Support/ClaudeCode"
mv ~/Downloads/managed-settings.json \\
   "$HOME/Library/Application Support/ClaudeCode/managed-settings.json"
sudo mv ~/Downloads/magi-gate.sh /usr/local/bin/magi-gate.sh
sudo chmod +x /usr/local/bin/magi-gate.sh`
            }</CodeBlock>
          </Card>

          {/* Step 3 — restart */}
          <Card className="mb-3">
            <div className="text-sm font-medium mb-1">{t("setup.step3.title")}</div>
            <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
              {t("setup.step3.body")}
            </p>
            <CodeBlock maxHeight="auto">{
`# Quit then relaunch Claude Code, then in a new bash session:
echo FILE_COURT_M1_D1`
            }</CodeBlock>
          </Card>

          {/* Step 4 — try */}
          <Card>
            <div className="text-sm font-medium mb-1">{t("setup.step4.title")}</div>
            <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
              {t("setup.step4.body")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Link href="/policies/compile">
                <Button variant="primary" size="sm">{t("setup.tryCompile")}</Button>
              </Link>
              <Link href="/verify">
                <Button variant="secondary" size="sm">{t("setup.tryVerify")}</Button>
              </Link>
              <Link href="/overview">
                <Button variant="ghost" size="sm">{t("setup.tryDashboard")}</Button>
              </Link>
            </div>
          </Card>
          </details>
        </>
      )}
    </>
  )
}
