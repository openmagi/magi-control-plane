import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import Link from "next/link"
import { cloud } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import {
  Button, Card, ErrorState, Input, PageHeader, SubmitButton, Textarea,
} from "@/components/ui"

export const dynamic = "force-dynamic"

async function submitSignup(formData: FormData): Promise<void> {
  "use server"
  const email = String(formData.get("email") ?? "").trim()
  if (!email) {
    redirect("/signup?err=required")
  }
  const payload = {
    email,
    firm: String(formData.get("firm") ?? "").trim(),
    role: String(formData.get("role") ?? "").trim(),
    use_case: String(formData.get("use_case") ?? "").trim(),
    referrer: String(formData.get("referrer") ?? "").trim(),
  }
  try {
    await cloud.signup(payload)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg.includes("429")) {
      redirect("/signup?err=rate_limit")
    }
    redirect("/signup?err=cloud")
  }
  revalidatePath("/signup")
  redirect("/signup?msg=ok")
}

export default async function SignupPage({
  searchParams,
}: { searchParams: { msg?: string; err?: string } }) {
  const { t } = await getT()

  if (searchParams.msg === "ok") {
    return (
      <>
        <PageHeader title={t("signup.success.title")} />
        <Card tone="status" className="space-y-3">
          <p className="text-sm">{t("signup.success.body")}</p>
          <div className="flex gap-2">
            <Link href="/">
              <Button variant="secondary">{t("setup.tryDashboard")}</Button>
            </Link>
            <Link href="/setup">
              <Button variant="primary">{t("setup.title")}</Button>
            </Link>
          </div>
        </Card>
      </>
    )
  }

  const errCard = (() => {
    if (searchParams.err === "required") {
      return <ErrorState status="invalid" title={t("signup.error.required")} severity="warning" />
    }
    if (searchParams.err === "rate_limit") {
      return <ErrorState status="429" title={t("signup.error.rateLimit")} severity="warning" />
    }
    if (searchParams.err === "cloud") {
      return <ErrorState status={t("common.cloudUnreachable")} title={t("signup.error.cloud")} />
    }
    return null
  })()

  return (
    <>
      <PageHeader title={t("signup.title")} description={t("signup.subtitle")} />
      {errCard}
      <form action={submitSignup} className="grid gap-4 max-w-2xl">
        <Input
          name="email"
          type="email"
          required
          autoComplete="email"
          inputMode="email"
          spellCheck={false}
          label={t("signup.field.email")}
          placeholder={t("signup.field.emailPlaceholder")}
        />
        <Input
          name="firm"
          type="text"
          autoComplete="organization"
          spellCheck={false}
          label={t("signup.field.firm")}
          placeholder={t("signup.field.firmPlaceholder")}
        />
        <Input
          name="role"
          type="text"
          autoComplete="organization-title"
          label={t("signup.field.role")}
          placeholder={t("signup.field.rolePlaceholder")}
        />
        <Textarea
          name="use_case"
          rows={3}
          spellCheck={false}
          autoComplete="off"
          label={t("signup.field.useCase")}
          placeholder={t("signup.field.useCasePlaceholder")}
        />
        <Input
          name="referrer"
          type="text"
          autoComplete="off"
          label={t("signup.field.referrer")}
          placeholder={t("signup.field.referrerPlaceholder")}
        />
        <div className="pt-2">
          <SubmitButton
            label={t("signup.submit")}
            pendingLabel={t("signup.submit.pending")}
          />
        </div>
      </form>
    </>
  )
}
