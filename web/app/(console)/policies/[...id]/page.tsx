import Link from "next/link"
import { cloud, type PolicyDetail, type CompiledManagedSettings } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"
import { getT } from "@/lib/i18n/server"
import {
  Badge, Card, Code, CodeBlock, CopyButton, EnforcementBadge,
  ErrorState, PageHeader,
} from "@/components/ui"

export const dynamic = "force-dynamic"

export default async function PolicyDetailPage({
  params,
}: { params: { id: string[] } }) {
  const { t } = await getT()
  const raw = params.id.join("/")
  let id: string
  try { id = validatePolicyId(raw) }
  catch {
    return (
      <>
        <p className="mb-3">
          <Link href="/policies" className="text-sm">{t("newPolicy.back")}</Link>
        </p>
        <ErrorState
          status={t("newPolicy.invalidId")}
          title={t("newPolicy.invalidId")}
        />
      </>
    )
  }

  let detail: PolicyDetail | null = null
  let compiled: CompiledManagedSettings | null = null
  let errCode: string | null = null
  try {
    [detail, compiled] = await Promise.all([
      cloud.getPolicy(id),
      cloud.getCompiled(id),
    ])
  } catch (e: unknown) { errCode = codeForError(e) }

  if (errCode === "not_found") {
    return (
      <>
        <p className="mb-3">
          <Link href="/policies" className="text-sm">{t("newPolicy.back")}</Link>
        </p>
        <ErrorState
          status={t("policies.notFound")}
          title={t("policies.notFound")}
          body={<Code>{id}</Code>}
        />
      </>
    )
  }
  if (errCode || !detail || !compiled) {
    return (
      <>
        <p className="mb-3">
          <Link href="/policies" className="text-sm">{t("newPolicy.back")}</Link>
        </p>
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      </>
    )
  }

  const irJson = JSON.stringify(detail.policy, null, 2)
  const msJson = JSON.stringify(compiled.managed_settings, null, 2)
  const shaShort = compiled.sha256.slice(0, 16)
  const shaMismatch = detail.compiled_sha256 !== compiled.sha256

  return (
    <>
      <p className="mb-3">
        <Link href="/policies" className="text-sm">{t("newPolicy.back")}</Link>
      </p>
      <PageHeader title={<Code className="text-md">{detail.id}</Code>} />

      <Card className="mb-6">
        <div className="flex flex-wrap gap-x-6 gap-y-3 items-center">
          <span className="text-sm">
            {t("policies.source")}: <Code>{detail.source}</Code>
          </span>
          <span className="text-sm flex items-center gap-2">
            {t("policies.enabled")}:
            {detail.enabled
              ? <Badge variant="ok">{t("policies.enabled")}</Badge>
              : <Badge variant="deny">{t("policies.disabled")}</Badge>}
          </span>
          <span className="text-sm flex items-center gap-2">
            enforcement:
            <EnforcementBadge kind={detail.enforcement} />
          </span>
          <details className="text-sm">
            <summary className="cursor-pointer text-[var(--color-text-tertiary)]">
              compiled sha: <Code>{shaShort}…</Code>
            </summary>
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <Code className="break-all">{compiled.sha256}</Code>
              <CopyButton value={compiled.sha256} size="sm" variant="ghost" />
            </div>
          </details>
        </div>
        {shaMismatch && (
          <div className="mt-3">
            <Badge variant="deny">sha mismatch</Badge>
          </div>
        )}
      </Card>

      <div
        className="grid gap-4"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))" }}
      >
        <section>
          <h2 className="text-md font-semibold mt-0 mb-2 flex items-center justify-between">
            <span>Policy IR</span>
            <CopyButton value={irJson} size="sm" variant="ghost" />
          </h2>
          <p className="text-xs text-[var(--color-text-tertiary)] mb-2">
            What an operator authors. The compiler (right) turns it into the
            managed-settings JSON Claude Code consumes.
          </p>
          <CodeBlock maxHeight="60vh">{irJson}</CodeBlock>
        </section>
        <section>
          <h2 className="text-md font-semibold mt-0 mb-2 flex items-center justify-between">
            <span>Compiled managed-settings.json</span>
            <CopyButton value={msJson} size="sm" variant="ghost" />
          </h2>
          <p className="text-xs text-[var(--color-text-tertiary)] mb-2">
            Deterministic compile. Same IR ⇒ same byte output ⇒ same sha256.
          </p>
          <CodeBlock maxHeight="60vh">{msJson}</CodeBlock>
        </section>
      </div>
    </>
  )
}
