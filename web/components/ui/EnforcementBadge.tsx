/**
 * EnforcementBadge — shared rendering of the policy enforcement label.
 *
 * P8 fix-cycle #5 + #6: the rules list (web/app/(console)/rules/page.tsx)
 * and the policy detail page
 * (web/app/(console)/policies/[...id]/page.tsx) used to inline-branch on
 * the legacy enforcement vocabulary (`"deterministic-gate"`,
 * `"observe-only"`, `"missing"`). Post-P8 the cloud also emits
 * `"enforcing"` and `"preview"` (`policy.step_enforcement` resolver), and
 * the fix-cycle adds `"unresolved-legacy"` for pre-P8 rows whose step
 * ref no longer resolves against the live registry. Both inline-ternary
 * call sites fell through to the default Badge variant for any
 * post-rename value — a silent UX regression. Centralising the branch
 * here means a future cloud-side vocabulary change is a single edit, and
 * the union type in `web/lib/cloud.ts::EnforcementLabel` makes drift a
 * `tsc --noEmit` failure.
 */
import type { EnforcementLabel } from "@/lib/cloud"
import { Badge, type BadgeVariant } from "./Badge"

interface EnforcementBadgeProps {
  kind: string
}

function variantFor(kind: string): BadgeVariant {
  // Post-P8 vocabulary
  if (kind === "enforcing") return "ok"
  if (kind === "preview") return "review"
  if (kind === "unresolved-legacy") return "deny"
  // Legacy (pre-P8) vocabulary kept for /catalog/evidence-types rows
  // and back-compat fall-throughs on pre-P8 on-disk policies.
  if (kind === "deterministic-gate") return "ok"
  if (kind === "observe-only") return "review"
  if (kind === "log-only") return "muted"
  if (kind === "missing") return "deny"
  return "default"
}

export function EnforcementBadge({ kind }: EnforcementBadgeProps) {
  return <Badge variant={variantFor(kind)}>{kind}</Badge>
}

// Re-export for callers that prefer to keep the union name local.
export type { EnforcementLabel }
