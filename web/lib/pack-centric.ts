/**
 * P4 (pack-centric runtime): shared server-side read of the
 * MAGI_CP_PACK_CENTRIC_RUNTIME flag.
 *
 * Default OFF preserves the legacy per-policy `enabled` path — the 47
 * live policy ids keep firing on their enabled bit regardless of pack
 * membership or session activation. Every pack-centric-only surface
 * (the /sessions tab, the pack-membership picker, the floor-pack
 * always-on specialization) gates its render behind this so an operator
 * on the legacy runtime is never shown a governance model that is not
 * yet in effect.
 *
 * Server-only: this reads process.env, so call it from server
 * components / server actions and thread the resulting boolean down to
 * any client components that need it.
 *
 * Truthy string values ("1", "true", "yes", "on") flip it on. Mirrors
 * the local `_packCentricEnabled()` used by the /rules page (kept there
 * for its existing source-grep test contract).
 */
export function isPackCentricEnabled(): boolean {
  const raw = (process.env.MAGI_CP_PACK_CENTRIC_RUNTIME || "").trim().toLowerCase()
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on"
}
