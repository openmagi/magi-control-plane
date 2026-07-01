import type { PolicyPackEntry } from "@/lib/cloud"
import { ErrorState } from "@/components/ui"
import { PackSection } from "./PackSection"

type TFunc = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/**
 * D82a: Packs gets its own top-level tab.
 *
 * Pre-D82a the PackSection rendered on the Policies tab above the
 * prebuilt list. The operator review on the first hands-on install
 * flagged the dual-list framing as confusing because packs are a
 * separate intent layer (a bundle of policies) — clicking a pack
 * toggle has different semantics than clicking a per-policy toggle.
 *
 * Promoting Packs to its own tab gives the bundle concept its own
 * surface and lets the Policies tab focus on individual prebuilt rows
 * + user-authored policies.
 *
 * The body itself is just PackSection rendered standalone, plus the
 * cloud-error banner. Same content, different home.
 */
export function PacksTab({
  items, err, t, packCentric = false,
}: {
  items: PolicyPackEntry[]
  err: string | null
  t: TFunc
  /** P4 legacy-guard: only under the pack-centric runtime does the
   *  floor pack render as a server-locked ALWAYS-ON pack (no toggle).
   *  With the flag off it renders like any other pack. */
  packCentric?: boolean
}) {
  return (
    <section>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
        {t("rules.tab.packs.hint")}
      </p>
      {err && (
        <ErrorState
          title={t("common.cloudUnreachable")}
          body={t("common.seeServerLogs")}
        />
      )}
      <PackSection items={items} t={t} packCentric={packCentric} />
    </section>
  )
}
