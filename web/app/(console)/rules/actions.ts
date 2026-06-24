"use server"

import { redirect } from "next/navigation"
import { revalidatePath } from "next/cache"
import { cloud } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"

/** Toggle a stored policy's enabled flag. The only mutating action on
 * /rules. pure-derivation pivot retired the per-verifier toggle. */
export async function togglePolicyAction(formData: FormData): Promise<void> {
  let id: string
  try {
    id = validatePolicyId(formData.get("id"))
  } catch {
    redirect("/rules?err=invalid_id")
  }
  const enabled = formData.get("enabled") === "true"
  try {
    await cloud.setEnabled(id, enabled)
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?tab=policies&msg=toggled`)
}

/** D60: enable/disable a prebuilt template directly from the toggle
 * on the prebuilt card. Splits the URL-side path between
 * `cloud.enablePrebuilt` and `cloud.disablePrebuilt` rather than
 * re-using `setEnabled` because the dashboard `enabled=true` may
 * need to MATERIALIZE the policy into the store (first-time enable),
 * which `PATCH /policies/{id}/enabled` does not do. The cloud's
 * idempotent enable handles both first-time enable and re-enable. */
export async function togglePrebuiltAction(formData: FormData): Promise<void> {
  const rawId = formData.get("id")
  if (typeof rawId !== "string" || !rawId.startsWith("prebuilt/")) {
    redirect("/rules?err=invalid_id")
  }
  const id = rawId as string
  const enabled = formData.get("enabled") === "true"
  try {
    if (enabled) {
      await cloud.enablePrebuilt(id)
    } else {
      await cloud.disablePrebuilt(id)
    }
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?tab=policies&msg=toggled`)
}
