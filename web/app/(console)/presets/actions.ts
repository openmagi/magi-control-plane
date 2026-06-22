"use server"

import { cookies } from "next/headers"
import { revalidatePath } from "next/cache"

const COOKIE_NAME = "magi-cp-presets-disabled"
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365 // 1 year

/**
 * Cookie schema: comma-separated preset IDs that the operator has
 * explicitly *disabled*. We store the disabled set (not the enabled
 * set) because almost all presets are enabled by default — a much
 * shorter list to persist.
 *
 * Phase 1 of the toggle UX: cookie-only, no backend wiring. The
 * policy compiler does not yet read this set. Phase 2 will add the
 * preset_overrides table + endpoint and replace the cookie with a
 * server-persisted source of truth.
 */

export async function readDisabledPresetIds(): Promise<Set<string>> {
  const store = await cookies()
  const raw = store.get(COOKIE_NAME)?.value ?? ""
  return new Set(raw.split(",").map(s => s.trim()).filter(Boolean))
}

export async function togglePresetAction(presetId: string): Promise<void> {
  // Whitelist guard: don't trust an arbitrary string into the cookie.
  if (!/^[A-Za-z0-9_\-:.]{1,80}$/.test(presetId)) {
    throw new Error("invalid preset id")
  }
  const store = await cookies()
  const raw = store.get(COOKIE_NAME)?.value ?? ""
  const set = new Set(raw.split(",").map(s => s.trim()).filter(Boolean))
  if (set.has(presetId)) {
    set.delete(presetId)
  } else {
    set.add(presetId)
  }
  store.set(COOKIE_NAME, [...set].join(","), {
    httpOnly: false,         // visible to client for hydration mismatch detection
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: COOKIE_MAX_AGE,
    path: "/",
  })
  revalidatePath("/presets")
}
