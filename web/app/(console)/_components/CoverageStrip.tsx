import type { TKey } from "@/lib/i18n/dict"
import type { CoverageCell, PackCoverage } from "@/lib/cloud"
import { Badge } from "@/components/ui"

type TFunc = (k: TKey, v?: Record<string, string | number>) => string

/**
 * P4 (Codex runtime adapter): per-policy + per-pack coverage rendering.
 *
 * A single mapping from the normalized coverage cell (see
 * src/magi_cp/runtime/trait.py `coverage_cell`) onto the design-system
 * Badge variant + the i18n label. Green enforced / amber downgraded /
 * red unsupported / gray n-a — matching Section 7.2 of the design doc.
 */
const CELL_VARIANT: Record<CoverageCell, "ok" | "review" | "deny" | "muted"> = {
  enforced: "ok",
  downgraded: "review",
  unsupported: "deny",
  not_applicable: "muted",
}

const CELL_LABEL: Record<CoverageCell, TKey> = {
  enforced: "policy.coverage.enforced",
  downgraded: "policy.coverage.downgraded",
  unsupported: "policy.coverage.unsupported",
  not_applicable: "policy.coverage.not_applicable",
}

export function coverageBadgeVariant(cell: CoverageCell) {
  return CELL_VARIANT[cell]
}

/**
 * Per-policy coverage strip on a policy card.
 *
 * Claude Code is the reference runtime: every policy is enforced, so its
 * cell is always green. The Codex cell only renders when the tenant has
 * the Codex runtime enabled (MAGI_CP_CODEX_RUNTIME_ENABLED + picker on),
 * so a CC-only tenant sees exactly one green chip and no Codex noise.
 */
export function CoverageStrip({
  t, codexEnabled, codexCell,
}: {
  t: TFunc
  codexEnabled: boolean
  /** The Codex cell for THIS policy, from GET
   *  /policies/{id}/coverage/codex. Undefined falls back to n/a. */
  codexCell?: CoverageCell
}) {
  const codex = codexCell ?? "not_applicable"
  return (
    <div
      data-testid="coverage-strip"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]"
    >
      <span className="uppercase tracking-wider font-semibold">
        {t("policy.coverage.title")}
      </span>
      <span className="inline-flex items-center gap-1">
        {t("runtime.name.claude-code")}
        <Badge variant="ok">{t("policy.coverage.enforced")}</Badge>
      </span>
      {codexEnabled && (
        <span className="inline-flex items-center gap-1">
          {t("runtime.name.codex")}
          <Badge variant={CELL_VARIANT[codex]}>{t(CELL_LABEL[codex])}</Badge>
        </span>
      )}
    </div>
  )
}

/**
 * Per-pack Codex coverage rollup on a pack card.
 *
 * Renders "Codex coverage: 12 enforced, 3 downgraded, 0 unsupported"
 * from GET /packs/{id}/coverage/codex. Only rendered when Codex is
 * enabled for the tenant (the caller passes `coverage: null` otherwise,
 * so the row disappears entirely on a CC-only tenant).
 */
export function PackCoverageRollup({
  t, coverage,
}: {
  t: TFunc
  coverage: PackCoverage | null
}) {
  if (!coverage) return null
  return (
    <p
      data-testid="pack-coverage-rollup"
      className="text-[11px] text-[var(--color-text-tertiary)]"
    >
      {t("pack.coverage.rollup", {
        enforced: coverage.enforced,
        downgraded: coverage.downgraded,
        unsupported: coverage.unsupported,
      })}
    </p>
  )
}
