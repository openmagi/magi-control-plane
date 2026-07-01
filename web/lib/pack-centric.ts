/**
 * Pack-centric runtime: shared server-side read of the
 * MAGI_CP_PACK_CENTRIC_RUNTIME flag.
 *
 * P5 flipped the default to ON. Unset now means the pack-centric,
 * session-scoped runtime is active. The boot migration has moved every
 * enabled policy into the tenant's floor pack, so the pack-centric
 * surfaces (the /sessions tab, the pack-membership picker, the
 * floor-pack always-on specialization) are the canonical view. An
 * operator only sees the legacy /rules toggle switchboard after an
 * explicit rollback (MAGI_CP_PACK_CENTRIC_RUNTIME=0).
 *
 * Server-only: this reads process.env, so call it from server
 * components / server actions and thread the resulting boolean down to
 * any client components that need it.
 *
 * Only an explicit falsy value ("0", "false", "no", "off", or empty)
 * rolls it back. Mirrors the code-side
 * `magi_cp.config.pack_centric_runtime_enabled()` and the local
 * `_packCentricEnabled()` used by the /rules page (kept there for its
 * existing source-grep test contract).
 */
export function isPackCentricEnabled(): boolean {
  const raw = process.env.MAGI_CP_PACK_CENTRIC_RUNTIME
  if (raw === undefined) return true
  const norm = raw.trim().toLowerCase()
  return !(
    norm === "0" ||
    norm === "false" ||
    norm === "no" ||
    norm === "off" ||
    norm === ""
  )
}
