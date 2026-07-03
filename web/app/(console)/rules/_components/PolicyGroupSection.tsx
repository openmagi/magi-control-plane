import type { PolicyGroupItem } from "@/lib/cloud"
import { Badge, Button, Card } from "@/components/ui"

import { deletePolicyGroupAction, togglePolicyGroupAction } from "../actions"

/** pack -> policy -> rule: the policy-management surface.
 *
 * A user authors one intent that may own several rules; managing N loose rules
 * is painful. This section lists authored POLICIES (only the multi-rule ones,
 * where grouping actually helps), each with its member count, a policy-level
 * enable/disable (cascades to all rules), and a delete (cascades). Single-rule
 * policies stay in the per-rule grid below, unchanged. */
export function PolicyGroupSection({ groups }: { groups: PolicyGroupItem[] }) {
  // Only surface policies that own more than one rule; a one-rule policy is
  // already well represented by its single card below.
  const multi = groups.filter((g) => g.rule_ids.length > 1)
  if (multi.length === 0) return null

  return (
    <div className="space-y-2">
      <div className="text-sm font-semibold">Policies ({multi.length})</div>
      <p className="text-xs text-[var(--color-text-tertiary)]">
        One authored policy, implemented by several rules. Enable/disable or delete acts on the whole policy.
      </p>
      <div className="space-y-2">
        {multi.map((g) => (
          <Card key={g.id} className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-sm font-medium">{g.id}</span>
                {g.kind === "compound" ? <Badge variant="default">compound</Badge> : null}
                <Badge variant={g.enabled ? "default" : "review"}>{g.enabled ? "enabled" : "disabled"}</Badge>
                {g.mixed ? <Badge variant="deny">mixed</Badge> : null}
                {g.missing_rules && g.missing_rules.length > 0
                  ? <Badge variant="deny">{g.missing_rules.length} missing</Badge> : null}
              </div>
              {g.description ? (
                <div className="text-sm text-[var(--color-text-secondary)] mt-1">{g.description}</div>
              ) : null}
              <div className="text-xs text-[var(--color-text-tertiary)] mt-1">
                {g.rule_ids.length} rules: <span className="font-mono">{g.rule_ids.join(", ")}</span>
              </div>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <form action={togglePolicyGroupAction}>
                <input type="hidden" name="id" value={g.id} />
                <input type="hidden" name="enabled" value={g.enabled ? "false" : "true"} />
                <Button type="submit" variant="ghost" size="sm">{g.enabled ? "Disable" : "Enable"}</Button>
              </form>
              <form action={deletePolicyGroupAction}>
                <input type="hidden" name="id" value={g.id} />
                <Button type="submit" variant="ghost" size="sm">Delete</Button>
              </form>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
