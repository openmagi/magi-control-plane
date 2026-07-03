import { redirect } from "next/navigation"
import { cloud } from "@/lib/cloud"
import {
  signSession,
  CONSOLE_COOKIE,
  CONSOLE_MAX_AGE_S,
} from "@/lib/dashboard-auth"
import {
  Button,
  Card,
  ErrorState,
  Input,
  PageHeader,
  SubmitButton,
} from "@/components/ui"

export const dynamic = "force-dynamic"

/** Only same-site relative paths are honored, to avoid an open redirect. */
function safeFrom(from: string): string {
  return from.startsWith("/") && !from.startsWith("//") ? from : "/overview"
}

// magi-cp is self-host, single-operator software. Any valid tenant API key
// unlocks the operator console BY DESIGN (the operator IS the tenant); this is
// not a multi-tenant privilege boundary. The console must never be exposed as
// a shared multi-user surface. See docs/operator.md "Dashboard exposure".
async function login(formData: FormData): Promise<void> {
  "use server"
  const key = String(formData.get("apiKey") ?? "").trim()
  const from = safeFrom(String(formData.get("from") ?? "/overview"))
  if (!key) redirect("/login?err=required")

  let tenant: Awaited<ReturnType<typeof cloud.getMyTenant>> | undefined
  try {
    tenant = await cloud.getMyTenant(key)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg.includes("401") || msg.includes("403")) redirect("/login?err=invalid")
    redirect("/login?err=cloud")
  }
  if (!tenant) redirect("/login?err=cloud")

  const token = await signSession(tenant.id)
  if (!token) redirect("/login?err=nosecret")

  const { cookies, headers } = await import("next/headers")
  const isHttps = (headers().get("x-forwarded-proto") ?? "").includes("https")
  cookies().set({
    name: CONSOLE_COOKIE,
    value: token,
    path: "/",
    httpOnly: true,
    sameSite: "lax",
    secure: isHttps,
    maxAge: CONSOLE_MAX_AGE_S,
  })
  redirect(from)
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: { err?: string; from?: string }
}) {
  const from = safeFrom(searchParams.from ?? "/overview")

  const errCard = (() => {
    if (searchParams.err === "required")
      return <ErrorState status="invalid" title="Enter your tenant API key to continue." severity="warning" />
    if (searchParams.err === "invalid")
      return <ErrorState status="401" title="That API key was not accepted." severity="warning" />
    if (searchParams.err === "nosecret")
      return (
        <ErrorState
          status="config"
          title="Console sessions are not configured. Set MAGI_CP_DASHBOARD_SESSION_SECRET (or MAGI_CP_ADMIN_HMAC_SECRET) on the dashboard server and restart."
        />
      )
    if (searchParams.err === "cloud")
      return <ErrorState status="unreachable" title="Could not reach the control plane to verify the key." />
    return null
  })()

  return (
    <>
      <PageHeader
        title="Sign in"
        description="This dashboard is meant for localhost. When reached over a network it requires your tenant API key."
      />
      {errCard}
      <form action={login} className="grid gap-3 max-w-xl mt-4">
        <input type="hidden" name="from" value={from} />
        <Input
          name="apiKey"
          type="password"
          required
          autoComplete="off"
          spellCheck={false}
          label="Tenant API key"
          helper="Starts with mcp_. The same key the CLI and gate use."
          placeholder="mcp_…"
        />
        <div>
          <SubmitButton label="Sign in" pendingLabel="Verifying…" />
        </div>
      </form>
      <Card className="mt-6">
        <div className="text-xs text-[var(--color-text-tertiary)]">
          A localhost request opens the console without signing in. This page
          appears because sign-in is required for this deployment, either
          because you set <code>MAGI_CP_TRUST_LOOPBACK_HEADER=0</code> or the
          request did not arrive with a loopback Host. Sign in with your tenant
          API key; if you front the console with a reverse proxy, enforce auth
          at the proxy too.
        </div>
        <div className="mt-2">
          <a href="/install">
            <Button variant="ghost" size="sm">
              Installation guide
            </Button>
          </a>
        </div>
      </Card>
    </>
  )
}
