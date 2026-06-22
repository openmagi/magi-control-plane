"use server"

import { cookies } from "next/headers"
import { redirect } from "next/navigation"
import { revalidatePath } from "next/cache"
import { cloud } from "@/lib/cloud"
import { codeForError } from "@/lib/flash"
import { validatePolicyId } from "@/lib/policy-id"

const PRESET_COOKIE = "magi-cp-presets-disabled"
const PRESET_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

/** Built-in / preview verifiers carry a single global cookie of operator-
 * disabled IDs. Custom verifiers are toggled at the backend instead.
 * Returning the *disabled* set keeps the cookie short (most are enabled). */
export async function readDisabledVerifierIds(): Promise<Set<string>> {
  const store = await cookies()
  const raw = store.get(PRESET_COOKIE)?.value ?? ""
  return new Set(raw.split(",").map((s) => s.trim()).filter(Boolean))
}

export async function toggleBuiltinVerifierAction(verifierId: string): Promise<void> {
  if (!/^[A-Za-z0-9_\-:.]{1,80}$/.test(verifierId)) {
    throw new Error("invalid verifier id")
  }
  const store = await cookies()
  const raw = store.get(PRESET_COOKIE)?.value ?? ""
  const set = new Set(raw.split(",").map((s) => s.trim()).filter(Boolean))
  if (set.has(verifierId)) set.delete(verifierId)
  else set.add(verifierId)
  store.set(PRESET_COOKIE, [...set].join(","), {
    httpOnly: false,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: PRESET_COOKIE_MAX_AGE,
    path: "/",
  })
  revalidatePath("/rules")
}

export async function toggleCustomVerifierAction(formData: FormData): Promise<void> {
  const step = String(formData.get("step") ?? "")
  const enabled = formData.get("enabled") === "true"
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(step)) {
    redirect(`/rules?err=invalid_step`)
  }
  try {
    await cloud.setCustomVerifierEnabled(step, enabled)
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?msg=toggled`)
}

export async function deleteCustomVerifierAction(formData: FormData): Promise<void> {
  const step = String(formData.get("step") ?? "")
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(step)) {
    redirect(`/rules?err=invalid_step`)
  }
  try {
    await cloud.deleteCustomVerifier(step)
  } catch (e: unknown) {
    redirect(`/rules?err=${codeForError(e)}`)
  }
  revalidatePath("/rules")
  redirect(`/rules?msg=deleted`)
}

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
  redirect(`/rules?msg=toggled`)
}
