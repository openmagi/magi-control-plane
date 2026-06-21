import { redirect } from "next/navigation"

/**
 * Root entry: send anonymous visitors to /welcome (marketing).
 *
 * Operators bookmark /overview directly for the KPI dashboard. The old
 * "/ = dashboard" mapping is intentionally retired in v2.2 — see
 * docs/plans/2026-06-21-dashboard-console-shell.md §1.
 */
export default function Root() {
  redirect("/welcome")
}
