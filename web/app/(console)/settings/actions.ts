"use server"

/**
 * Q97b — server actions for /settings.
 *
 * Both actions wrap the same-origin proxy at /api/settings/llm-keys
 * (and .../test). They live as server actions so the LlmKeysForm
 * client component can `<form action={saveLlmKeysAction}>` without
 * holding the admin key on the client side. The proxy strips
 * client-side auth headers and signs the upstream call with the
 * admin key read from the server-only env.
 *
 * Return shape is the normalised LlmKeysStatus envelope on save
 * success, plus a discriminator field so the form can render a toast
 * vs an inline error. Test action returns a per-provider map so the
 * form can render two pills in one round-trip.
 */

import { revalidatePath } from "next/cache"
import { cloud, type LlmKeysStatus, type LlmKeysTestSingle } from "@/lib/cloud"

export type SaveResult =
  | { ok: true; status: LlmKeysStatus }
  | { ok: false; error: string }

export type TestResult = {
  anthropic: LlmKeysTestSingle | null
  openai: LlmKeysTestSingle | null
  error: string | null
}

const MAX_KEY_LEN = 4_096

function trimOrNull(v: FormDataEntryValue | null): string | null | undefined {
  // undefined  = field absent → preserve existing value
  // null       = sentinel: clear (saved as empty string)
  // string     = overwrite
  if (v === null) return undefined
  const s = typeof v === "string" ? v.trim() : ""
  return s
}

/**
 * Persist the LLM provider keys. `formData` carries:
 *   - `anthropic_api_key` (optional string; empty = clear)
 *   - `openai_api_key`    (optional string; empty = clear)
 *   - `anthropic_clear`   (optional "1"; explicit clear instead of preserve)
 *   - `openai_clear`      (optional "1"; explicit clear instead of preserve)
 *
 * The clear flag exists because an empty string from an HTML password
 * input is ambiguous (operator may have left it blank intentionally
 * OR be re-rendering the form after a successful save). The form
 * defaults to "preserve" when the input is left blank and surfaces
 * a dedicated checkbox for the clear path.
 */
export async function saveLlmKeysAction(formData: FormData): Promise<SaveResult> {
  const aRaw = trimOrNull(formData.get("anthropic_api_key"))
  const oRaw = trimOrNull(formData.get("openai_api_key"))
  const aClear = formData.get("anthropic_clear") === "1"
  const oClear = formData.get("openai_clear") === "1"

  const req: {
    anthropic_api_key?: string | null
    openai_api_key?: string | null
  } = {}

  if (aClear) {
    req.anthropic_api_key = ""
  } else if (typeof aRaw === "string" && aRaw.length > 0) {
    if (aRaw.length > MAX_KEY_LEN) {
      return { ok: false, error: "anthropic_api_key too long" }
    }
    req.anthropic_api_key = aRaw
  }

  if (oClear) {
    req.openai_api_key = ""
  } else if (typeof oRaw === "string" && oRaw.length > 0) {
    if (oRaw.length > MAX_KEY_LEN) {
      return { ok: false, error: "openai_api_key too long" }
    }
    req.openai_api_key = oRaw
  }

  if (Object.keys(req).length === 0) {
    // Operator clicked Save without entering or clearing anything.
    // Return the current status so the form re-renders without a
    // confusing "saved" toast.
    try {
      const status = await cloud.getLlmKeys()
      return { ok: true, status }
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) }
    }
  }

  try {
    const status = await cloud.putLlmKeys(req)
    revalidatePath("/settings")
    return { ok: true, status }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * Run one "ping" completion per provider against the live cloud
 * singletons (uses the just-saved keys via the in-place rebuild).
 * Returns a per-provider result so the form can render two pills.
 */
/**
 * P4 (Codex runtime adapter): switch the tenant's runtime.
 *
 * The cloud refuses "codex" unless MAGI_CP_CODEX_RUNTIME_ENABLED is set
 * (403 → surfaced as an inline error), and persists tenants.runtime_id
 * otherwise. Single-tenant-beta tenant id is "default" (the same synthetic
 * tenant the rest of the self-host dashboard reads). The admin key stays
 * server-side — the client component only sees the result envelope.
 */
export type RuntimeSwitchResult =
  | { ok: true; runtimeId: string }
  | { ok: false; error: string }

export async function setRuntimeAction(
  runtimeId: string,
): Promise<RuntimeSwitchResult> {
  if (runtimeId !== "claude-code" && runtimeId !== "codex") {
    return { ok: false, error: "unknown runtime" }
  }
  try {
    const r = await cloud.setTenantRuntime("default", runtimeId)
    revalidatePath("/settings")
    return { ok: true, runtimeId: r.runtime_id }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

export async function testConnectionAction(): Promise<TestResult> {
  try {
    const r = await cloud.testLlmKeys()
    // shape from cloud is the map when provider was omitted
    if (r && typeof r === "object" && "anthropic" in r && "openai" in r) {
      const map = r as { anthropic: LlmKeysTestSingle; openai: LlmKeysTestSingle }
      return { anthropic: map.anthropic, openai: map.openai, error: null }
    }
    // Defensive: cloud surprised us with a single-provider envelope.
    // Surface as a top-level error so the form shows a banner instead
    // of two empty pills.
    return {
      anthropic: null, openai: null,
      error: "unexpected cloud response shape",
    }
  } catch (e) {
    return {
      anthropic: null, openai: null,
      error: e instanceof Error ? e.message : String(e),
    }
  }
}
