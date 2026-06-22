"use server"

import { redirect } from "next/navigation"
import { revalidatePath } from "next/cache"
import { cloud } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"

/** Toggle a stored policy's enabled flag. The only mutating action on
 * /rules — pure-derivation pivot retired the per-verifier toggle. */
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
