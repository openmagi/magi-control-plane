"use server"

import { revalidatePath } from "next/cache"
import { redirect } from "next/navigation"
import { cloud, type CustomVerifierUpsertReq } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"

const STEP_RE = /^[a-z][a-z0-9_]{0,63}$/
const VALID_CATEGORIES = new Set([
  "ANSWER", "FACT", "CODING", "TASK", "OUTPUT",
  "RESEARCH", "MEMORY", "SECURITY",
])
const VALID_ON_MATCH = new Set(["deny", "review"])

/** Server action: parse the wizard form, validate, POST to backend.
 *
 * Validation here mirrors the backend's `CustomVerifierSpec.validate()`
 * — the duplicate is intentional: we want a fast client-visible error
 * on bad input (regex compile, step name) without round-tripping to
 * the cloud. The backend remains the authority on persistence. */
export async function saveCustomVerifier(formData: FormData): Promise<void> {
  const step = String(formData.get("step") ?? "").trim()
  const name = String(formData.get("name") ?? "").trim()
  const category = String(formData.get("category") ?? "").trim()
  const description = String(formData.get("description") ?? "").trim()
  const pattern = String(formData.get("pattern") ?? "")
  const onMatch = String(formData.get("on_match") ?? "deny").trim()
  const reasonsRaw = String(formData.get("reasons") ?? "")
  const enabled = formData.get("enabled") !== "false"

  if (!STEP_RE.test(step)) {
    redirect("/rules/new/verifier?err=bad_step")
  }
  if (!name) {
    redirect("/rules/new/verifier?err=required")
  }
  if (!VALID_CATEGORIES.has(category)) {
    redirect("/rules/new/verifier?err=required")
  }
  if (!VALID_ON_MATCH.has(onMatch)) {
    redirect("/rules/new/verifier?err=required")
  }
  if (!pattern || pattern.length > 1024) {
    redirect("/rules/new/verifier?err=required")
  }
  try {
    // RegExp.compile() coverage parity with Python re — both error on
    // unbalanced parens / bad escapes. Differences (e.g. lookbehind
    // syntax) are caught by the backend re-validation.
    new RegExp(pattern)
  } catch {
    redirect("/rules/new/verifier?err=bad_regex")
  }

  const reasons = reasonsRaw
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 8)

  const spec: CustomVerifierUpsertReq = {
    step,
    name,
    category: category as CustomVerifierUpsertReq["category"],
    description,
    kind: "regex",
    config: {
      pattern,
      on_match: onMatch as "deny" | "review",
      reasons,
    },
    enabled,
  }

  try {
    await cloud.upsertCustomVerifier(spec)
  } catch (e: unknown) {
    redirect(`/rules/new/verifier?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect("/rules?msg=saved")
}
