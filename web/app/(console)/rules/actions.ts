"use server"

import { redirect } from "next/navigation"
import { revalidatePath } from "next/cache"
import { cloud } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"

/** D75: enable / disable a policy pack as a single cascading action.
 *
 * Pack id must match `pack/<slug>` or `user-pack/<slug>`. The server
 * action normalises the request, calls the right cloud verb based on
 * the `enabled` form field, and revalidates `/rules` so the next
 * paint reflects the cascade result.
 *
 * Per-member errors come back inside the response envelope (the brief
 * commits partial success). The dashboard surfaces them on the pack
 * card when present. */
const _PACK_ID_RE = /^(pack|user-pack)\/[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$/

function _validatePackId(raw: FormDataEntryValue | null): string {
  if (typeof raw !== "string" || !_PACK_ID_RE.test(raw) || raw.length > 200) {
    throw new Error("invalid_pack_id")
  }
  return raw
}

export async function togglePackAction(formData: FormData): Promise<void> {
  let id: string
  try {
    id = _validatePackId(formData.get("id"))
  } catch {
    redirect("/rules?err=invalid_id")
  }
  const enabled = formData.get("enabled") === "true"
  // D75 follow-up: the cloud cascade reports per-member outcomes in
  // `results[]`. The original action threw the array away and only
  // distinguished success vs. transport failure, so a partial cascade
  // (one un-enableable member among several) flashed the generic
  // `toggled` banner with no signal which member failed or why.
  // Inspect `results[]` and route to a distinct flash code on
  // partial-success or all-failed, logging the failed member ids to
  // server stderr so an operator hitting the issue has a forensic
  // trail.
  let result
  try {
    if (enabled) {
      result = await cloud.enablePack(id)
    } else {
      result = await cloud.disablePack(id)
    }
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  const failed = result.results.filter(
    (r) => r.ok === false,
  )
  const succeeded = result.results.filter(
    (r) => r.ok === true && r.skipped !== true,
  )
  revalidatePath("/rules")
  if (failed.length > 0) {
    const ids = failed.map((r) => r.id).join(", ")
    console.error(
      `rules: pack cascade ${id} ${enabled ? "enable" : "disable"} `
        + `partial — failed members: ${ids}`,
    )
    // All members failed → distinct error flash. Mixed success/failure
    // → the "partial success" OK flash (banner is visible but tone is
    // warning-ish).
    if (succeeded.length === 0) {
      redirect(`/rules?tab=packs&err=pack_partial_failure`)
    }
    redirect(`/rules?tab=packs&msg=pack_partial_success`)
  }
  redirect(`/rules?tab=packs&msg=toggled`)
}

/** D75: create a user pack from the New Pack page. The cloud derives
 * the slug from `name` when `slug` is omitted. Redirects to /rules on
 * success so the new card renders inline; redirects to the new page
 * with an error code on failure (422 / 409 / transport). */
export async function createPackAction(formData: FormData): Promise<void> {
  const name = String(formData.get("name") || "").trim()
  const description = String(formData.get("description") || "").trim()
  // policy_ids[] checkbox group on the picker.
  const policyIds = formData.getAll("policy_ids").map(String).filter(Boolean)
  if (!name) {
    redirect("/policy-packs/new?err=name_required")
  }
  try {
    await cloud.createPack({ name, description, policy_ids: policyIds })
  } catch (e: unknown) {
    redirect(`/policy-packs/new?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?tab=packs&msg=pack_created`)
}

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

/** pack -> policy -> rule: toggle an authored policy (cascades to its rules). */
export async function togglePolicyGroupAction(formData: FormData): Promise<void> {
  let id: string
  try {
    id = validatePolicyId(formData.get("id"))
  } catch {
    redirect("/rules?err=invalid_id")
  }
  const enabled = formData.get("enabled") === "true"
  try {
    await cloud.togglePolicyGroup(id, enabled)
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?tab=policies&msg=toggled`)
}

/** Delete an authored policy and cascade to the rules it owns. */
export async function deletePolicyGroupAction(formData: FormData): Promise<void> {
  let id: string
  try {
    id = validatePolicyId(formData.get("id"))
  } catch {
    redirect("/rules?err=invalid_id")
  }
  try {
    await cloud.deletePolicyGroup(id)
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?tab=policies&msg=deleted`)
}

/** D60: enable/disable a prebuilt template directly from the toggle
 * on the prebuilt card. Splits the URL-side path between
 * `cloud.enablePrebuilt` and `cloud.disablePrebuilt` rather than
 * re-using `setEnabled` because the dashboard `enabled=true` may
 * need to MATERIALIZE the policy into the store (first-time enable),
 * which `PATCH /policies/{id}/enabled` does not do. The cloud's
 * idempotent enable handles both first-time enable and re-enable.
 *
 * D60 follow-up: defense-in-depth on the id. The original revision
 * only checked `startsWith("prebuilt/")`, which lets `prebuilt/`
 * (empty slug), `prebuilt/foo bar` (whitespace), and overlong inputs
 * reach the cloud. Run `validatePolicyId` (same regex togglePolicy
 * uses) and reject empty-slug + overlong cases before the cloud is
 * touched. The cloud-side 404 stays the authoritative check on a
 * well-formed-but-unknown slug. */
export async function togglePrebuiltAction(formData: FormData): Promise<void> {
  const rawId = formData.get("id")
  if (
    typeof rawId !== "string"
    || !rawId.startsWith("prebuilt/")
    || rawId === "prebuilt/"
    || rawId.length > 200
  ) {
    redirect("/rules?err=invalid_id")
  }
  let id: string
  try {
    // POLICY_ID_RE allows `/`, so a well-formed `prebuilt/<slug>`
    // sails through. A whitespace/control-char/path-traversal id
    // throws `invalid_id` and we redirect with the same flash.
    id = validatePolicyId(rawId)
  } catch {
    redirect("/rules?err=invalid_id")
  }
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
