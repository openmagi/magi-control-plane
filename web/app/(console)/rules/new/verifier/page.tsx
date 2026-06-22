import Link from "next/link"
import { getT } from "@/lib/i18n/server"
import { resolveFlash } from "@/lib/flash"
import {
  Card,
  CardHeader,
  ErrorState,
  Input,
  PageHeader,
  Select,
  SubmitButton,
  Textarea,
} from "@/components/ui"
import { saveCustomVerifier } from "./actions"

export const dynamic = "force-dynamic"

const CATEGORY_OPTIONS = [
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
] as const

/**
 * Custom verifier authoring form. v1 supports regex matching only —
 * step name + display name + category + regex pattern + on-match
 * behavior + optional reasons.
 *
 * Design notes:
 *   - One vertical column with grouped sections (decision per section),
 *     mirroring the Toss-style rhythm we use elsewhere without forcing
 *     a multi-step wizard for what is really a single declarative form.
 *   - Server action validates duplicate-of-backend (fast feedback +
 *     defense-in-depth); the backend remains the authority.
 *   - No client-side regex preview yet — that lands in a follow-up
 *     once the form shape stabilises.
 */
export default async function NewCustomVerifierPage({
  searchParams,
}: { searchParams: { err?: string; msg?: string } }) {
  const { t } = await getT()
  const flash = resolveFlash(searchParams.msg, searchParams.err)
  const errKey = searchParams.err
  const localErr =
    errKey === "bad_regex"   ? t("customVerifier.error.badRegex") :
    errKey === "bad_step"    ? t("customVerifier.error.badStep") :
    errKey === "required"    ? t("customVerifier.error.required") :
    null

  return (
    <>
      <PageHeader
        title={t("customVerifier.title")}
        description={t("customVerifier.kind.regexHelper")}
        actions={
          <Link
            href="/rules"
            className="text-xs font-medium text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:underline"
          >
            {t("customVerifier.back")}
          </Link>
        }
      />

      {localErr && (
        <ErrorState title={localErr} severity="error" />
      )}
      {flash?.kind === "error" && !localErr && (
        <ErrorState title={flash.text} severity="error" />
      )}

      <form action={saveCustomVerifier} className="space-y-5 max-w-2xl">
        <Card>
          <CardHeader
            title={t("customVerifier.field.step")}
            subtitle={t("customVerifier.field.stepHint")}
          />
          <Input
            id="cv-step"
            name="step"
            required
            placeholder="custom_secret_leak"
            pattern="^[a-z][a-z0-9_]{0,63}$"
          />
        </Card>

        <Card>
          <CardHeader title={t("customVerifier.field.name")} />
          <Input
            id="cv-name"
            name="name"
            required
            placeholder=""
            maxLength={128}
          />
        </Card>

        <Card>
          <CardHeader title={t("customVerifier.field.category")} />
          <Select
            id="cv-category"
            name="category"
            required
            defaultValue="SECURITY"
            options={CATEGORY_OPTIONS.map((c) => ({
              value: c,
              label: t(`presets.category.${c}` as never),
            }))}
          />
        </Card>

        <Card>
          <CardHeader title={t("customVerifier.field.description")} />
          <Textarea
            id="cv-description"
            name="description"
            rows={2}
            maxLength={1024}
          />
        </Card>

        <Card>
          <CardHeader
            title={t("customVerifier.field.pattern")}
            subtitle={t("customVerifier.field.patternHint")}
          />
          <Input
            id="cv-pattern"
            name="pattern"
            required
            placeholder="\\bSECRET_LEAK\\b"
            maxLength={1024}
            className="font-mono text-[12.5px]"
          />
        </Card>

        <Card>
          <CardHeader title={t("customVerifier.field.onMatch")} />
          <fieldset className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <label className="flex items-start gap-2 rounded-xl border border-black/[0.08] bg-white p-3 cursor-pointer hover:border-[var(--color-accent)] has-[:checked]:border-[var(--color-accent)] has-[:checked]:bg-[var(--color-accent)]/[0.04]">
              <input
                type="radio"
                name="on_match"
                value="deny"
                defaultChecked
                className="mt-1 accent-[var(--color-accent)]"
              />
              <span className="text-sm font-medium text-[var(--color-text-primary)]">
                {t("customVerifier.field.onMatchDeny")}
              </span>
            </label>
            <label className="flex items-start gap-2 rounded-xl border border-black/[0.08] bg-white p-3 cursor-pointer hover:border-[var(--color-accent)] has-[:checked]:border-[var(--color-accent)] has-[:checked]:bg-[var(--color-accent)]/[0.04]">
              <input
                type="radio"
                name="on_match"
                value="review"
                className="mt-1 accent-[var(--color-accent)]"
              />
              <span className="text-sm font-medium text-[var(--color-text-primary)]">
                {t("customVerifier.field.onMatchReview")}
              </span>
            </label>
          </fieldset>
        </Card>

        <Card>
          <CardHeader
            title={t("customVerifier.field.reasons")}
            subtitle={t("customVerifier.field.reasonsHint")}
          />
          <Textarea
            id="cv-reasons"
            name="reasons"
            rows={3}
            placeholder="forbidden keyword"
          />
        </Card>

        <Card>
          <CardHeader title={t("customVerifier.field.enabled")} />
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              name="enabled"
              value="true"
              defaultChecked
              className="h-4 w-4 rounded border-black/[0.18] accent-[var(--color-accent)] cursor-pointer"
            />
            <span className="text-sm text-[var(--color-text-secondary)]">
              {t("customVerifier.field.enabledHelper")}
            </span>
          </label>
        </Card>

        <div className="flex items-center gap-3 pt-2">
          <SubmitButton
            label={t("customVerifier.save")}
            pendingLabel={t("customVerifier.saving")}
            className="primary"
          />
          <Link
            href="/rules"
            className="text-xs font-medium text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] px-3 py-2"
          >
            {t("common.cancel")}
          </Link>
        </div>
      </form>
    </>
  )
}
