/**
 * Q97b — Settings page.
 *
 * Self-host operators paste ANTHROPIC_API_KEY / OPENAI_API_KEY here
 * instead of editing ~/.magi-cp/.env + recreating containers. The
 * page server-fetches the current status (set + last4 only — the
 * raw key is never on the wire) and hands it to the client form for
 * the password inputs + status pills.
 *
 * Authoring lives on a client component, not a server action chain,
 * because the form needs optimistic state (status pill flips green
 * the moment a successful test resolves) and a Test button that
 * does NOT submit the form. Persistence still goes through a
 * server action so the admin key never reaches the browser.
 */
import { cloud, CloudConfigError, type LlmKeysStatus } from "@/lib/cloud"
import { getT } from "@/lib/i18n/server"
import { ErrorState, PageHeader } from "@/components/ui"
import { LlmKeysForm } from "./_components/LlmKeysForm"

export const dynamic = "force-dynamic"

function errMsg(e: unknown): string {
  if (e instanceof CloudConfigError) return "cloud not configured"
  return e instanceof Error ? e.message : String(e)
}

export default async function SettingsPage() {
  const { t, locale } = await getT()

  let initial: LlmKeysStatus | null = null
  let err: string | null = null
  try {
    initial = await cloud.getLlmKeys()
  } catch (e) {
    err = errMsg(e)
  }

  return (
    <>
      <PageHeader
        title={t("settings.title")}
        description={t("settings.subtitle")}
      />

      {err && (
        <ErrorState
          status={t("common.cloudUnreachable")}
          title={err}
        />
      )}

      {!err && initial && (
        <LlmKeysForm locale={locale} initialStatus={initial} />
      )}
    </>
  )
}
